"""Tickets de pergunta do cerebro contra o Postgres de teste (CB-6, CB-7, CB-4). SINTETICO."""
from __future__ import annotations

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.values import BrainOutcome
from app.services.flags import Flags
from app.services.question_tickets import (
    create_access_request_ticket,
    create_question_ticket,
    find_open_access_request,
)
from tests.brain import factories

pytestmark = pytest.mark.db

_TRACE_N = iter(range(1, 10_000))


async def _trace(conn: asyncpg.Connection, user: int, clock: FakeClock) -> int:
    return await factories.create_pending_trace(
        conn, user, clock.now(), wa_message_id=f"wamid.qt.{next(_TRACE_N)}"
    )


async def _ticket(conn: asyncpg.Connection, ticket_id: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", ticket_id)
    assert row is not None
    return row


async def _deliveries(conn: asyncpg.Connection, ticket_id: int) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM ops.deliveries WHERE source_type = 'ticket' AND source_id = $1", ticket_id
    )


async def test_respondida_nao_escalada(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock, team_group_initial=True)
    trace = await _trace(db_conn, brain_user, clock)
    t = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="Qual o sabor?", flags=flags,
        clock=clock, classification="PRODUTO", brand=factories.BRAND_A,
        summary="Sabor sintetico A.", confidence=0.91, meta={"refs": [1, 2], "prompt": "v1"},
    )
    row = await _ticket(db_conn, t.id)
    assert t.created and t.ticket_number == f"J-{int(t.ticket_number[2:]):04d}"
    assert row["kind"] == "question" and row["status"] == "answered_bot"
    assert row["escalated"] is False and row["urgency"] is None
    assert row["message"] == "Qual o sabor?" and row["summary"] == "Sabor sintetico A."
    assert row["classification"] == "PRODUTO" and float(row["confidence"]) == 0.91
    deliveries = await _deliveries(db_conn, t.id)
    assert [(d["channel"], d["status"]) for d in deliveries] == [("group", "pending")]


async def test_numeros_da_sequencia_crescem(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock)
    nums = []
    for _ in range(2):
        trace = await _trace(db_conn, brain_user, clock)
        t = await create_question_ticket(
            db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock
        )
        nums.append(int(t.ticket_number[2:]))
    assert nums[1] == nums[0] + 1


async def test_chave_do_grupo_desligada_vira_panel_only(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock, team_group_initial=False)
    trace = await _trace(db_conn, brain_user, clock)
    t = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock
    )
    deliveries = await _deliveries(db_conn, t.id)
    assert [(d["channel"], d["status"]) for d in deliveries] == [("group", "panel_only")]
    assert await db_conn.fetchval(
        "SELECT count(*) FROM ops.deliveries WHERE channel = 'email'"
    ) == 0


@pytest.mark.parametrize(
    ("classification", "outcome", "expected"),
    [
        ("PRECO", BrainOutcome.NO_ANSWER, "urgente"),
        ("PRODUTO", BrainOutcome.NO_ANSWER, "pouco_urgente"),
        ("PRODUTO", BrainOutcome.BRAND_DISABLED, "pouco_urgente"),
    ],
)
async def test_escalado_tem_urgencia(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int,
    classification: str, outcome: BrainOutcome, expected: str,
) -> None:
    flags = Flags(db_pool, clock)
    trace = await _trace(db_conn, brain_user, clock)
    t = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock,
        classification=classification, escalated=True, escalation_reason="no_match",
        outcome=outcome,
    )
    row = await _ticket(db_conn, t.id)
    assert row["status"] == "awaiting" and row["escalated"] is True
    assert row["urgency"] == expected and row["escalation_reason"] == "no_match"


async def test_urgencia_explicita_do_downvote(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock)
    trace = await _trace(db_conn, brain_user, clock)
    t = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock,
        classification="PRECO", escalated=True, escalation_reason="rating_down",
        urgency="pouco_urgente", summary="resposta dada",
    )
    assert (await _ticket(db_conn, t.id))["urgency"] == "pouco_urgente"


async def test_mesmo_rastro_nao_duplica(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock)
    trace = await _trace(db_conn, brain_user, clock)
    first = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock
    )
    again = await create_question_ticket(
        db_conn, user_id=brain_user, trace_id=trace, message="p", flags=flags, clock=clock
    )
    assert again.id == first.id and again.created is False
    assert len(await _deliveries(db_conn, first.id)) == 1
    with pytest.raises(asyncpg.UniqueViolationError):  # indice 202 como defesa em profundidade
        await db_conn.execute(
            "INSERT INTO chat.tickets (ticket_number, user_id, trace_id, kind, status, "
            "created_at, updated_at) VALUES ('J-X', $1, $2, 'question', 'awaiting', now(), now())",
            brain_user, trace,
        )


async def test_pedido_de_acesso_e_dedup(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, brain_user: int
) -> None:
    flags = Flags(db_pool, clock)
    assert await find_open_access_request(db_conn, brain_user, "zaya") is None
    t1 = await create_access_request_ticket(
        db_conn, user_id=brain_user, trace_id=await _trace(db_conn, brain_user, clock),
        message="quero Zaya", brand="zaya", brand_label="Zaya", flags=flags, clock=clock,
    )
    row = await _ticket(db_conn, t1.id)
    assert row["status"] == "awaiting" and row["urgency"] == "urgente" and row["escalated"]
    assert row["classification"] == "NAO_IDENTIFICADO" and row["brand"] == "zaya"
    assert row["escalation_reason"] == "brand_access_request"
    assert row["summary"] == "Pedido de acesso à marca Zaya"
    found = await find_open_access_request(db_conn, brain_user, "zaya")
    assert found is not None and found.id == t1.id
    t2 = await create_access_request_ticket(
        db_conn, user_id=brain_user, trace_id=await _trace(db_conn, brain_user, clock),
        message="de novo", brand="zaya", brand_label="Zaya", flags=flags, clock=clock,
    )
    assert t2.id == t1.id and t2.created is False
    assert await find_open_access_request(db_conn, brain_user, "jungle") is None
    # respondido: deixa de ser "aberto"
    await db_conn.execute("UPDATE chat.tickets SET status = 'answered_human' WHERE id = $1", t1.id)
    assert await find_open_access_request(db_conn, brain_user, "zaya") is None
