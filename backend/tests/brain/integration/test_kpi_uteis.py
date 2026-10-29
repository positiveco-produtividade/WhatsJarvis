"""KPI "Avaliados como uteis" (CB-26) contra Postgres real: up/(up+down) e cobertura."""
from __future__ import annotations

import itertools
from datetime import datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.api.metrics import USEFUL_TARGET_HINT, build_view
from app.panel.services.metrics_read import (
    MetricsCache,
    load_metrics,
    metrics_snapshot,
    parse_period,
)
from app.panel.services.scope import Scope
from tests.brain import factories
from tests.panel import factories as panel_factories
from tests.panel.factories import BRAND_A, BRAND_B

pytestmark = pytest.mark.db

ALL = Scope(None)
P30 = parse_period("30")
_N = itertools.count(1)


async def _answered(
    conn: asyncpg.Connection, user_id: int, brand: str, at: datetime, *, buttons: bool = True
) -> tuple[int, int]:
    """Pergunta respondida: rastro, ticket da marca, brain_run e (opcional) saida com botoes."""
    n = next(_N)
    trace_id = await factories.create_pending_trace(conn, user_id, at, wa_message_id=f"wamid.k{n}")
    ticket = await panel_factories.insert_ticket(
        conn, number=900 + n, user_id=user_id, status="answered_bot", created_at=at, brand=brand
    )
    await conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, ticket_id, outcome, path, brand, created_at) "
        "VALUES ($1, $2, 'answered', 'ia', $3, $4)",
        trace_id, ticket.id, brand, at,
    )
    if buttons:
        await conn.execute(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
            "inbound_trace_id, status, next_attempt_at, created_at) "
            "VALUES ($1, '5511900000000', 'brain_answer_rate', 'active', 'resp', $2, 'sent', "
            "$3, $3)",
            user_id, trace_id, at,
        )
    return ticket.id, trace_id


async def _rate(
    conn: asyncpg.Connection, user_id: int, ids: tuple[int, int], rating: str, at: datetime
) -> None:
    await conn.execute(
        "INSERT INTO chat.answer_ratings (ticket_id, user_id, trace_id, rating, rated_at) "
        "VALUES ($1, $2, $3, $4, $5)",
        ids[0], user_id, ids[1], rating, at,
    )


async def _clean(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM chat.tickets")
    await conn.execute("DELETE FROM chat.message_trace")


async def test_sem_avaliacao_mantem_placeholder(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _clean(db_conn)
    await _answered(db_conn, brain_user, BRAND_A, clock.now() - timedelta(hours=2))
    snap = await load_metrics(db_conn, ALL, P30, now=clock.now())
    k = snap.kpis
    assert k.useful_pct is None and not k.has_ratings and k.coverage_pct == 0.0
    assert k.answers_with_buttons == 1
    assert build_view(snap).kpis[3].hint == USEFUL_TARGET_HINT


async def test_up_sobre_up_mais_down_e_cobertura(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _clean(db_conn)
    at = clock.now() - timedelta(hours=3)
    ids = [await _answered(db_conn, brain_user, BRAND_A, at) for _ in range(4)]
    await _answered(db_conn, brain_user, BRAND_A, at)  # sem avaliacao
    for pair, rating in zip(ids, ["up", "up", "up", "down"], strict=True):
        await _rate(db_conn, brain_user, pair, rating, at)
    k = (await load_metrics(db_conn, ALL, P30, now=clock.now())).kpis
    assert k.useful_pct == 75.0 and k.rated_count == 4
    assert k.answers_with_buttons == 5 and k.coverage_pct == 80.0
    assert k.useful_label == "75%"
    view = build_view(await load_metrics(db_conn, ALL, P30, now=clock.now()))
    assert view.kpis[3].value == "75%" and "cobertura 80%" in view.kpis[3].hint


async def test_filtra_por_marca_e_periodo(
    db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _clean(db_conn)
    recent = clock.now() - timedelta(hours=3)
    old = clock.now() - timedelta(days=10)
    a_up = await _answered(db_conn, brain_user, BRAND_A, recent)
    b_down = await _answered(db_conn, brain_user, BRAND_B, recent)
    a_old = await _answered(db_conn, brain_user, BRAND_A, old)
    await _rate(db_conn, brain_user, a_up, "up", recent)
    await _rate(db_conn, brain_user, b_down, "down", recent)
    await _rate(db_conn, brain_user, a_old, "down", old)

    allk = (await load_metrics(db_conn, ALL, P30, now=clock.now())).kpis
    assert allk.rated_count == 3 and allk.useful_pct == 33.3
    a30 = (await load_metrics(db_conn, Scope(BRAND_A), P30, now=clock.now())).kpis
    assert a30.useful_pct == 50.0 and a30.answers_with_buttons == 2
    a7 = (await load_metrics(db_conn, Scope(BRAND_A), parse_period("7"), now=clock.now())).kpis
    assert a7.useful_pct == 100.0 and a7.coverage_pct == 100.0
    b7 = (await load_metrics(db_conn, Scope(BRAND_B), parse_period("7"), now=clock.now())).kpis
    assert b7.useful_pct == 0.0 and b7.has_ratings


async def test_cache_de_60s_serve_o_mesmo_valor(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, brain_user: int, clock: FakeClock
) -> None:
    await _clean(db_conn)
    at = clock.now() - timedelta(hours=1)
    ids = await _answered(db_conn, brain_user, BRAND_A, at)
    await _rate(db_conn, brain_user, ids, "up", at)
    cache = MetricsCache(clock=lambda: 0.0)
    first = await metrics_snapshot(db_pool, ALL, P30, now=clock.now(), cache=cache)
    await db_conn.execute("DELETE FROM chat.answer_ratings")
    second = await metrics_snapshot(db_pool, ALL, P30, now=clock.now(), cache=cache)
    assert first is second and second.kpis.useful_pct == 100.0
