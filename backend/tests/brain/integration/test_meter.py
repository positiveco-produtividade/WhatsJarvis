"""Medidor de custo e teto mensal do cerebro (CB-15, CB-20)."""
from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services.brain.meter import BrainMeter, month_bounds, month_key
from tests.brain import factories

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
_N = itertools.count(1)


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "brain_price_in_per_mtok_usd": 0.3,
        "brain_price_out_per_mtok_usd": 2.5,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _run(conn: asyncpg.Connection, user_id: int, cost: str, at: datetime) -> None:
    trace_id = await factories.create_pending_trace(
        conn, user_id, at, wa_message_id=f"wamid.meter.{next(_N)}"
    )
    await conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, outcome, path, cost_usd, created_at) "
        "VALUES ($1, 'answered', 'ia', $2::numeric, $3)",
        trace_id,
        cost,
        at,
    )


async def _alerts(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT * FROM ops.alerts WHERE kind = 'brain_budget' ORDER BY id")


def _meter(clock: FakeClock, **over: Any) -> BrainMeter:
    return BrainMeter(make_settings(**over), clock)


def test_cost_is_tokens_times_configured_price(clock: FakeClock) -> None:
    meter = _meter(clock)
    assert meter.cost_usd(1_000_000, 1_000_000) == Decimal("2.800000")
    assert meter.cost_usd(1000, 200) == Decimal("0.000800")
    assert meter.cost_usd(0, 0) == Decimal("0.000000")
    other = _meter(clock, brain_price_in_per_mtok_usd=1, brain_price_out_per_mtok_usd=4)
    assert other.cost_usd(1_000_000, 500_000) == Decimal("3.000000")


def test_cost_without_configured_price_fails(clock: FakeClock) -> None:
    meter = _meter(clock, brain_price_in_per_mtok_usd=None, brain_price_out_per_mtok_usd=None)
    with pytest.raises(InvalidValue):
        meter.cost_usd(10, 10)


def test_month_boundaries_use_sao_paulo() -> None:
    # 01/11 02:00 UTC ainda e 31/10 23:00 em Sao Paulo (UTC-3)
    assert month_key(datetime(2026, 11, 1, 2, 0, tzinfo=UTC)) == "2026-10"
    assert month_key(datetime(2026, 11, 1, 3, 0, tzinfo=UTC)) == "2026-11"
    start, end = month_bounds(datetime(2026, 12, 15, 12, 0, tzinfo=UTC))
    assert start.astimezone(UTC) == datetime(2026, 12, 1, 3, 0, tzinfo=UTC)
    assert end.astimezone(UTC) == datetime(2027, 1, 1, 3, 0, tzinfo=UTC)


async def test_spend_sums_only_current_month_and_brain_runs(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "1.500000", NOW)
    await _run(db_conn, brain_user, "2.250000", datetime(2026, 10, 1, 3, 0, tzinfo=UTC))
    # fora do mes: 01/10 02:59 UTC e 30/09 em SP; 01/11 03:00 UTC e novembro em SP
    await _run(db_conn, brain_user, "50", datetime(2026, 10, 1, 2, 59, tzinfo=UTC))
    await _run(db_conn, brain_user, "50", datetime(2026, 11, 1, 3, 0, tzinfo=UTC))
    # o Assistente do painel nunca entra na conta
    await db_conn.execute(
        "INSERT INTO ops.assistant_questions (asked_at, asked_by, agent, question, status, "
        "tokens_in, tokens_out) VALUES ($1, 'x', 'a', 'q', 'ok', 999999999, 999999999)",
        NOW,
    )
    assert await _meter(clock).month_spend(db_conn) == Decimal("3.750000")


async def test_empty_month_is_zero_and_allowed(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    meter = _meter(clock)
    assert await meter.month_spend(db_conn) == Decimal(0)
    assert await meter.ai_allowed(db_conn) is True
    assert await _alerts(db_conn) == []


async def test_below_80_percent_no_alert(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "79.99", NOW)
    assert await _meter(clock).ai_allowed(db_conn) is True
    assert await _alerts(db_conn) == []


async def test_80_percent_raises_single_monthly_alert_with_email(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "80", NOW)
    meter = _meter(clock)
    assert await meter.ai_allowed(db_conn) is True
    meter.invalidate()
    assert await meter.ai_allowed(db_conn) is True
    rows = await _alerts(db_conn)
    assert len(rows) == 1
    assert rows[0]["severity"] == "warn"
    assert rows[0]["dedup_key"] == "brain_budget:2026-10"
    channels = await db_conn.fetch(
        "SELECT channel FROM ops.deliveries WHERE source_type = 'alert' AND source_id = $1",
        rows[0]["id"],
    )
    assert {r["channel"] for r in channels} == {"group", "email"}


async def test_100_percent_blocks_ai_until_month_turns(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "100", NOW)
    meter = _meter(clock)
    assert await meter.ai_allowed(db_conn) is False
    assert len(await _alerts(db_conn)) == 1
    # ainda 31/10 em Sao Paulo, mesmo ja sendo 01/11 em UTC: segue bloqueado
    clock.set(datetime(2026, 11, 1, 2, 30, tzinfo=UTC))
    assert await meter.ai_allowed(db_conn) is False
    # virou o mes em Sao Paulo: gasto zerado, IA liberada sem esperar o cache
    clock.set(datetime(2026, 11, 1, 3, 0, tzinfo=UTC))
    assert await meter.ai_allowed(db_conn) is True
    assert len(await _alerts(db_conn)) == 1


async def test_new_month_can_alert_again(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "85", NOW)
    meter = _meter(clock)
    await meter.ai_allowed(db_conn)
    await _run(db_conn, brain_user, "90", datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    clock.set(datetime(2026, 11, 5, 12, 0, tzinfo=UTC))
    assert await meter.ai_allowed(db_conn) is True
    keys = [r["dedup_key"] for r in await _alerts(db_conn)]
    assert keys == ["brain_budget:2026-10", "brain_budget:2026-11"]


async def test_spend_is_cached_for_30_seconds(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    meter = _meter(clock)
    assert await meter.month_spend(db_conn) == Decimal(0)
    await _run(db_conn, brain_user, "5", NOW)
    clock.advance(seconds=29)
    assert await meter.month_spend(db_conn) == Decimal(0)
    clock.advance(seconds=1)
    assert await meter.month_spend(db_conn) == Decimal("5.000000")


async def test_budget_follows_configuration(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _run(db_conn, brain_user, "10", NOW)
    meter = _meter(clock, brain_monthly_budget_usd=10)
    assert await meter.ai_allowed(db_conn) is False
