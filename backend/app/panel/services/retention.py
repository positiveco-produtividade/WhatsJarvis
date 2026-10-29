"""Rotina de retencao de 12 meses (spec 4.8; task PN-T61; AC PN-58).

`LOOP` verifica a cada 5 min a hora local (America/Sao_Paulo) e executa a purga uma vez
por dia, a partir das 02:30. Idempotente: o ultimo `retention.purge` de `ops.audit_log`
diz se hoje ja rodou (sobrevive a reinicio); a trava consultiva do registro de lacos
impede dois processos ao mesmo tempo.

Apaga em lotes de 5.000, na ordem das chaves estrangeiras (chat.tickets ->
chat.outbound_messages -> chat.message_trace), o que passou de `RETENTION_MONTHS`, mais
`unknown_first_messages`, `assistant_questions`, e `import_batches`/`panel_sessions`
vencidos. `ops.audit_log` e imutavel (gatilho) e NAO e purgada. Com `RETENTION_DRY_RUN`
so conta. A auditoria leva apenas contagens e a data de corte, nunca dados pessoais.
Uma conexao do pool por vez.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.panel.services.workers import Loop
from app.services import audit

logger = logging.getLogger(__name__)

SP_TZ_NAME = "America/Sao_Paulo"
RUN_AT = time(2, 30)
BATCH_SIZE = 5_000
INTERVAL_SECONDS = 300.0
FLOOR_MONTHS = 12
EXPIRING_DAYS = 30
AUDIT_ACTION = "retention.purge"

SP_TZ = ZoneInfo(SP_TZ_NAME)


def months_back(moment: datetime, months: int) -> datetime:
    """`moment` menos N meses de calendario (dia limitado ao fim do mes de destino)."""
    index = moment.year * 12 + (moment.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    day = min(moment.day, calendar.monthrange(year, month)[1])
    return moment.replace(year=year, month=month, day=day)


def floor_for(now: datetime, months: int = FLOOR_MONTHS) -> datetime:
    """Piso das consultas das telas: so o que e `>= now - 12 meses` aparece."""
    return months_back(now, months)


def cutoff_for(now: datetime, settings: Settings) -> datetime:
    """Corte da purga: `RETENTION_MONTHS` (minimo 1)."""
    return months_back(now, max(1, int(settings.retention_months)))


# --- alvos da purga (identificadores fixos do modulo; valores sempre por parametro) ---


@dataclass(frozen=True)
class Target:
    """Uma tabela purgada. `$1` = referencia de tempo (corte ou agora), `$2` = lote."""

    name: str
    uses_cutoff: bool
    where: str
    table: str

    @property
    def count_sql(self) -> str:
        return f"SELECT count(*) FROM {self.table} AS t WHERE {self.where}"

    @property
    def delete_sql(self) -> str:
        return (
            f"WITH d AS (DELETE FROM {self.table} WHERE ctid IN ("
            f"SELECT t.ctid FROM {self.table} AS t WHERE {self.where} LIMIT $2) RETURNING 1) "
            "SELECT count(*) FROM d"
        )


# Ordem das chaves estrangeiras: quem referencia message_trace sai antes dela.
TARGETS: tuple[Target, ...] = (
    Target("tickets", True, "t.created_at < $1", "chat.tickets"),
    Target("outbound_messages", True, "t.created_at < $1", "chat.outbound_messages"),
    Target(
        "message_trace",
        True,
        "t.received_at < $1 "
        "AND NOT EXISTS (SELECT 1 FROM chat.tickets k WHERE k.trace_id = t.id "
        "AND k.created_at >= $1) "
        "AND NOT EXISTS (SELECT 1 FROM chat.outbound_messages o WHERE o.inbound_trace_id = t.id "
        "AND o.created_at >= $1)",
        "chat.message_trace",
    ),
    Target("unknown_first_messages", True, "t.stored_at < $1", "access.unknown_first_messages"),
    Target("assistant_questions", True, "t.asked_at < $1", "ops.assistant_questions"),
    Target("import_batches", False, "t.expires_at < $1", "ops.import_batches"),
    Target("panel_sessions", False, "t.expires_at < $1", "ops.panel_sessions"),
)


@dataclass(frozen=True)
class PurgeResult:
    """Contagens por tabela (apagadas, ou que seriam apagadas no dry-run)."""

    dry_run: bool
    cutoff: datetime
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


async def _purge_target(conn: Any, target: Target, ref: datetime, *, dry_run: bool) -> int:
    if dry_run:
        return int(await conn.fetchval(target.count_sql, ref))
    total = 0
    while True:
        deleted = int(await conn.fetchval(target.delete_sql, ref, BATCH_SIZE))
        total += deleted
        if deleted < BATCH_SIZE:
            return total


async def purge(
    conn: Any,
    settings: Settings,
    clock: Clock | None = None,
    *,
    dry_run: bool | None = None,
) -> PurgeResult:
    """Uma purga completa + auditoria `retention.purge` (so contagens)."""
    clk = clock or SystemClock()
    now = clk.now()
    cutoff = cutoff_for(now, settings)
    effective_dry = settings.retention_dry_run if dry_run is None else dry_run
    counts: dict[str, int] = {}
    for target in TARGETS:
        ref = cutoff if target.uses_cutoff else now
        counts[target.name] = await _purge_target(conn, target, ref, dry_run=effective_dry)
    result = PurgeResult(dry_run=effective_dry, cutoff=cutoff, counts=counts)
    await audit.write(
        conn,
        "system",
        "retention",
        AUDIT_ACTION,
        "retention",
        "purge",
        None,
        {
            "dry_run": effective_dry,
            "cutoff": cutoff.date().isoformat(),
            "months": max(1, int(settings.retention_months)),
            "counts": counts,
        },
        clock=clk,
    )
    logger.info(
        "retention_purge",
        extra={"dry_run": effective_dry, "total": result.total, "counts": counts},
    )
    return result


# --- agenda diaria ---


def scheduled_for(now: datetime) -> datetime:
    """Instante das 02:30 locais do dia local de `now` (com fuso)."""
    local = now.astimezone(SP_TZ)
    return datetime.combine(local.date(), RUN_AT, tzinfo=SP_TZ)


def is_due(now: datetime, last_run_at: datetime | None) -> bool:
    """True se ja passou das 02:30 locais de hoje e nao houve execucao desde entao."""
    scheduled = scheduled_for(now)
    if now < scheduled:
        return False
    return last_run_at is None or last_run_at < scheduled


_LAST_RUN = "SELECT max(at) FROM ops.audit_log WHERE action = $1"


async def run_if_due(
    conn: Any, settings: Settings, clock: Clock | None = None
) -> PurgeResult | None:
    """Executa a purga so se for a hora e ainda nao rodou hoje."""
    clk = clock or SystemClock()
    last = await conn.fetchval(_LAST_RUN, AUDIT_ACTION)
    if not is_due(clk.now(), last):
        return None
    return await purge(conn, settings, clk)


async def run(app: Any) -> None:
    """Um ciclo do laco: uma conexao do pool."""
    state = app.state
    settings = getattr(state, "settings", None) or get_settings()
    async with state.pool.acquire() as conn:
        await run_if_due(conn, settings)


# --- leitura de volumes (aba Retencao, PN-T62) ---

_MONTHLY = (
    "SELECT date_trunc('month', {col} AT TIME ZONE '" + SP_TZ_NAME + "')::date AS month, "
    "count(*) AS n FROM {table} WHERE {col} >= $1 GROUP BY 1"
)
_EXPIRING = (
    "SELECT "
    "(SELECT count(*) FROM chat.message_trace WHERE received_at >= $1 AND received_at < $2), "
    "(SELECT count(*) FROM chat.tickets WHERE created_at >= $1 AND created_at < $2), "
    "(SELECT count(*) FROM chat.outbound_messages WHERE created_at >= $1 AND created_at < $2)"
)
_MONTHLY_SQL = {
    "messages": _MONTHLY.format(col="received_at", table="chat.message_trace"),
    "tickets": _MONTHLY.format(col="created_at", table="chat.tickets"),
    "replies": _MONTHLY.format(col="created_at", table="chat.outbound_messages"),
}


@dataclass(frozen=True)
class MonthVolume:
    month: date
    messages: int
    tickets: int
    replies: int


@dataclass(frozen=True)
class RetentionVolumes:
    """Volume por mes dentro do piso e o que expira nos proximos 30 dias."""

    months: tuple[MonthVolume, ...]
    expiring_messages: int
    expiring_tickets: int
    expiring_replies: int
    expiring_days: int
    cutoff: datetime


async def read_volumes(
    conn: Any, settings: Settings | None = None, clock: Clock | None = None
) -> RetentionVolumes:
    """Volume por mes (mais recente primeiro) acima do piso e o que expira em 30 dias."""
    cfg = settings or get_settings()
    now = (clock or SystemClock()).now()
    cutoff = cutoff_for(now, cfg)
    per_kind: dict[str, dict[date, int]] = {}
    for kind, sql in _MONTHLY_SQL.items():
        rows = await conn.fetch(sql, cutoff)
        per_kind[kind] = {r["month"]: int(r["n"]) for r in rows}
    all_months = sorted(set().union(*(d.keys() for d in per_kind.values())), reverse=True)
    months = tuple(
        MonthVolume(
            m,
            per_kind["messages"].get(m, 0),
            per_kind["tickets"].get(m, 0),
            per_kind["replies"].get(m, 0),
        )
        for m in all_months
    )
    horizon = cutoff + timedelta(days=EXPIRING_DAYS)
    exp = await conn.fetchrow(_EXPIRING, cutoff, horizon)
    return RetentionVolumes(
        months=months,
        expiring_messages=int(exp[0]),
        expiring_tickets=int(exp[1]),
        expiring_replies=int(exp[2]),
        expiring_days=EXPIRING_DAYS,
        cutoff=cutoff,
    )


LOOP = Loop(name="retention", interval_seconds=INTERVAL_SECONDS, run=run)
