"""Resumo diario dos pendentes no grupo da equipe, as 08:05 (painel.1 PN-61, task PN-T72).

Igual ao AUTO-1 (decisao do Caio, 2026-11-18): todo dia, inclusive fim de semana, as 08:05
de America/Fortaleza; sem pendentes manda "0 pendentes". So com a chave do grupo ligada.
Uma vez por dia: o envio bem-sucedido grava `daily_summary.sent` na auditoria e e isso que
o laco consulta (sobrevive a reinicio). Falha no WAHA nao grava: o proximo ciclo (1 min)
tenta de novo. App fora do ar as 08:05: sai quando voltar, no mesmo dia.
O texto nao leva nome nem telefone de vendedor; so contagens e o numero do ticket.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.panel.services.workers import Loop
from app.services import audit
from app.services.deliveries import send_team_group_text
from app.services.flags import Flags
from app.services.notifications import DeliveryResult

logger = logging.getLogger(__name__)

TZ_NAME = "America/Fortaleza"
TZ = ZoneInfo(TZ_NAME)
RUN_AT = time(8, 5)
INTERVAL_SECONDS = 60.0
AUDIT_ACTION = "daily_summary.sent"
LINK_PATH = "/dashboard/escalados"

SendFn = Callable[[str, Settings, "httpx.AsyncClient | None"], Awaitable[DeliveryResult]]

_COUNTS = (
    "SELECT "
    "count(*) FILTER (WHERE escalated AND status = 'awaiting') AS awaiting, "
    "count(*) FILTER (WHERE escalated AND status = 'awaiting' AND urgency = 'muito_urgente') "
    "  AS muito_urgente, "
    "count(*) FILTER (WHERE escalated AND status = 'awaiting' AND urgency = 'urgente') "
    "  AS urgente, "
    "count(*) FILTER (WHERE escalated AND status = 'awaiting' AND urgency = 'pouco_urgente') "
    "  AS pouco_urgente, "
    "count(*) FILTER (WHERE status = 'awaiting' AND escalation_reason = 'brand_access_request') "
    "  AS access_requests, "
    "count(*) FILTER (WHERE status = 'send_error') AS send_errors "
    "FROM chat.tickets"
)
_OLDEST = (
    "SELECT ticket_number, created_at FROM chat.tickets "
    "WHERE escalated AND status = 'awaiting' ORDER BY created_at, id LIMIT 1"
)
_LAST_SENT = "SELECT max(at) FROM ops.audit_log WHERE action = $1"


@dataclass(frozen=True)
class Summary:
    day: date
    awaiting: int
    muito_urgente: int
    urgente: int
    pouco_urgente: int
    access_requests: int
    send_errors: int
    oldest_number: str | None
    oldest_days: int | None

    @property
    def empty(self) -> bool:
        return self.awaiting == 0 and self.send_errors == 0


def scheduled_for(now: datetime) -> datetime:
    """08:05 de hoje em Fortaleza."""
    local = now.astimezone(TZ)
    return datetime.combine(local.date(), RUN_AT, tzinfo=TZ)


def is_due(now: datetime, last_sent_at: datetime | None) -> bool:
    """True se ja passou das 08:05 locais de hoje e o resumo ainda nao saiu desde entao."""
    scheduled = scheduled_for(now)
    if now < scheduled:
        return False
    return last_sent_at is None or last_sent_at < scheduled


async def read_summary(conn: Any, now: datetime) -> Summary:
    counts = await conn.fetchrow(_COUNTS)
    oldest = await conn.fetchrow(_OLDEST)
    local_day = now.astimezone(TZ).date()
    oldest_days = None
    if oldest is not None:
        oldest_days = max(0, (local_day - oldest["created_at"].astimezone(TZ).date()).days)
    return Summary(
        day=local_day,
        awaiting=int(counts["awaiting"]),
        muito_urgente=int(counts["muito_urgente"]),
        urgente=int(counts["urgente"]),
        pouco_urgente=int(counts["pouco_urgente"]),
        access_requests=int(counts["access_requests"]),
        send_errors=int(counts["send_errors"]),
        oldest_number=None if oldest is None else str(oldest["ticket_number"]),
        oldest_days=oldest_days,
    )


def _age(days: int) -> str:
    if days == 0:
        return "de hoje"
    return "há 1 dia" if days == 1 else f"há {days} dias"


def build_text(summary: Summary, cfg: Settings) -> str:
    day = summary.day.strftime("%d/%m/%Y")
    if summary.empty:
        return cfg.text_team_group_daily_summary_empty.format(data=day)
    lines = [
        cfg.text_team_group_daily_summary_title.format(data=day),
        f"⏳ Escalados aguardando resposta: {summary.awaiting}",
        f"   🔴 Muito urgentes: {summary.muito_urgente} · 🟠 Urgentes: {summary.urgente}"
        f" · 🟢 Pouco urgentes: {summary.pouco_urgente}",
    ]
    if summary.oldest_number is not None and summary.oldest_days is not None:
        lines.append(f"🕐 Mais antigo: {summary.oldest_number}, {_age(summary.oldest_days)}")
    lines.append(f"🔑 Pedidos de acesso a marca: {summary.access_requests}")
    lines.append(f"⚠ Respostas da equipe que falharam: {summary.send_errors}")
    lines.append(f"Abrir: {cfg.panel_link_base.rstrip('/')}{LINK_PATH}")
    return "\n".join(lines)


@dataclass
class SummaryDeps:
    settings: Settings
    flags: Flags | None
    clock: Clock | None = None
    client: httpx.AsyncClient | None = None
    send: SendFn = send_team_group_text


async def run_if_due(conn: Any, deps: SummaryDeps) -> str:
    """Um ciclo. Devolve o que aconteceu: not_due | group_off | sent | failed."""
    now = (deps.clock or SystemClock()).now()
    if not is_due(now, await conn.fetchval(_LAST_SENT, AUDIT_ACTION)):
        return "not_due"
    if deps.flags is None or not await deps.flags.team_group_enabled():
        return "group_off"
    summary = await read_summary(conn, now)
    # Marca "enviado" ANTES de mandar, na mesma transacao: se a auditoria falhar, nada sai;
    # se o envio falhar, a transacao desfaz a marca e o proximo ciclo tenta de novo.
    tx = conn.transaction()
    await tx.start()
    try:
        await audit.write(
            conn, "system", "jarvis", AUDIT_ACTION, "team_group", summary.day.isoformat(),
            new={"awaiting": summary.awaiting, "send_errors": summary.send_errors},
            clock=deps.clock,
        )  # fmt: skip
        result = await deps.send(build_text(summary, deps.settings), deps.settings, deps.client)
    except BaseException:
        await tx.rollback()
        raise
    if not result.ok:
        await tx.rollback()
        logger.warning("daily_summary_failed", extra={"detail": result.detail})
        return "failed"
    await tx.commit()
    logger.info("daily_summary_sent", extra={"awaiting": summary.awaiting})
    return "sent"


async def run(app: Any) -> None:
    """Um ciclo do laco: uma conexao do pool."""
    state = app.state
    deps = SummaryDeps(
        settings=getattr(state, "settings", None) or get_settings(),
        flags=getattr(state, "flags", None),
    )
    async with state.pool.acquire() as conn:
        await run_if_due(conn, deps)


LOOP = Loop(name="daily_summary", interval_seconds=INTERVAL_SECONDS, run=run)
