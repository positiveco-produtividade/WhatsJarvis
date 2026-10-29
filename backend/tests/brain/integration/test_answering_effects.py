"""answering: partes, efeitos na transacao 2, ticket no rastro e desfazer tudo (CB-7/11/18/24)."""
import json
from dataclasses import dataclass
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import rate_down_payload, rate_up_payload
from app.services.answering import Answer, AnswerContext, AppliedEffects, answer_and_enqueue
from tests.integration.test_answering import NOW, SpyAnswerProvider, make_request

pytestmark = pytest.mark.db


@dataclass
class FakeEffects:
    rate: bool = True
    calls: int = 0
    seen_outbound: int = -1

    async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects:
        self.calls += 1
        self.seen_outbound = await conn.fetchval("SELECT count(*) FROM chat.outbound_messages")
        number = await conn.fetchval("SELECT 'J-' || lpad(nextval('ops.ticket_seq')::text, 4, '0')")
        ticket_id = await conn.fetchval(
            "INSERT INTO chat.tickets (ticket_number, user_id, trace_id, kind, status, "
            "created_at, updated_at) VALUES ($1, $2, $3, 'question', 'awaiting', $4, $4) "
            "RETURNING id",
            number, user_id, trace_id, NOW,
        )
        return AppliedEffects(ticket_id=ticket_id, ticket_number=number, rate=self.rate)


async def _outbound(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM chat.outbound_messages ORDER BY id")


async def test_answer_curto_continua_valido(db_pool: asyncpg.Pool) -> None:
    assert Answer("k", "t").parts == () and Answer("k", "t").effects is None
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    spy = SpyAnswerProvider(answer=Answer("brain_answer", "um"))
    ids = await answer_and_enqueue(db_pool, request, spy, clock=FakeClock(NOW))
    rows = await _outbound(db_pool)
    assert ids == tuple(r["id"] for r in rows) and [r["body"] for r in rows] == ["um"]
    assert rows[0]["reply_buttons"] is None
    assert spy.contexts[0].trace_id == request.trace_id
    assert isinstance(spy.contexts[0], AnswerContext)


async def test_partes_na_ordem_e_botoes_so_na_ultima(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    effects = FakeEffects()
    answer = Answer("brain_answer_rate", "ignorado", parts=("a", "b", "c"), effects=effects)
    ids = await answer_and_enqueue(
        db_pool, request, SpyAnswerProvider(answer=answer), clock=FakeClock(NOW)
    )
    rows = await _outbound(db_pool)
    assert len(ids) == 3 and list(ids) == [r["id"] for r in rows]
    assert [r["body"] for r in rows] == ["a", "b", "c"]
    assert rows[0]["reply_buttons"] is None and rows[1]["reply_buttons"] is None
    async with db_pool.acquire() as conn:
        ticket_id, number = await conn.fetchval(
            "SELECT ARRAY[id::text, ticket_number] FROM chat.tickets"
        )
        trace_number = await conn.fetchval(
            "SELECT ticket_number FROM chat.message_trace WHERE id = $1", request.trace_id
        )
    buttons = json.loads(rows[2]["reply_buttons"])
    assert [b["id"] for b in buttons] == [
        rate_up_payload(int(ticket_id)), rate_down_payload(int(ticket_id))
    ]
    titles = ["\U0001F44D \u00datil", "\U0001F44E N\u00e3o \u00fatil"]
    assert [b["title"] for b in buttons] == titles
    assert effects.seen_outbound == 0  # efeitos rodam antes de enfileirar
    assert trace_number == number


async def test_sem_rate_nao_ha_botoes(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    answer = Answer("brain_answer", "x", parts=("a", "b"), effects=FakeEffects(rate=False))
    await answer_and_enqueue(db_pool, request, SpyAnswerProvider(answer=answer))
    assert all(r["reply_buttons"] is None for r in await _outbound(db_pool))


async def test_reexecutar_desfaz_ticket_e_saidas(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
        await conn.execute(
            "UPDATE chat.message_trace SET reply_kind = 'none' WHERE id = $1", request.trace_id
        )
    effects = FakeEffects()
    answer = Answer("brain_answer_rate", "x", parts=("a", "b"), effects=effects)
    with pytest.raises(InvalidValue):
        await answer_and_enqueue(db_pool, request, SpyAnswerProvider(answer=answer))
    assert effects.calls == 1
    assert await _outbound(db_pool) == []
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM chat.tickets") == 0


async def test_rate_sem_ticket_falha_alto_e_desfaz(db_pool: asyncpg.Pool) -> None:
    class NoTicket:
        async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects:
            return AppliedEffects(rate=True)

    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    answer = Answer("brain_answer_rate", "x", parts=("a", "b"), effects=NoTicket())
    with pytest.raises(InvalidValue):
        await answer_and_enqueue(db_pool, request, SpyAnswerProvider(answer=answer))
    assert await _outbound(db_pool) == []


async def test_provedor_que_falha_segue_pending_sem_efeitos(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    spy = SpyAnswerProvider(error=RuntimeError("boom"))
    assert await answer_and_enqueue(db_pool, request, spy) == ()
    async with db_pool.acquire() as conn:
        kind = await conn.fetchval(
            "SELECT reply_kind FROM chat.message_trace WHERE id = $1", request.trace_id
        )
        assert await conn.fetchval("SELECT count(*) FROM chat.tickets") == 0
    assert kind == "pending"
