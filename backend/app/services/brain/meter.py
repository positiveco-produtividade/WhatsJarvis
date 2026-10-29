"""Medidor de custo e teto mensal do cerebro (CB-15, CB-20).

Custo = tokens x preco configurado por 1M de tokens (`BRAIN_PRICE_*`; nenhum preco no codigo).
Gasto do mes = SUM(chat.brain_runs.cost_usd) no mes civil de America/Sao_Paulo. So o cerebro
conta: nunca soma `ops.assistant_questions` (o Assistente do painel tem limite proprio).
Aos 80% do teto cria um unico alerta `brain_budget` por mes (dedup_key mensal); aos 100% a IA
fica bloqueada (`ai_allowed()` False) ate virar o mes. O gasto e cacheado por 30 s. A
concorrencia pode estourar o teto por poucas chamadas: aceito (spec 4.8).
Nao importa `app.panel`.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.core.clock import Clock
from app.core.errors import InvalidValue
from app.services import alerts

logger = logging.getLogger(__name__)

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
CACHE_TTL_SECONDS = 30.0
ALERT_KIND = "brain_budget"
_MTOK = Decimal(1_000_000)

_MONTH_SPEND = (
    "SELECT COALESCE(SUM(cost_usd), 0) FROM chat.brain_runs "
    "WHERE created_at >= $1 AND created_at < $2"
)


def month_key(moment: datetime) -> str:
    """`YYYY-MM` do mes civil em America/Sao_Paulo."""
    local = moment.astimezone(SAO_PAULO)
    return f"{local.year:04d}-{local.month:02d}"


def month_bounds(moment: datetime) -> tuple[datetime, datetime]:
    """Inicio (inclusive) e fim (exclusivo) do mes de Sao Paulo, como instantes com fuso."""
    local = moment.astimezone(SAO_PAULO)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start.month == 12:
        end = start.replace(year=start.year + 1, month=1)
    else:
        end = start.replace(month=start.month + 1)
    return start, end


def compute_cost_usd(
    tokens_in: int, tokens_out: int, *, price_in_per_mtok: float, price_out_per_mtok: float
) -> Decimal:
    """tokens_in*preco_in + tokens_out*preco_out, precos por 1M de tokens (6 casas)."""
    cost = (
        Decimal(tokens_in) * Decimal(str(price_in_per_mtok))
        + Decimal(tokens_out) * Decimal(str(price_out_per_mtok))
    ) / _MTOK
    return cost.quantize(Decimal("0.000001"))


class BrainMeter:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._settings = settings
        self._clock = clock
        self._cached: tuple[str, float, Decimal] | None = None  # (mes, instante, gasto)

    def cost_usd(self, tokens_in: int, tokens_out: int) -> Decimal:
        """Custo estimado de uma chamada; `tokens_out` ja inclui os tokens de pensamento."""
        s = self._settings
        if s.brain_price_in_per_mtok_usd is None or s.brain_price_out_per_mtok_usd is None:
            raise InvalidValue("brain_price", "preco do modelo nao configurado")
        return compute_cost_usd(
            tokens_in,
            tokens_out,
            price_in_per_mtok=s.brain_price_in_per_mtok_usd,
            price_out_per_mtok=s.brain_price_out_per_mtok_usd,
        )

    async def month_spend(self, conn: Any) -> Decimal:
        """Gasto do mes corrente (Sao Paulo) em `brain_runs`; cache de 30 s, zerado na virada."""
        now = self._clock.now()
        key = month_key(now)
        stamp = now.timestamp()
        cached = self._cached
        if cached is not None and cached[0] == key and 0 <= stamp - cached[1] < CACHE_TTL_SECONDS:
            return cached[2]
        start, end = month_bounds(now)
        spent = Decimal(await conn.fetchval(_MONTH_SPEND, start, end))
        self._cached = (key, stamp, spent)
        return spent

    def invalidate(self) -> None:
        self._cached = None

    async def ai_allowed(self, conn: Any) -> bool:
        """False quando o gasto do mes chegou ao teto; aos 80% cria o aviso unico do mes."""
        spent = await self.month_spend(conn)
        budget = Decimal(str(self._settings.brain_monthly_budget_usd))
        alert_at = budget * Decimal(str(self._settings.brain_budget_alert_pct))
        if spent >= alert_at:
            await self._alert_once(conn, spent, budget)
        allowed = spent < budget
        if not allowed:
            month = month_key(self._clock.now())
            logger.warning("brain_budget", extra={"state": "blocked", "month": month})
        return allowed

    async def _alert_once(self, conn: Any, spent: Decimal, budget: Decimal) -> None:
        month = month_key(self._clock.now())
        pct = int(self._settings.brain_budget_alert_pct * 100)
        alert_id = await alerts.raise_alert(
            conn,
            ALERT_KIND,
            "warn",
            f"Gasto do cerebro no mes chegou a {pct}% do teto mensal",
            {
                "month": month,
                "spent_usd": float(round(spent, 2)),
                "budget_usd": float(budget),
                "alert_pct": pct,
            },
            dedup_key=f"{ALERT_KIND}:{month}",
            dedup_window=None,
            email=True,
            settings=self._settings,
            clock=self._clock,
        )
        if alert_id is not None:
            extra = {"state": "alerted", "month": month, "alert_id": alert_id}
            logger.info("brain_budget", extra=extra)
