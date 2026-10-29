"""Consultas de Metricas (PN-41, PN-43) contra Postgres real."""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.panel.services.metrics_read import (
    NO_RATINGS,
    MetricsCache,
    load_metrics,
    metrics_snapshot,
    parse_period,
)
from app.panel.services.scope import Scope
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B, PanelWorld

ALL = Scope(None)
P30 = parse_period("30")


async def _clean(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM chat.tickets")
    await conn.execute("DELETE FROM chat.message_trace")
    await conn.execute("DELETE FROM ops.security_counters")


_seq = {"n": 0}


async def _trace(
    conn: asyncpg.Connection,
    user_id: int | None,
    at: datetime,
    *,
    decision: str = "allowed",
    latency_ms: int | None = None,
) -> None:
    _seq["n"] += 1
    attempt_id = None
    if user_id is None:  # CHECK has_sender: desconhecido precisa de tentativa
        attempt_id = await conn.fetchval(
            "INSERT INTO access.access_attempts (phone_key, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type) "
            "VALUES ($1, 'unknown', 1, $2, $2, 'text') RETURNING id",
            f"5511988{_seq['n']:06d}",
            at,
        )
    await conn.execute(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "attempt_id, access_decision, decision, latency_ms) "
        "VALUES ($1, $2, 'text', $3, $4, $5, $5, $6)",
        f"wamid.test{_seq['n']}",
        at,
        user_id,
        attempt_id,
        decision,
        latency_ms,
    )


async def _ticket(
    conn: asyncpg.Connection, world: PanelWorld, n: int, brand: str | None, **kw: Any
) -> None:
    kw.setdefault("status", "answered_bot")
    kw.setdefault("created_at", world.now - timedelta(hours=n))
    await factories.insert_ticket(
        conn, number=800 + n, user_id=world.sellers.one_brand, brand=brand, **kw
    )


@pytest.mark.db
async def test_banco_vazio_sem_divisao_por_zero(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    await db_conn.execute("DELETE FROM access.users")
    snap = await load_metrics(db_conn, ALL, P30, now=world.now)
    k = snap.kpis
    assert snap.is_empty and snap.by_brand == []
    assert k.active_users_pct is None and k.tickets_change_pct is None
    assert k.resolved_without_escalation_pct is None
    assert k.useful_pct is None and k.useful_label == NO_RATINGS and not k.has_ratings
    assert snap.response.p50_seconds is None and snap.response.p95_seconds is None
    assert snap.response.human_median_seconds is None
    assert snap.response.p95_within_target is None
    assert len(snap.messages_per_day) == 30
    assert all(d.count == 0 for d in snap.messages_per_day)
    assert snap.blocks.total == 0


@pytest.mark.db
async def test_kpis_tickets_e_periodo_anterior(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    await _ticket(db_conn, world, 1, BRAND_A)
    await _ticket(db_conn, world, 2, BRAND_A, status="awaiting", escalated=True)
    await _ticket(db_conn, world, 3, BRAND_B)
    await _ticket(db_conn, world, 4, BRAND_B)
    await _ticket(db_conn, world, 5, BRAND_B, created_at=world.now - timedelta(days=40))
    await _ticket(db_conn, world, 6, BRAND_B, created_at=world.now - timedelta(days=100))
    snap = await load_metrics(db_conn, ALL, P30, now=world.now)
    k = snap.kpis
    assert (k.tickets, k.tickets_previous) == (4, 1)
    assert k.tickets_change_pct == 300.0
    assert k.resolved_without_escalation_pct == 75.0
    assert [(b.brand, b.tickets, b.pct) for b in snap.by_brand] == [
        (BRAND_B, 2, 50.0),
        (BRAND_A, 2, 50.0),
    ]  # empate: ordem alfabetica da marca
    only_b = await load_metrics(db_conn, Scope(BRAND_B), P30, now=world.now)
    assert only_b.kpis.tickets == 2 and [b.brand for b in only_b.by_brand] == [BRAND_B]


@pytest.mark.db
async def test_usuarios_ativos_sobre_cadastrados(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    await _trace(db_conn, world.sellers.one_brand, world.now - timedelta(hours=1))
    await _trace(db_conn, world.sellers.one_brand, world.now - timedelta(hours=2))
    await _trace(db_conn, world.sellers.two_brands, world.now - timedelta(days=45))
    snap = await load_metrics(db_conn, ALL, P30, now=world.now)
    assert snap.kpis.registered_users == 3
    assert snap.kpis.active_users == 1
    assert snap.kpis.active_users_pct == pytest.approx(33.3)
    b = await load_metrics(db_conn, Scope(BRAND_B), P30, now=world.now)
    assert b.kpis.registered_users == 1 and b.kpis.active_users == 0


@pytest.mark.db
async def test_mensagens_por_dia_em_sao_paulo_com_zeros(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    u = world.sellers.one_brand
    # now = 26/10 09:00 em SP. 02:30 UTC de 26/10 ainda e 25/10 (23:30 SP).
    await _trace(db_conn, u, datetime(2026, 10, 26, 2, 30, tzinfo=UTC))
    await _trace(db_conn, u, datetime(2026, 10, 26, 11, 0, tzinfo=UTC))
    await _trace(db_conn, u, datetime(2026, 10, 26, 11, 5, tzinfo=UTC))
    snap = await load_metrics(db_conn, ALL, parse_period("7"), now=world.now)
    counts = {d.day: d.count for d in snap.messages_per_day}
    assert len(counts) == 7 and snap.messages_per_day[-1].day == date(2026, 10, 26)
    assert counts[date(2026, 10, 25)] == 1 and counts[date(2026, 10, 26)] == 2
    assert counts[date(2026, 10, 24)] == 0
    hoje = await load_metrics(db_conn, ALL, parse_period("hoje"), now=world.now)
    assert [d.count for d in hoje.messages_per_day] == [2]
    assert hoje.since == datetime(2026, 10, 26, 3, 0, tzinfo=UTC)


@pytest.mark.db
async def test_tempo_de_resposta_p50_p95_e_equipe(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    u = world.sellers.one_brand
    for ms in (1000, 2000, 3000):
        await _trace(db_conn, u, world.now - timedelta(hours=1), latency_ms=ms)
    await _trace(db_conn, u, world.now - timedelta(hours=1))  # sem latencia: ignorada
    await _ticket(db_conn, world, 1, BRAND_A, status="answered_human", escalated=True)
    snap = await load_metrics(db_conn, ALL, P30, now=world.now)
    assert snap.response.p50_seconds == pytest.approx(2.0)
    assert snap.response.p95_seconds == pytest.approx(2.9)
    assert snap.response.p95_within_target is True
    assert snap.response.human_median_seconds == pytest.approx(300.0)


@pytest.mark.db
async def test_bloqueios_e_escopo(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    recent = world.now - timedelta(hours=2)
    await _trace(db_conn, None, recent, decision="unknown")
    await _trace(db_conn, None, recent, decision="unknown")
    await _trace(db_conn, world.sellers.one_brand, recent, decision="rate_limited")
    await _trace(db_conn, world.sellers.two_brands, recent, decision="rate_limited")
    await _trace(db_conn, None, world.now - timedelta(days=60), decision="unknown")
    await db_conn.execute(
        "INSERT INTO ops.security_counters (day, kind, count) VALUES ($1, 'invalid_signature', 3), "
        "($2, 'invalid_signature', 9)",
        date(2026, 10, 25),
        date(2026, 8, 1),
    )
    snap = await load_metrics(db_conn, ALL, P30, now=world.now)
    assert (snap.blocks.unknown_numbers, snap.blocks.rate_limited) == (2, 2)
    assert snap.blocks.invalid_signature == 3 and snap.blocks.total == 7
    b = await load_metrics(db_conn, Scope(BRAND_B), P30, now=world.now)
    assert (b.blocks.unknown_numbers, b.blocks.rate_limited) == (0, 1)
    assert b.blocks.invalid_signature is None


class _Conn:
    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FailPool:
    """Pool que falha se alguem pedir conexao: prova que o hit nao toca o banco."""

    def acquire(self) -> _Conn:
        raise AssertionError("cache hit nao deve pegar conexao")


@pytest.mark.db
async def test_cache_de_60s_por_marca_e_periodo(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    t = {"v": 0.0}
    cache = MetricsCache(clock=lambda: t["v"])
    first = await metrics_snapshot(db_pool, ALL, P30, now=world.now, cache=cache)
    assert first.kpis.tickets == 0
    await _ticket(db_conn, world, 1, BRAND_A)  # entra no banco, mas o cache segura 60 s
    t["v"] = 59.9
    assert await metrics_snapshot(_FailPool(), ALL, P30, now=world.now, cache=cache) is first
    p7 = parse_period("7")
    other_period = await metrics_snapshot(db_pool, ALL, p7, now=world.now, cache=cache)
    scoped = Scope(BRAND_A)
    other_brand = await metrics_snapshot(db_pool, scoped, P30, now=world.now, cache=cache)
    assert other_period.kpis.tickets == 1 and other_brand.kpis.tickets == 1
    t["v"] = 60.0
    fresh = await metrics_snapshot(db_pool, ALL, P30, now=world.now, cache=cache)
    assert fresh is not first and fresh.kpis.tickets == 1


def test_parse_period_invalido_vira_padrao() -> None:
    assert parse_period("hoje").days == 1
    assert parse_period("7").days == 7 and parse_period(7).key == "7"
    for bad in (None, "", "x", "14", "-1"):
        assert parse_period(bad).key == "30"
