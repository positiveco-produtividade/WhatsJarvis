"""answering: provedor padrao, contexto completo, falha sem perder o rastro (AC-30/31)."""
import dataclasses
import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services.answering import (
    Answer,
    AnswerContext,
    AnswerRequest,
    UnderConstructionProvider,
    answer_and_enqueue,
)
from tests.integration.test_schema_access_chat import add_trace, add_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
PHONE = "+5511987654321"
SECRET_TEXT = "qual o preco do produto sigiloso"


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr("postgresql://x"),
        whatsapp_app_secret=SecretStr("s"),
        whatsapp_verify_token=SecretStr("v"),
        whatsapp_access_token=SecretStr("t"),
        whatsapp_phone_number_id="1",
    )


class SpyAnswerProvider:
    def __init__(self, answer: Answer | None = None, error: Exception | None = None) -> None:
        self.contexts: list[AnswerContext] = []
        self._answer = answer or Answer("under_construction", "resposta do espiao")
        self._error = error

    async def answer(self, ctx: AnswerContext) -> Answer:
        self.contexts.append(ctx)
        if self._error is not None:
            raise self._error
        return self._answer


async def make_request(conn: asyncpg.Connection) -> AnswerRequest:
    user_id = await add_user(
        conn,
        status="active",
        accepted_at=NOW,
        accepted_notice_version="v1",
        profile="vendedor",
        ufs=["SP", "RJ"],
        channels=["varejo", "farma"],
        brands=["jungle"],
    )
    trace_id = await add_trace(conn, user_id=user_id, reply_kind="pending", body=SECRET_TEXT)
    ctx = AnswerContext(
        user_id=user_id,
        profile="vendedor",
        ufs=["SP", "RJ"],
        channels=["varejo", "farma"],
        brands=["jungle"],
        text=SECRET_TEXT,
        inbound_wa_message_id="wamid.1",
        received_at=NOW,
    )
    return AnswerRequest(trace_id=trace_id, phone_e164=PHONE, context=ctx)


async def test_provedor_padrao_enfileira_texto_fixo(db_pool: asyncpg.Pool) -> None:
    settings = make_settings()
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    ids = await answer_and_enqueue(
        db_pool, request, UnderConstructionProvider(settings), clock=FakeClock(NOW)
    )
    assert len(ids) == 1
    outbound_id = ids[0]
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", outbound_id)
        reply_kind = await conn.fetchval(
            "SELECT reply_kind FROM chat.message_trace WHERE id = $1", request.trace_id
        )
    assert row is not None
    assert row["kind"] == "under_construction"
    assert row["body"] == settings.text_under_construction
    assert row["required_status"] == "active"
    assert row["status"] == "pending"
    assert row["to_phone"] == PHONE
    assert row["user_id"] == request.context.user_id
    assert row["inbound_trace_id"] == request.trace_id
    assert reply_kind == "under_construction"


async def test_espiao_recebe_contexto_completo(db_pool: asyncpg.Pool) -> None:
    spy = SpyAnswerProvider()
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    await answer_and_enqueue(db_pool, request, spy)
    ctx = spy.contexts[0]
    assert spy.contexts == [dataclasses.replace(request.context, trace_id=request.trace_id)]
    assert (ctx.profile, ctx.ufs, ctx.channels, ctx.brands) == (
        "vendedor",
        ["SP", "RJ"],
        ["varejo", "farma"],
        ["jungle"],
    )
    assert ctx.text == SECRET_TEXT and ctx.inbound_wa_message_id == "wamid.1"


async def test_provedor_nao_roda_dentro_de_transacao(db_pool: asyncpg.Pool) -> None:
    """O provedor pode demorar: nenhuma conexao do pool fica presa durante a chamada."""
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    in_use: list[int] = []

    class Probe:
        async def answer(self, ctx: AnswerContext) -> Answer:
            in_use.append(db_pool.get_size() - db_pool.get_idle_size())
            return Answer("under_construction", "x")

    await answer_and_enqueue(db_pool, request, Probe())
    assert in_use == [0]


async def test_falha_do_provedor_nao_perde_o_rastro(
    db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    spy = SpyAnswerProvider(error=RuntimeError(f"falhou com {SECRET_TEXT} {PHONE}"))
    with caplog.at_level(logging.DEBUG):
        result = await answer_and_enqueue(db_pool, request, spy)
    assert result == ()
    async with db_pool.acquire() as conn:
        trace = await conn.fetchrow(
            "SELECT reply_kind, decision FROM chat.message_trace WHERE id = $1", request.trace_id
        )
        count = await conn.fetchval("SELECT count(*) FROM chat.outbound_messages")
    assert trace is not None
    assert (trace["reply_kind"], trace["decision"]) == ("pending", "allowed")
    assert count == 0
    assert "RuntimeError" in caplog.text
    assert SECRET_TEXT not in caplog.text and PHONE not in caplog.text


async def test_kind_invalido_nao_grava_nada(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    spy = SpyAnswerProvider(answer=Answer("promo", "x"))
    with pytest.raises(InvalidValue):
        await answer_and_enqueue(db_pool, request, spy)
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


async def test_rastro_nao_pendente_desfaz_a_saida(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
        await conn.execute(
            "UPDATE chat.message_trace SET reply_kind = 'none' WHERE id = $1", request.trace_id
        )
    with pytest.raises(InvalidValue):
        await answer_and_enqueue(db_pool, request, SpyAnswerProvider())
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


async def test_provedor_padrao_ignora_contexto(db_pool: asyncpg.Pool) -> None:
    settings = make_settings()
    async with db_pool.acquire() as conn:
        request = await make_request(conn)
    answer: Any = await UnderConstructionProvider(settings).answer(request.context)
    assert answer == Answer("under_construction", settings.text_under_construction)
