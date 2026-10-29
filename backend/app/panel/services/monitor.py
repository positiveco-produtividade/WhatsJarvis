"""Monitor de sistema do painel (spec 4.11; task PN-T41).

Laco de 10 min (`LOOP`, sob trava consultiva do registro) que detecta tres anomalias a
partir de `chat.message_trace` / `chat.outbound_messages` e gera alertas de sistema via
`alerts.raise_alert` (com e-mail, dedup de 1 h por tipo): `volume_spike`, `error_rate` e
`api_failure`. O payload leva so contagens e taxas, nunca dados pessoais. Escalados e
numeros desconhecidos NAO sao alertas. Uma conexao por vez; `conn=` vai ao Flags.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock
from app.panel.services.workers import Loop
from app.services import alerts
from app.services.flags import Flags

logger = logging.getLogger(__name__)

INTERVAL_SECONDS = 600.0
DEDUP_WINDOW = timedelta(hours=1)
BASELINE_DAYS = 7

_VOLUME = (
    "SELECT "
    "(SELECT count(*) FROM chat.message_trace "
    "  WHERE received_at > $1::timestamptz - interval '1 hour' "
    "  AND received_at <= $1::timestamptz) AS current_count, "
    "(SELECT COALESCE(percentile_cont(0.5) WITHIN GROUP (ORDER BY c), 0)::float8 FROM ("
    "  SELECT (SELECT count(*) FROM chat.message_trace "
    "          WHERE received_at > $1::timestamptz - make_interval(days => d) - interval '1 hour' "
    "            AND received_at <= $1::timestamptz - make_interval(days => d)) AS c "
    "  FROM generate_series(1, $2::int) AS d) s) AS baseline_median"
)
_ERRORS = (
    "SELECT count(*) AS total, "
    "count(*) FILTER (WHERE decision = 'error' OR send_result = 'failed') AS errors "
    "FROM chat.message_trace WHERE received_at > $1 AND received_at <= $2"
)
_API_FAILURES = (
    "SELECT count(*) FROM chat.outbound_messages "
    "WHERE status = 'failed' AND COALESCE(claimed_at, created_at) > $1 "
    "AND COALESCE(claimed_at, created_at) <= $2"
)


@dataclass(frozen=True)
class MonitorDeps:
    """Dependencias de um ciclo (injetaveis nos testes)."""

    settings: Settings
    flags: Flags | None = None
    clock: Clock | None = None


async def _emit(
    conn: Any,
    deps: MonitorDeps,
    kind: str,
    severity: str,
    message: str,
    payload: dict[str, Any],
) -> int | None:
    return await alerts.raise_alert(
        conn,
        kind,
        severity,
        message,
        payload,
        dedup_key=kind,
        dedup_window=DEDUP_WINDOW,
        email=True,
        flags=deps.flags,
        settings=deps.settings,
        clock=deps.clock,
    )


async def check_volume_spike(conn: Any, deps: MonitorDeps, now: datetime) -> int | None:
    """Mensagens na ultima hora > fator x mediana das mesmas horas dos 7 dias anteriores."""
    cfg = deps.settings
    row = await conn.fetchrow(_VOLUME, now, BASELINE_DAYS)
    current = int(row["current_count"])
    median = float(row["baseline_median"])
    if current < cfg.monitor_volume_min_per_hour or current <= cfg.monitor_volume_factor * median:
        return None
    return await _emit(
        conn,
        deps,
        "volume_spike",
        "warn",
        "Volume de mensagens muito acima do normal na ultima hora.",
        {
            "current_count": current,
            "baseline_median": round(median, 2),
            "factor": cfg.monitor_volume_factor,
            "min_per_hour": cfg.monitor_volume_min_per_hour,
        },
    )


async def check_error_rate(conn: Any, deps: MonitorDeps, now: datetime) -> int | None:
    """(falhas de envio + decision='error') / total na janela >= taxa, com minimo de mensagens."""
    cfg = deps.settings
    window = timedelta(minutes=cfg.monitor_window_minutes)
    row = await conn.fetchrow(_ERRORS, now - window, now)
    total, errors = int(row["total"]), int(row["errors"])
    if total < cfg.monitor_error_min_messages:
        return None
    rate = errors / total
    if rate < cfg.monitor_error_rate:
        return None
    return await _emit(
        conn,
        deps,
        "error_rate",
        "high",
        "Taxa de erros no atendimento acima do limite.",
        {
            "total_count": total,
            "error_count": errors,
            "error_rate": round(rate, 4),
            "threshold": cfg.monitor_error_rate,
            "window_minutes": cfg.monitor_window_minutes,
        },
    )


async def check_api_failure(conn: Any, deps: MonitorDeps, now: datetime) -> int | None:
    """Falhas definitivas de envio na Meta na janela >= limite."""
    cfg = deps.settings
    window = timedelta(minutes=cfg.monitor_window_minutes)
    failures = int(await conn.fetchval(_API_FAILURES, now - window, now))
    if failures < cfg.monitor_api_failures:
        return None
    return await _emit(
        conn,
        deps,
        "api_failure",
        "high",
        "Falhas definitivas de envio pela API do WhatsApp.",
        {
            "failure_count": failures,
            "threshold": cfg.monitor_api_failures,
            "window_minutes": cfg.monitor_window_minutes,
        },
    )


async def run_checks(conn: Any, deps: MonitorDeps) -> dict[str, int | None]:
    """Roda as tres verificacoes; devolve o id do alerta criado (ou None) por tipo."""
    now = (deps.clock or SystemClock()).now()
    return {
        "volume_spike": await check_volume_spike(conn, deps, now),
        "error_rate": await check_error_rate(conn, deps, now),
        "api_failure": await check_api_failure(conn, deps, now),
    }


async def run(app: Any) -> None:
    """Um ciclo do laco: uma conexao do pool para tudo."""
    state = app.state
    deps = MonitorDeps(
        settings=getattr(state, "settings", None) or get_settings(),
        flags=getattr(state, "flags", None),
    )
    async with state.pool.acquire() as conn:
        created = await run_checks(conn, deps)
    logger.info(
        "panel_monitor_cycle",
        extra={"alerts": sorted(k for k, v in created.items() if v is not None)},
    )


LOOP = Loop(name="monitor", interval_seconds=INTERVAL_SECONDS, run=run)
