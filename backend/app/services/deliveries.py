"""Despacho de entregas de alertas e tickets (ops.deliveries, spec 4.4.6, AC-13/AC-37/AC-36).

`dispatch(pool, delivery_id)` faz o claim atomico (pending -> sending), envia pelo canal
e grava `sent`, `pending` (com backoff), `failed`, `skipped` ou `panel_only`. Falha nunca
propaga ao chamador (webhook). E-mail e aviso de alerta nunca carregam telefone, nome nem
texto de mensagem. O aviso de ticket escalado no grupo (retorno-humano.1, RH-2, D4) leva o
primeiro nome e a UF do vendedor, a pergunta e o link para o Inbox, nunca o telefone. Logs
trazem so ids e codigos curtos.

Envio ao grupo: um por vez, com `TEAM_GROUP_MIN_INTERVAL_SECONDS` entre dois envios no mesmo
processo (RH-3). Ticket nao escalado (respondido pelo Jarvis) nao vai ao grupo: `skipped`.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.core.values import BRAND_NAMES
from app.services.flags import Flags
from app.services.notifications import DeliveryResult, DeliveryStatus, send_email
from app.services.waha import send_group_text

logger = logging.getLogger(__name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
COUNT_WINDOW = timedelta(hours=24)
PANEL_ALERTS_PATH = "/dashboard/alerts"

_CLAIM = (
    "UPDATE ops.deliveries SET status = 'sending', claimed_at = $2, attempts = attempts + 1 "
    "WHERE id = $1 AND status = 'pending' "
    "RETURNING id, source_type, source_id, channel, attempts"
)
_FINISH = (
    "UPDATE ops.deliveries SET status = $2, next_attempt_at = COALESCE($3, next_attempt_at), "
    "last_error = $4, sent_at = $5, claimed_at = NULL WHERE id = $1"
)
_ALERT = "SELECT severity, kind, message, user_id, created_at FROM ops.alerts WHERE id = $1"
_ALERT_COUNT = "SELECT count(*) FROM ops.alerts WHERE kind = $1 AND created_at > $2"
_TICKET = (
    "SELECT t.ticket_number, t.kind, t.brand, t.message, t.escalated, t.escalation_reason, "
    "u.name, u.ufs FROM chat.tickets t JOIN access.users u ON u.id = t.user_id WHERE t.id = $1"
)
INBOX_TICKET_PATH = "/dashboard/inbox/ticket/"
GROUP_MESSAGE_MAX_CHARS = 1000
NOT_ESCALATED = "not_escalated"

# Motivo de escalada -> texto do aviso (RH-2). Desconhecido ou ausente: "escalado".
REASON_LABELS: dict[str, str] = {
    "no_match": "sem resposta na FAQ",
    "not_answerable": "sem resposta na FAQ",
    "no_price_in_source": "preço fora da base",
    "paused": "Jarvis pausado",
    "brand_disabled": "marca desligada",
    "rating_down": "vendedor marcou 👎 na resposta",
    "brand_access_request": "pedido de acesso à marca",
    "commercial_condition": "condição comercial",
    "calc_not_supported": "cálculo que o Jarvis não faz",
    "out_of_scope": "fora do escopo",
    "injection": "mensagem suspeita",
}
MEDIA_REASON = "mídia (o Jarvis não lê áudio nem imagem)"
DEFAULT_REASON = "escalado"
_ANON_PREFIX = "anon:"

# Intervalo entre avisos no grupo (RH-3): trava e relogio monotonico do processo. `_sleep` e
# `_monotonic` sao trocados nos testes.
_group_lock: asyncio.Lock | None = None
_group_lock_loop: asyncio.AbstractEventLoop | None = None
_last_group_send: float | None = None
_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
_monotonic: Callable[[], float] = time.monotonic


@dataclass(frozen=True, slots=True)
class Content:
    """Conteudo pronto para envio; `subject` so existe no e-mail."""

    body: str
    subject: str = ""


def _local_instant(moment: datetime) -> str:
    return moment.astimezone(SAO_PAULO).strftime("%d/%m/%Y %H:%M (America/Sao_Paulo)")


def build_email(
    *,
    severity: str,
    kind: str,
    message: str,
    count: int,
    at: datetime,
    user_id: int | None,
    panel_base_url: str = "",
    alert_id: int | None = None,
) -> Content:
    """Assunto e corpo do e-mail de alerta. Nunca telefone, nome ou texto de mensagem."""
    lines = [
        f"Severidade: {severity}",
        f"Tipo: {kind}",
        f"Alerta: {message}",
        f"Ocorrências deste tipo nas últimas 24 h: {count}",
        f"Instante: {_local_instant(at)}",
    ]
    if user_id is not None:
        lines.append(f"Usuário #{user_id}")
    base = panel_base_url.strip().rstrip("/")
    if base:
        link = f"{base}{PANEL_ALERTS_PATH}"
        if alert_id is not None:
            link += f"?abrir={alert_id}"  # o painel destaca o alerta (PN-65)
        lines.append(f"Painel: {link}")
    return Content(body="\n".join(lines), subject=f"[ALTA] Jarvis: {kind}")


def build_alert_group_text(*, severity: str, kind: str, user_id: int | None) -> str:
    """Linha curta do grupo para alerta (sem telefone/nome/texto)."""
    who = f" — usuário #{user_id}" if user_id is not None else ""
    return f"Alerta {severity}: {kind}{who}. Veja o painel."


class _KeepUnknown(dict[str, str]):
    """`format_map` que deixa `{campo}` desconhecido como esta (modelo vindo da configuracao)."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def seller_label(name: str | None, ufs: list[str] | None) -> str:
    """Primeiro nome e UF(s) do vendedor; nunca telefone. Anonimizado (LGPD) = 'Vendedor'."""
    raw = (name or "").strip()
    first = "" if raw.startswith(_ANON_PREFIX) else (raw.split()[0] if raw else "")
    who = first or "Vendedor"
    places = "/".join(u for u in (ufs or []) if u)
    return f"{who} ({places})" if places else who


def _message_label(kind: str, message: str | None) -> str:
    text = (message or "").strip()
    if kind == "media":
        return text or "[mídia]"
    if not text:
        return "(mensagem removida)"
    if len(text) > GROUP_MESSAGE_MAX_CHARS:
        text = text[: GROUP_MESSAGE_MAX_CHARS - 1].rstrip() + "…"
    return f"“{text}”"


def build_ticket_group_text(
    *,
    template: str,
    ticket_number: str,
    kind: str,
    brand: str | None,
    escalation_reason: str | None,
    message: str | None,
    seller_name: str | None,
    seller_ufs: list[str] | None,
    link_base: str,
) -> str:
    """Aviso de ticket escalado no grupo (RH-2), montado com o modelo da configuracao."""
    if kind == "media":
        reason = MEDIA_REASON
    else:
        reason = REASON_LABELS.get(escalation_reason or "", DEFAULT_REASON)
    fields = _KeepUnknown(
        ticket=ticket_number,
        marca=BRAND_NAMES.get(brand or "", "não identificada"),
        motivo=reason,
        vendedor=seller_label(seller_name, seller_ufs),
        mensagem=_message_label(kind, message),
        link=f"{link_base.rstrip('/')}{INBOX_TICKET_PATH}{ticket_number}",
    )
    return template.format_map(fields)


async def _alert_content(
    conn: Any, source_id: int, channel: str, cfg: Settings
) -> Content | None:
    row = await conn.fetchrow(_ALERT, source_id)
    if row is None:
        return None
    if channel == "group":
        return Content(
            build_alert_group_text(
                severity=row["severity"], kind=row["kind"], user_id=row["user_id"]
            )
        )
    count = int(await conn.fetchval(_ALERT_COUNT, row["kind"], row["created_at"] - COUNT_WINDOW))
    return build_email(
        severity=row["severity"],
        kind=row["kind"],
        message=row["message"],
        count=max(count, 1),
        at=row["created_at"],
        user_id=row["user_id"],
        panel_base_url=cfg.panel_base_url,
        alert_id=source_id,
    )


async def _ticket_content(conn: Any, source_id: int, cfg: Settings) -> Content | str | None:
    """Texto do aviso; `NOT_ESCALATED` se o Jarvis respondeu (nao vai ao grupo, RH-1)."""
    row = await conn.fetchrow(_TICKET, source_id)
    if row is None:
        return None
    if not row["escalated"]:
        return NOT_ESCALATED
    return Content(
        build_ticket_group_text(
            template=cfg.text_team_group_ticket,
            ticket_number=row["ticket_number"],
            kind=row["kind"],
            brand=row["brand"],
            escalation_reason=row["escalation_reason"],
            message=row["message"],
            seller_name=row["name"],
            seller_ufs=list(row["ufs"] or []),
            link_base=cfg.panel_link_base,
        )
    )


async def _load_content(conn: Any, claimed: Any, cfg: Settings) -> Content | str | None:
    if claimed["source_type"] == "ticket":
        return await _ticket_content(conn, claimed["source_id"], cfg)
    return await _alert_content(conn, claimed["source_id"], claimed["channel"], cfg)


async def _finish(
    pool: Any, delivery_id: int, status: str, *, error: str | None, now: datetime,
    next_at: datetime | None = None,
) -> None:
    sent_at = now if status == "sent" else None
    await pool.execute(_FINISH, delivery_id, status, next_at, error, sent_at)


def _retry_status(
    result: DeliveryResult, attempts: int, cfg: Settings, now: datetime
) -> tuple[str, datetime | None]:
    """Mapeia o resultado do envio para (status, proxima tentativa)."""
    if result.status is DeliveryStatus.OK:
        return "sent", None
    if result.status is DeliveryStatus.SKIPPED:
        return "skipped", None
    if result.status is DeliveryStatus.PERMANENT or attempts >= cfg.email_max_attempts:
        return "failed", None
    waits = cfg.email_backoff_list
    wait = waits[min(attempts - 1, len(waits) - 1)]
    return "pending", now + timedelta(seconds=wait)


async def _send(
    claimed: Any,
    content: Content,
    cfg: Settings,
    email_client: httpx.AsyncClient | None,
    waha_client: httpx.AsyncClient | None,
) -> DeliveryResult:
    if claimed["channel"] == "email":
        return await send_email(content.subject, content.body, settings=cfg, client=email_client)
    return await _send_group_spaced(content.body, cfg, waha_client)


def _group_send_lock() -> asyncio.Lock:
    """Trava do envio ao grupo, uma por event loop (o app tem um; os testes, varios)."""
    global _group_lock, _group_lock_loop
    loop = asyncio.get_running_loop()
    if _group_lock is None or _group_lock_loop is not loop:
        _group_lock, _group_lock_loop = asyncio.Lock(), loop
    return _group_lock


async def _send_group_spaced(
    text: str, cfg: Settings, client: httpx.AsyncClient | None
) -> DeliveryResult:
    """Um envio ao grupo por vez, com o intervalo minimo desde o anterior (RH-3)."""
    global _last_group_send
    async with _group_send_lock():
        if _last_group_send is not None:
            wait = cfg.team_group_min_interval_seconds - (_monotonic() - _last_group_send)
            if wait > 0:
                await _sleep(wait)
        try:
            return await send_group_text(text, settings=cfg, client=client)
        finally:
            _last_group_send = _monotonic()


async def send_team_group_text(
    text: str, cfg: Settings, client: httpx.AsyncClient | None = None
) -> DeliveryResult:
    """Envio avulso ao grupo (resumo diario, PN-61) respeitando o intervalo entre avisos."""
    return await _send_group_spaced(text, cfg, client)


async def _run(
    pool: Any,
    claimed: Any,
    flags: Flags,
    cfg: Settings,
    now: datetime,
    email_client: httpx.AsyncClient | None,
    waha_client: httpx.AsyncClient | None,
) -> str:
    delivery_id = claimed["id"]
    if claimed["channel"] == "group" and not await flags.team_group_enabled():
        await _finish(pool, delivery_id, "panel_only", error=None, now=now)
        return "panel_only"
    async with pool.acquire() as conn:
        content = await _load_content(conn, claimed, cfg)
    if content is None:
        await _finish(pool, delivery_id, "failed", error="source_missing", now=now)
        return "failed"
    if isinstance(content, str):  # ticket nao escalado: nao e aviso para o grupo
        await _finish(pool, delivery_id, "skipped", error=content, now=now)
        return "skipped"
    result = await _send(claimed, content, cfg, email_client, waha_client)
    status, next_at = _retry_status(result, claimed["attempts"], cfg, now)
    error = None if result.ok else result.detail
    await _finish(pool, delivery_id, status, error=error, now=now, next_at=next_at)
    return status


async def _release_after_error(pool: Any, claimed: Any, cfg: Settings, now: datetime) -> str:
    """Erro inesperado: devolve a linha a fila (ou desiste no limite), sem propagar."""
    result = DeliveryResult(DeliveryStatus.TRANSIENT, "unexpected_error")
    status, next_at = _retry_status(result, claimed["attempts"], cfg, now)
    try:
        await _finish(
            pool, claimed["id"], status, error="unexpected_error", now=now, next_at=next_at
        )
    except Exception as exc:  # noqa: BLE001 - nunca propaga ao webhook
        logger.error("delivery release failed: %s", type(exc).__name__)
    return status


async def dispatch(
    pool: Any,
    delivery_id: int,
    *,
    flags: Flags,
    settings: Settings | None = None,
    clock: Clock | None = None,
    email_client: httpx.AsyncClient | None = None,
    waha_client: httpx.AsyncClient | None = None,
) -> str | None:
    """Despacha uma entrega; devolve o status final, ou None se nao havia o que despachar.

    Nunca levanta: falhas viram `pending` (retentativa), `failed` ou ficam logadas.
    """
    cfg = settings or get_settings()
    now = (clock or SystemClock()).now()
    try:
        claimed = await pool.fetchrow(_CLAIM, delivery_id, now)
    except Exception as exc:  # noqa: BLE001 - nunca propaga ao webhook
        logger.error("delivery claim failed: %s", type(exc).__name__)
        return None
    if claimed is None:
        return None
    try:
        status = await _run(pool, claimed, flags, cfg, now, email_client, waha_client)
    except Exception as exc:  # noqa: BLE001 - nunca propaga ao webhook
        logger.error("delivery dispatch error: %s", type(exc).__name__)
        status = await _release_after_error(pool, claimed, cfg, now)
    logger.info(
        "delivery_dispatched",
        extra={
            "delivery_id": delivery_id,
            "channel": claimed["channel"],
            "status": status,
            "attempts": claimed["attempts"],
        },
    )
    return status
