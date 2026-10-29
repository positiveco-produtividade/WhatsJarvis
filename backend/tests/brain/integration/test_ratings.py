"""Avaliacao positiva/negativa contra o Postgres de teste (CB-25, CB-29). SINTETICO."""
from __future__ import annotations

import itertools
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import rate_down_payload, rate_up_payload
from app.services.flags import Flags
from app.services.question_tickets import create_question_ticket
from app.services.ratings import RatingResult, handle_rating
from tests.brain import factories

pytestmark = pytest.mark.db

_N = itertools.count(1)


def make_settings() -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _trace(conn: asyncpg.Connection, user: int, clock: FakeClock) -> int:
    return await factories.create_pending_trace(
        conn, user, clock.now(), wa_message_id=f"wamid.rt.{next(_N)}"
    )


async def _answered(
    db_pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int
) -> int:
    """Ticket respondido pelo bot, com duas partes de resposta ja enfileiradas."""
    trace = await _trace(conn, user, clock)
    t = await create_question_ticket(
        conn, user_id=user, trace_id=trace, message="Qual o sabor?", flags=Flags(db_pool, clock),
        clock=clock, classification="PRODUTO", brand=factories.BRAND_A, meta={"refs": [7]},
    )
    for i, kind in enumerate(("brain_answer", "brain_answer_rate")):
        await conn.execute(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
            "inbound_trace_id, status, attempts, next_attempt_at, created_at) "
            "SELECT id, phone_e164, $2, 'active', $3, $4, 'sent', 1, $5, $5 "
            "FROM access.users WHERE id = $1",
            user, kind, f"Parte {i + 1}.", trace, clock.now(),
        )
    return t.id


async def _rate(
    db_pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock, user: int, payload: str
) -> RatingResult:
    tap = await _trace(conn, user, clock)
    return await handle_rating(
        conn, user_id=user, payload=payload, trace_id=tap, clock=clock,
        settings=make_settings(), flags=Flags(db_pool, clock),
    )


async def _count(conn: asyncpg.Connection, sql: str) -> int:
    return int(await conn.fetchval(sql))


async def test_up_so_grava(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await _answered(db_pool, db_conn, clock, brain_user)
    before = await _count(db_conn, "SELECT count(*) FROM chat.tickets")
    res = await _rate(db_pool, db_conn, clock, brain_user, rate_up_payload(tid))
    assert (res.recorded, res.useful, res.outbound_ids) == (True, True, ())
    row = await db_conn.fetchrow("SELECT * FROM chat.answer_ratings WHERE ticket_id = $1", tid)
    assert row is not None and row["rating"] == "up" and row["down_ticket_id"] is None
    assert await _count(db_conn, "SELECT count(*) FROM chat.tickets") == before


async def test_down_cria_ticket_escalado_e_agradece(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    tid = await _answered(db_pool, db_conn, clock, brain_user)
    res = await _rate(db_pool, db_conn, clock, brain_user, rate_down_payload(tid))
    assert res.recorded and res.useful is False and len(res.outbound_ids) == 1
    rating = await db_conn.fetchrow("SELECT * FROM chat.answer_ratings WHERE ticket_id = $1", tid)
    assert rating is not None and rating["rating"] == "down"
    t = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", rating["down_ticket_id"])
    assert t is not None
    assert t["kind"] == "question" and t["status"] == "awaiting" and t["escalated"] is True
    assert t["escalation_reason"] == "rating_down" and t["urgency"] == "pouco_urgente"
    assert t["classification"] == "PRODUTO" and t["brand"] == factories.BRAND_A
    assert t["message"] == "Qual o sabor?" and t["summary"] == "Parte 1.\n\nParte 2."
    assert "rated_ticket_number" in t["meta"]
    out = await db_conn.fetchrow(
        "SELECT * FROM chat.outbound_messages WHERE id = $1", res.outbound_ids[0]
    )
    assert out is not None and out["kind"] == "brain_notice" and out["status"] == "pending"
    assert out["body"] == "Obrigado, avisei a equipe para melhorar essa resposta."
    deliveries = await db_conn.fetch(
        "SELECT channel FROM ops.deliveries WHERE source_type = 'ticket' AND source_id = $1",
        t["id"],
    )
    assert [d["channel"] for d in deliveries] == ["group"]  # sem e-mail


@pytest.mark.parametrize("first_up", [True, False])
async def test_repeticao_e_botao_oposto_nao_fazem_nada(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int,
    first_up: bool,
) -> None:
    tid = await _answered(db_pool, db_conn, clock, brain_user)
    first, other = (
        (rate_up_payload, rate_down_payload) if first_up else (rate_down_payload, rate_up_payload)
    )
    await _rate(db_pool, db_conn, clock, brain_user, first(tid))
    tickets = await _count(db_conn, "SELECT count(*) FROM chat.tickets")
    outs = await _count(db_conn, "SELECT count(*) FROM chat.outbound_messages")
    for payload in (first(tid), other(tid)):
        res = await _rate(db_pool, db_conn, clock, brain_user, payload)
        assert (res.recorded, res.useful, res.outbound_ids) == (False, None, ())
    assert await _count(db_conn, "SELECT count(*) FROM chat.answer_ratings") == 1
    assert await _count(db_conn, "SELECT count(*) FROM chat.tickets") == tickets
    assert await _count(db_conn, "SELECT count(*) FROM chat.outbound_messages") == outs


async def test_ticket_de_outro_vendedor_inexistente_ou_adulterado(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    other = await factories.create_active_user(db_conn, clock.now(), seq=2)
    tid = await _answered(db_pool, db_conn, clock, other)
    for payload in (
        rate_down_payload(tid), rate_up_payload(tid + 10_000), "RATE_UP:abc", "RATE_DOWN:",
        "RATE_UP:-1", "OPTIN_ACCEPT:1", "",
    ):
        res = await _rate(db_pool, db_conn, clock, brain_user, payload)
        assert (res.recorded, res.useful, res.outbound_ids) == (False, None, ())
    assert await _count(db_conn, "SELECT count(*) FROM chat.answer_ratings") == 0
    assert await _count(
        db_conn, "SELECT count(*) FROM chat.tickets WHERE escalation_reason = 'rating_down'"
    ) == 0
