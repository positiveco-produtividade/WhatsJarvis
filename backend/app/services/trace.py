"""Rastro por mensagem (chat.message_trace): idempotencia (AC-7) e resultado do envio.

`begin` e o primeiro comando da transacao da mensagem; `finalize` troca a decisao
transitoria `processing` pela final; `ensure_finalized` e a trava antes do COMMIT.
`mark_error` roda em transacao propria (a da mensagem foi desfeita). Corpo so de
mensagem autorizada (`allowed`). Nunca registra telefone nem corpo em log.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue, JarvisError
from app.core.values import AccessDecision, Decision, ReplyKind, SendResult

PROCESSING = AccessDecision.PROCESSING.value

# Decisoes que `record_send_result` pode impor ao rastro (resto do pipeline nao muda).
_SEND_DECISIONS = frozenset({Decision.SEND_FAILED, Decision.REVOKED_BEFORE_SEND})

# `has_sender` (migration 003) exige remetente ou access_decision='error'. Antes da
# identidade ser resolvida o rastro nasce com este valor provisorio em access_decision;
# `finalize` sobrescreve com a decisao de acesso real.
_PLACEHOLDER_ACCESS = AccessDecision.ERROR.value

_BEGIN = (
    "INSERT INTO chat.message_trace "
    "(wa_message_id, received_at, wa_timestamp, msg_type, access_decision, decision) "
    "VALUES ($1::text, $2, $3, $4::text, $5::text, $6::text) "
    "ON CONFLICT (wa_message_id) DO NOTHING RETURNING id"
)

_FINALIZE = (
    "UPDATE chat.message_trace SET user_id = $2::bigint, attempt_id = $3::bigint, "
    "access_decision = $4::text, decision = $5::text, decision_reason = $6::text, "
    "counted_in_limit = $7::boolean, body = $8::text, reply_kind = $9::text, "
    "ticket_number = $10::text "
    "WHERE id = $1::bigint AND decision = $11::text RETURNING id"
)

_MARK_ERROR = (
    "INSERT INTO chat.message_trace "
    "(wa_message_id, received_at, msg_type, access_decision, decision, decision_reason) "
    "VALUES ($1::text, $2, $3::text, 'error', 'error', $4::text) "
    "ON CONFLICT (wa_message_id) DO UPDATE SET access_decision = 'error', decision = 'error', "
    "decision_reason = EXCLUDED.decision_reason "
    "WHERE chat.message_trace.decision = $5::text RETURNING id"
)

_SEND_RESULT = (
    "UPDATE chat.message_trace SET send_result = $2::text, reply_wa_message_id = $3::text, "
    "reply_sent_at = $4, latency_ms = $5::integer, send_error = $6::text, "
    "decision = COALESCE($7::text, decision) WHERE id = $1::bigint RETURNING id"
)


class TraceNotFinalized(JarvisError):
    """O rastro ainda esta `processing` (nao pode ser commitado assim)."""


async def begin(
    conn: Any,
    wa_message_id: str,
    *,
    msg_type: str,
    wa_timestamp: datetime | None = None,
    clock: Clock | None = None,
) -> int | None:
    """Abre o rastro `processing`. Devolve o id, ou None se a mensagem e duplicata (AC-7)."""
    now = (clock or SystemClock()).now()
    row_id: int | None = await conn.fetchval(
        _BEGIN, wa_message_id, now, wa_timestamp, msg_type, _PLACEHOLDER_ACCESS, PROCESSING
    )
    return row_id


def _check_body(decision: Decision, body: str | None) -> None:
    if body is not None and decision is not Decision.ALLOWED:
        raise InvalidValue("body", "corpo so e guardado para mensagem autorizada")


async def finalize(
    conn: Any,
    trace_id: int,
    *,
    access_decision: AccessDecision,
    decision: Decision,
    user_id: int | None = None,
    attempt_id: int | None = None,
    decision_reason: str | None = None,
    counted_in_limit: bool = False,
    body: str | None = None,
    reply_kind: ReplyKind = ReplyKind.NONE,
    ticket_number: str | None = None,
) -> None:
    """Grava a decisao final. `body` so com `allowed`; so atua em rastro `processing`."""
    _check_body(decision, body)
    if access_decision is AccessDecision.PROCESSING:
        raise InvalidValue("access_decision", "decisao final nao pode ser processing")
    updated = await conn.fetchval(
        _FINALIZE,
        trace_id,
        user_id,
        attempt_id,
        access_decision.value,
        decision.value,
        decision_reason,
        counted_in_limit,
        body,
        reply_kind.value,
        ticket_number,
        PROCESSING,
    )
    if updated is None:
        raise InvalidValue("trace_id", "rastro inexistente ou ja finalizado")


async def ensure_finalized(conn: Any, trace_id: int) -> None:
    """Chamar antes do COMMIT: levanta se o rastro ainda esta `processing`."""
    decision: str | None = await conn.fetchval(
        "SELECT decision FROM chat.message_trace WHERE id = $1::bigint", trace_id
    )
    if decision == PROCESSING:
        raise TraceNotFinalized("rastro ainda em processing")


async def mark_error(
    pool: Any,
    wa_message_id: str,
    *,
    msg_type: str,
    reason: str,
    clock: Clock | None = None,
) -> None:
    """Grava rastro `error` em transacao propria (a da mensagem foi desfeita).

    `reason` deve ser o nome da classe do erro, nunca texto com PII. Nao sobrescreve
    um rastro ja finalizado (so um `processing` residual).
    """
    now = (clock or SystemClock()).now()
    async with pool.acquire() as conn, conn.transaction():
        await conn.fetchval(_MARK_ERROR, wa_message_id, now, msg_type, reason, PROCESSING)


async def record_send_result(
    conn: Any,
    trace_id: int,
    *,
    send_result: SendResult,
    decision: Decision | None = None,
    reply_wa_message_id: str | None = None,
    reply_sent_at: datetime | None = None,
    latency_ms: int | None = None,
    send_error: str | None = None,
) -> bool:
    """Registra o resultado do envio; `decision` so pode ser send_failed/revoked_before_send."""
    if decision is not None and decision not in _SEND_DECISIONS:
        raise InvalidValue("decision", "so send_failed ou revoked_before_send")
    updated = await conn.fetchval(
        _SEND_RESULT,
        trace_id,
        send_result.value,
        reply_wa_message_id,
        reply_sent_at,
        latency_ms,
        send_error,
        None if decision is None else decision.value,
    )
    return updated is not None
