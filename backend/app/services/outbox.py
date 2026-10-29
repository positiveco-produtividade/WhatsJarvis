"""Fila de saida transacional (chat.outbound_messages): enfileirar e despachar.

`enqueue` roda na transacao do chamador: o efeito externo (envio pela Meta) so acontece
depois do COMMIT, por `dispatch` (spec 4.4.7); a recuperacao de travados fica em PF-30.
Nunca registra telefone nem corpo em log (so ids).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.core.values import (
    Decision,
    DiscardReason,
    OutboundKind,
    OutboundStatus,
    ReplyKind,
    UserStatus,
)
from app.core.values import SendResult as TraceSendResult
from app.services import alerts, trace
from app.services.channel import Channel, ReplyButton, SendResult, TemplateMessage
from app.services.flags import Flags
from app.services.replies import notice_buttons
from app.services.reply_policy import AllowAllPolicy, ReplyPolicy

logger = logging.getLogger(__name__)

MAX_SEND_ATTEMPTS = 2  # a 2a so se a requisicao comprovadamente nao saiu (spec 4.4.7)

# Status que o usuario PRECISA ter na hora do envio (AC-23). A recusa e enviada depois
# que o status ja virou `declined` (spec 4.4.3: mesma transacao do toque em "Nao aceito").
REQUIRED_STATUS: Mapping[OutboundKind, UserStatus] = {
    OutboundKind.WELCOME_TEMPLATE: UserStatus.PENDING_OPTIN,
    OutboundKind.OPTIN_REMINDER: UserStatus.PENDING_OPTIN,
    OutboundKind.OPTIN_DECLINED: UserStatus.DECLINED,
    OutboundKind.OPTIN_CONFIRMATION: UserStatus.ACTIVE,
    OutboundKind.UNDER_CONSTRUCTION: UserStatus.ACTIVE,
    OutboundKind.RATE_LIMIT_NOTICE: UserStatus.ACTIVE,
    OutboundKind.MEDIA_ACK: UserStatus.ACTIVE,
    OutboundKind.OPT_OUT_CONFIRMATION: UserStatus.ACTIVE,
    OutboundKind.BRAIN_ANSWER: UserStatus.ACTIVE,
    OutboundKind.BRAIN_ANSWER_RATE: UserStatus.ACTIVE,
    OutboundKind.BRAIN_NOTICE: UserStatus.ACTIVE,
}

# Limites da Meta para mensagem interativa de botoes (verificacao-externa.md).
MAX_BUTTONS = 3
MAX_BUTTON_TITLE = 20
MAX_BUTTON_ID = 256
MAX_INTERACTIVE_BODY = 1024

_INSERT = (
    "INSERT INTO chat.outbound_messages "
    "(user_id, to_phone, kind, required_status, body, template, inbound_trace_id, "
    "optin_request_id, status, attempts, next_attempt_at, created_at, reply_buttons) "
    "VALUES ($1::bigint, $2::text, $3::text, $4::text, $5::text, $6::jsonb, $7::bigint, "
    "$8::bigint, $9::text, 0, $10, $10, $11::jsonb) RETURNING id"
)


class OutboundTarget(Protocol):
    """Destinatario: o que `enqueue` precisa do usuario (qualquer UserRow serve)."""

    @property
    def id(self) -> int: ...

    @property
    def phone_e164(self) -> str: ...


def parse_kind(kind: OutboundKind | str) -> OutboundKind:
    """Valida o `kind` contra core/values; `InvalidValue` se nao existe."""
    try:
        return OutboundKind(kind)
    except ValueError:
        raise InvalidValue("kind", "tipo de saida nao aceito") from None


def required_status_for(kind: OutboundKind | str) -> UserStatus:
    """Status exigido no envio para o `kind`."""
    return REQUIRED_STATUS[parse_kind(kind)]


async def enqueue(
    conn: Any,
    user: OutboundTarget,
    kind: OutboundKind | str,
    *,
    body: str | None = None,
    template: Mapping[str, Any] | None = None,
    inbound_trace_id: int | None = None,
    optin_request_id: int | None = None,
    reply_buttons: Sequence[Mapping[str, str]] | None = None,
    clock: Clock | None = None,
) -> int:
    """Grava uma saida `pending` na transacao do chamador e devolve o id."""
    kind_value = parse_kind(kind)  # valida antes de tocar no banco
    now = (clock or SystemClock()).now()
    row_id: int = await conn.fetchval(
        _INSERT,
        user.id,
        user.phone_e164,
        kind_value.value,
        REQUIRED_STATUS[kind_value].value,
        body,
        None if template is None else json.dumps(template),
        inbound_trace_id,
        optin_request_id,
        OutboundStatus.PENDING.value,
        now,
        None if reply_buttons is None else json.dumps([dict(b) for b in reply_buttons]),
    )
    return row_id


# ---------------------------------------------------------------------------
# Despacho (spec 4.4.7)
# ---------------------------------------------------------------------------

_policy: ReplyPolicy = AllowAllPolicy()

_CLAIM = (
    "UPDATE chat.outbound_messages SET status = 'sending', claimed_at = $2, "
    "attempts = attempts + 1 WHERE id = $1::bigint AND status = 'pending' "
    "RETURNING id, user_id, to_phone, kind, required_status, body, template, "
    "inbound_trace_id, optin_request_id, attempts, reply_buttons"
)
_BUMP_ATTEMPT = "UPDATE chat.outbound_messages SET attempts = attempts + 1 WHERE id = $1::bigint"
_USER_STATUS = "SELECT status FROM access.users WHERE id = $1::bigint"
_FINISH = (
    "UPDATE chat.outbound_messages SET status = $2::text, sent_at = $3, wa_message_id = $4::text, "
    "error_code = $5::text, error_subcode = $6::text, discard_reason = $7::text "
    "WHERE id = $1::bigint"
)
_TRACE_RECEIVED_AT = "SELECT received_at FROM chat.message_trace WHERE id = $1::bigint"


def set_reply_policy(policy: ReplyPolicy) -> None:
    """Define a politica de resposta do processo (R4). Padrao: `AllowAllPolicy`."""
    global _policy
    _policy = policy


@dataclass(frozen=True)
class _Recipient:
    """Usuario como a politica o enxerga: id, telefone de destino e status relido."""

    id: int
    phone_e164: str
    status: str | None


def _template_from(raw: Any) -> TemplateMessage:
    """Monta o `TemplateMessage` do JSON do outbox (listas viram tuplas). Falha alto sem nome."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, Mapping):
        raise InvalidValue("template", "modelo ausente ou invalido")
    name = str(data.get("name") or "").strip()
    if not name:
        raise InvalidValue("template", "nome do modelo vazio (WELCOME_TEMPLATE_NAME)")
    return TemplateMessage(
        name=name,
        language=str(data.get("language") or ""),
        body_params=tuple(str(p) for p in data.get("body_params") or ()),
        quick_reply_payloads=tuple(str(p) for p in data.get("quick_reply_payloads") or ()),
    )


def _buttons_from(raw: Any) -> tuple[ReplyButton, ...]:
    """Botoes do outbox (JSON) validados contra os limites da Meta. Falha alto se invalidos."""
    data = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(data, list) or not 1 <= len(data) <= MAX_BUTTONS:
        raise InvalidValue("reply_buttons", f"a saida exige de 1 a {MAX_BUTTONS} botoes")
    buttons: list[ReplyButton] = []
    for item in data:
        if not isinstance(item, Mapping):
            raise InvalidValue("reply_buttons", "botao invalido")
        button_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        if not button_id or len(button_id) > MAX_BUTTON_ID:
            raise InvalidValue("reply_buttons", "id do botao vazio ou acima do limite")
        if not title or len(title) > MAX_BUTTON_TITLE:
            raise InvalidValue("reply_buttons", "titulo do botao vazio ou acima do limite")
        buttons.append(ReplyButton(id=button_id, title=title))
    return tuple(buttons)


async def _send_once(channel: Channel, row: Any) -> SendResult:
    kind = OutboundKind(row["kind"])
    to: str = row["to_phone"]
    if kind is OutboundKind.WELCOME_TEMPLATE:
        return await channel.send_template(to, _template_from(row["template"]))
    body: str = row["body"]
    if kind is OutboundKind.OPTIN_REMINDER:
        return await channel.send_reply_buttons(to, body, notice_buttons(row["optin_request_id"]))
    if kind is OutboundKind.BRAIN_ANSWER_RATE:
        return await channel.send_reply_buttons(to, body, _buttons_from(row["reply_buttons"]))
    return await channel.send_text(to, body)


def _validate_content(row: Any) -> None:
    """Falha alto se faltar o conteudo do tipo (antes de chamar a Meta)."""
    kind = OutboundKind(row["kind"])
    if kind is OutboundKind.WELCOME_TEMPLATE:
        _template_from(row["template"])
        return
    if not row["body"]:
        raise InvalidValue("body", "texto da saida vazio")
    if kind is OutboundKind.OPTIN_REMINDER and row["optin_request_id"] is None:
        raise InvalidValue("optin_request_id", "lembrete sem aviso aberto")
    if kind is OutboundKind.BRAIN_ANSWER_RATE:
        _buttons_from(row["reply_buttons"])
        if len(row["body"]) > MAX_INTERACTIVE_BODY:
            raise InvalidValue("body", "texto acima do limite da mensagem interativa")


def _retryable(result: SendResult) -> bool:
    return not result.ok and result.transient and not result.ambiguous


async def _send_with_retry(conn: Any, channel: Channel, row: Any) -> SendResult:
    result = await _send_once(channel, row)
    if _retryable(result) and row["attempts"] < MAX_SEND_ATTEMPTS:
        await conn.execute(_BUMP_ATTEMPT, row["id"])
        result = await _send_once(channel, row)
    return result


def _error_text(result: SendResult) -> str:
    parts = [p for p in (result.error_code, result.error_subcode) if p]
    return "/".join(parts) or "unknown_error"


async def _finish(
    conn: Any,
    row: Any,
    status: OutboundStatus,
    *,
    sent_at: datetime | None = None,
    result: SendResult | None = None,
    discard_reason: str | None = None,
) -> None:
    await conn.execute(
        _FINISH,
        row["id"],
        status.value,
        sent_at,
        result.wa_message_id if result is not None and result.ok else None,
        None if result is None else result.error_code,
        None if result is None else result.error_subcode,
        discard_reason,
    )
    event = "outbound_sent" if status is OutboundStatus.SENT else f"outbound_{status.value}"
    logger.info(
        event, extra={"outbound_id": row["id"], "user_id": row["user_id"], "kind": row["kind"]}
    )


async def _latency_ms(conn: Any, trace_id: int, now: datetime) -> int | None:
    received_at: datetime | None = await conn.fetchval(_TRACE_RECEIVED_AT, trace_id)
    if received_at is None:
        return None
    return max(0, int((now - received_at).total_seconds() * 1000))


async def _trace_discarded(conn: Any, row: Any, decision: Decision | None) -> None:
    if row["inbound_trace_id"] is None:
        return
    await trace.record_send_result(
        conn, row["inbound_trace_id"], send_result=TraceSendResult.DISCARDED, decision=decision
    )


async def _discard_if_blocked(
    conn: Any, row: Any, recipient: _Recipient, policy: ReplyPolicy
) -> bool:
    """Descarta se o status mudou (AC-23) ou a politica nega (R4). True = descartado."""
    if recipient.status != row["required_status"]:
        reason = (
            DiscardReason.REVOKED_BEFORE_SEND
            if recipient.status == UserStatus.REVOKED.value
            else DiscardReason.STATUS_CHANGED
        )
        await _finish(conn, row, OutboundStatus.DISCARDED, discard_reason=reason.value)
        await _trace_discarded(conn, row, Decision.REVOKED_BEFORE_SEND)
        return True
    verdict = await policy.allow(conn, recipient, row["kind"], None)
    if not verdict.ok:
        await _finish(conn, row, OutboundStatus.DISCARDED, discard_reason=verdict.reason)
        await _trace_discarded(conn, row, None)
        return True
    return False


async def _record_outcome(conn: Any, row: Any, result: SendResult, now: datetime) -> OutboundStatus:
    """Grava o desfecho do envio na fila e no rastro de origem."""
    trace_id: int | None = row["inbound_trace_id"]
    if result.ok:
        await _finish(conn, row, OutboundStatus.SENT, sent_at=now, result=result)
        if trace_id is not None:
            await trace.record_send_result(
                conn,
                trace_id,
                send_result=TraceSendResult.SENT,
                reply_wa_message_id=result.wa_message_id,
                reply_sent_at=now,
                latency_ms=await _latency_ms(conn, trace_id, now),
            )
        return OutboundStatus.SENT
    if result.ambiguous:
        await _finish(conn, row, OutboundStatus.UNKNOWN, result=result)
        if trace_id is not None:
            await trace.record_send_result(
                conn,
                trace_id,
                send_result=TraceSendResult.UNKNOWN,
                send_error=_error_text(result),
            )
        return OutboundStatus.UNKNOWN
    await _finish(conn, row, OutboundStatus.FAILED, result=result)
    if trace_id is not None:
        await trace.record_send_result(
            conn,
            trace_id,
            send_result=TraceSendResult.FAILED,
            decision=Decision.SEND_FAILED,
            send_error=_error_text(result),
        )
    return OutboundStatus.FAILED


async def _fail_invalid(conn: Any, row: Any, error: InvalidValue) -> None:
    await _finish(
        conn,
        row,
        OutboundStatus.FAILED,
        result=SendResult(ok=False, error_code="invalid_content"),
    )
    if row["inbound_trace_id"] is not None:
        await trace.record_send_result(
            conn,
            row["inbound_trace_id"],
            send_result=TraceSendResult.FAILED,
            decision=Decision.SEND_FAILED,
            send_error=f"invalid_content:{error.field}",
        )


async def dispatch(
    pool: Any,
    outbound_id: int,
    *,
    channel: Channel,
    policy: ReplyPolicy | None = None,
    clock: Clock | None = None,
) -> OutboundStatus | None:
    """Despacha uma saida `pending` (spec 4.4.7); devolve o status final ou None sem claim.

    Claim atomico -> relê o status do usuario no banco -> politica -> envio (ate 2
    tentativas, a 2a so se a requisicao nao saiu) -> atualiza fila e rastro. Conteudo
    invalido (ex.: nome do modelo vazio) marca `failed` e levanta `InvalidValue`. Uma excecao
    inesperada do canal deixa a linha `sending` para a recuperacao (PF-30).
    """
    active_policy = policy or _policy
    time = clock or SystemClock()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(_CLAIM, outbound_id, time.now())
        if row is None:
            return None
        status = await conn.fetchval(_USER_STATUS, row["user_id"])
        recipient = _Recipient(row["user_id"], row["to_phone"], status)
        if await _discard_if_blocked(conn, row, recipient, active_policy):
            return OutboundStatus.DISCARDED
        try:
            _validate_content(row)
        except InvalidValue as error:
            await _fail_invalid(conn, row, error)
            raise
        result = await _send_with_retry(conn, channel, row)
        return await _record_outcome(conn, row, result, time.now())


# ---------------------------------------------------------------------------
# Recuperacao e alerta de falha repetida (spec 4.4.6-4.4.7)
# ---------------------------------------------------------------------------

SENDING_TIMEOUT = timedelta(minutes=2)  # `sending` acima disso vira `unknown` (sem reenviar)
PENDING_REPLY_TIMEOUT = timedelta(minutes=5)  # rastro `pending` acima disso vira `abandoned`
ALERT_DEDUP_WINDOW = timedelta(hours=1)

_STUCK_SENDING = (
    "UPDATE chat.outbound_messages SET status = 'unknown' "
    "WHERE status = 'sending' AND claimed_at < $1 RETURNING id, inbound_trace_id"
)
_ABANDON_TRACES = (
    "UPDATE chat.message_trace SET send_result = $2::text "
    "WHERE reply_kind = $3::text AND send_result IS NULL AND received_at < $1 RETURNING id"
)
_OUTBOUND_FOR_ALERT = "SELECT id, user_id, kind, status FROM chat.outbound_messages WHERE id = $1"
_COUNT_RECENT_FAILURES = (
    "SELECT count(*) FROM chat.outbound_messages WHERE status = 'failed' AND kind <> $2::text "
    "AND COALESCE(claimed_at, created_at) > $1"
)


@dataclass(frozen=True)
class RecoveryResult:
    """Quantas saidas foram para `unknown` e quantos rastros foram abandonados."""

    unknown: int
    abandoned: int


async def recover_stuck(conn: Any, now: datetime) -> RecoveryResult:
    """Recupera travados (4.4.7): `sending` > 2 min -> `unknown` (NAO reenvia) e rastro
    `reply_kind='pending'` > 5 min -> `send_result='abandoned'`. Idempotente.
    """
    stuck = await conn.fetch(_STUCK_SENDING, now - SENDING_TIMEOUT)
    for row in stuck:
        if row["inbound_trace_id"] is not None:
            await trace.record_send_result(
                conn, row["inbound_trace_id"], send_result=TraceSendResult.UNKNOWN
            )
        logger.info("outbound_unknown", extra={"outbound_id": row["id"], "reason": "stuck"})
    abandoned = await conn.fetch(
        _ABANDON_TRACES,
        now - PENDING_REPLY_TIMEOUT,
        TraceSendResult.ABANDONED.value,
        ReplyKind.PENDING.value,
    )
    for row in abandoned:
        logger.info("reply_abandoned", extra={"trace_id": row["id"]})
    return RecoveryResult(unknown=len(stuck), abandoned=len(abandoned))


async def _alert_welcome_failed(
    conn: Any, row: Any, settings: Settings, flags: Flags | None, clock: Clock
) -> int | None:
    return await alerts.raise_alert(
        conn,
        "welcome_failed",
        "high",
        "Falha ao enviar a mensagem de boas-vindas; o usuario segue aguardando o aceite.",
        {"outbound_id": row["id"]},
        user_id=row["user_id"],
        dedup_key=f"welcome_failed:{row['user_id']}",
        dedup_window=ALERT_DEDUP_WINDOW,
        email=True,
        flags=flags,
        settings=settings,
        clock=clock,
    )


async def check_send_failures(
    conn: Any,
    outbound_id: int,
    *,
    settings: Settings,
    flags: Flags | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Alerta apos uma falha definitiva (AC-17, AC-33); devolve o id do alerta ou None.

    Boas-vindas `failed` -> `welcome_failed` (high, dedup 1 h por usuario, com e-mail).
    Demais -> conta falhas dos ultimos SEND_FAILURE_ALERT_WINDOW_MINUTES e, com
    >= SEND_FAILURE_ALERT_THRESHOLD, `send_failure` (high, dedup global 1 h, com e-mail).
    Chamar depois de `dispatch` devolver FAILED ou levantar InvalidValue.
    """
    time = clock or SystemClock()
    row = await conn.fetchrow(_OUTBOUND_FOR_ALERT, outbound_id)
    if row is None or row["status"] != OutboundStatus.FAILED.value:
        return None
    if row["kind"] == OutboundKind.WELCOME_TEMPLATE.value:
        return await _alert_welcome_failed(conn, row, settings, flags, time)
    window = timedelta(minutes=settings.send_failure_alert_window_minutes)
    failures: int = await conn.fetchval(
        _COUNT_RECENT_FAILURES, time.now() - window, OutboundKind.WELCOME_TEMPLATE.value
    )
    if failures < settings.send_failure_alert_threshold:
        return None
    return await alerts.raise_alert(
        conn,
        "send_failure",
        "high",
        "Falhas repetidas no envio de respostas pelo WhatsApp.",
        {"count": failures, "window_minutes": settings.send_failure_alert_window_minutes},
        dedup_key="send_failure",
        dedup_window=ALERT_DEDUP_WINDOW,
        email=True,
        flags=flags,
        settings=settings,
        clock=time,
    )
