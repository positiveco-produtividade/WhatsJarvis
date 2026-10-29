"""Despacho de entregas de alertas e tickets (ops.deliveries, spec 4.4.6, AC-13/AC-37/AC-36).

`dispatch(pool, delivery_id)` faz o claim atomico (pending -> sending), envia pelo canal
e grava `sent`, `pending` (com backoff), `failed`, `skipped` ou `panel_only`. Falha nunca
propaga ao chamador (webhook). E-mail e grupo nunca carregam telefone, nome nem texto de
mensagem; logs trazem so ids e codigos curtos.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
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
_TICKET = "SELECT ticket_number, user_id, message FROM chat.tickets WHERE id = $1"


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


def build_ticket_group_text(*, ticket_number: str, user_id: int, message: str) -> str:
    """Ex.: 'Midia de usuario #12 (audio) aguardando a equipe - ticket J-0012'."""
    label = message.strip().strip("[]") or "mídia"
    return f"Mídia de usuário #{user_id} ({label}) aguardando a equipe — ticket {ticket_number}"


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


async def _ticket_content(conn: Any, source_id: int) -> Content | None:
    row = await conn.fetchrow(_TICKET, source_id)
    if row is None:
        return None
    return Content(
        build_ticket_group_text(
            ticket_number=row["ticket_number"], user_id=row["user_id"], message=row["message"]
        )
    )


async def _load_content(conn: Any, claimed: Any, cfg: Settings) -> Content | None:
    if claimed["source_type"] == "ticket":
        return await _ticket_content(conn, claimed["source_id"])
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
    return await send_group_text(content.body, settings=cfg, client=waha_client)


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
