"""Custo so depois do acesso (CB-22): sem busca nem Gemini para quem nao e `active` e `allowed`.

Passa pelo pipeline real (`app.services.pipeline.Pipeline`) e, quando ele devolve um
`answer_request`, entrega-o ao `BrainProvider` de verdade com espioes na fonte e no transporte do
Gemini. Controle positivo: texto de ativo chega a busca (prova que os espioes enxergam).
SINTETICO; nenhuma rede.
"""
from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services import answering
from app.services.brain.faq_source import FaqSource
from app.services.brain.llm import LlmClient
from app.services.brain.provider import BrainProvider
from app.services.brain.source import Scored
from app.services.faq_reader import FaqReader
from app.services.flags import PAUSED_KEY, Flags
from app.services.inbound import ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.brain import factories
from tests.brain.factories import FakeGemini

pytestmark = [pytest.mark.security, pytest.mark.db]

PHONE = "5511910000001"
OTHER_PHONE = "5511977770000"
Q = "Qual o sabor do produto exemplo?"
MID_Q = "sabor produto exemplo alfa bravo"  # nota 0,75: vai a IA


class SpySource:
    """Conta as buscas e delega para a FAQ real."""

    name = "spy"

    def __init__(self, inner: FaqSource) -> None:
        self._inner = inner
        self.calls = 0

    async def search(
        self, query: str, *, brands: Sequence[str], limit: int, floor: float,
        conn: Any | None = None,
    ) -> list[Scored]:  # fmt: skip
        self.calls += 1
        return await self._inner.search(query, brands=brands, limit=limit, floor=floor, conn=conn)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "brain_price_in_per_mtok_usd": 1.0,
        "brain_price_out_per_mtok_usd": 2.0,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


class Env:
    def __init__(
        self, pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock,
        gemini: FakeGemini, user: int,
    ) -> None:  # fmt: skip
        self.pool, self.conn, self.clock, self.gemini, self.user = pool, conn, clock, gemini, user
        self.settings = _settings()
        self.flags = Flags(pool, clock)
        self.pipeline = Pipeline(pool, self.settings, self.flags, clock)
        self.source = SpySource(FaqSource(FaqReader(conn, clock=clock)))
        client = LlmClient(api_key="k", model="gemini-3.8-flash", transport=gemini.transport)
        self.provider = BrainProvider(
            self.source, self.flags, self.settings, clock, llm=client, db=conn
        )
        self._n = 0

    def message(self, text: str = Q, *, wa_id: str = PHONE, kind: ContentKind = ContentKind.TEXT,
                ) -> InboundMessage:  # fmt: skip
        self._n += 1
        return InboundMessage(
            f"wamid.ZC.{self._n}", Sender(wa_id=wa_id), kind,
            text=text if kind is ContentKind.TEXT else None, text_length=len(text),
        )

    async def run(self, msg: InboundMessage) -> bool:
        """Pipeline real + cerebro; True se o pipeline pediu resposta ao cerebro."""
        outcome = await self.pipeline.handle_message(msg)
        request = outcome.answer_request
        if request is None:
            return False
        assert isinstance(request, answering.AnswerRequest)
        await answering.answer_and_enqueue(self.pool, request, self.provider, clock=self.clock)
        return True

    def assert_zero_cost(self) -> None:
        assert self.source.calls == 0, "a busca foi chamada"
        assert self.gemini.requests == [], "o Gemini foi chamado"

    async def faq(self) -> str:
        key = " ".join(Q.lower().split())
        item_id = await self.conn.fetchval(
            "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
            "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
            "VALUES ($1, 'PRODUTO', $2, $3, 'Sabor tropical, 300 g.', true, 'seed', $4, 'teste', "
            "$4, 'teste') RETURNING id",
            factories.BRAND_A, Q, key, self.clock.now(),
        )  # fmt: skip
        return f"faq:{item_id}"

    async def set_status(self, status: str) -> None:
        await self.conn.execute(
            "UPDATE access.users SET status = $2, accepted_at = CASE WHEN $2 = 'active' THEN "
            "accepted_at END, accepted_notice_version = CASE WHEN $2 = 'active' THEN "
            "accepted_notice_version END, revoked_at = CASE WHEN $2 = 'revoked' THEN now() END, "
            "revoked_by = CASE WHEN $2 = 'revoked' THEN 'teste' END WHERE id = $1",
            self.user, status,
        )  # fmt: skip


@pytest.fixture
async def env(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock,
    gemini: FakeGemini, brain_user: int,
) -> Env:
    """A fabrica nao normaliza `phone_key`; o pipeline casa pela chave normalizada."""
    await db_conn.execute(
        "UPDATE access.users SET phone_key = $2 WHERE id = $1",
        brain_user, normalize_phone("+" + PHONE).match_key,
    )
    return Env(db_pool, db_conn, clock, gemini, brain_user)


async def test_controle_positivo_texto_de_ativo_chega_a_busca_e_a_ia(env: Env) -> None:
    ref = await env.faq()
    env.gemini.queue_text(
        json.dumps({"intent": "answer", "item_refs": [ref], "answer_text": "É tropical, 300 g."})
    )
    assert await env.run(env.message(MID_Q)) is True
    assert env.source.calls == 1 and len(env.gemini.requests) == 1


async def test_numero_desconhecido_nao_chama_busca_nem_gemini(env: Env) -> None:
    await env.faq()
    for i in range(3):
        assert await env.run(env.message(MID_Q, wa_id=f"{OTHER_PHONE[:-1]}{i}")) is False
    assert await env.conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0
    env.assert_zero_cost()


@pytest.mark.parametrize("status", ["revoked", "declined", "pending_optin"])
async def test_revogado_recusado_ou_sem_aceite_nao_chama_busca_nem_gemini(
    env: Env, status: str
) -> None:
    await env.faq()
    await env.set_status(status)
    for text in (MID_Q, Q, "sair"):
        assert await env.run(env.message(text)) is False
    env.assert_zero_cost()


async def test_acima_de_60_por_hora_nao_chama_busca_nem_gemini(env: Env) -> None:
    ref = await env.faq()
    assert env.settings.rate_limit_per_hour == 60  # o limite do PRD, inalterado
    for _ in range(60):  # as 60 primeiras passam pelo pipeline (resposta pelo cerebro)
        outcome = await env.pipeline.handle_message(env.message(Q))
        assert isinstance(outcome.answer_request, answering.AnswerRequest)
    assert env.source.calls == 0 and env.gemini.requests == []  # nao as entregamos ao cerebro
    for _ in range(3):
        assert await env.run(env.message(MID_Q)) is False  # 61a em diante: bloqueadas
    env.assert_zero_cost()
    assert ref  # item existia: so o limite impediu a busca


async def test_midia_de_ativo_nao_chega_ao_cerebro(env: Env) -> None:
    await env.faq()
    for kind in (ContentKind.AUDIO, ContentKind.IMAGE, ContentKind.DOCUMENT):
        assert await env.run(env.message(kind=kind)) is False
    env.assert_zero_cost()


async def test_pausado_nao_chama_busca_nem_gemini(env: Env) -> None:
    await env.faq()
    async with env.pool.acquire() as conn, conn.transaction():
        await env.flags.set_bool(conn, PAUSED_KEY, True, "teste")
    env.gemini.queue_text('{"intent": "no_answer", "item_refs": [], "answer_text": ""}')
    await env.run(env.message(MID_Q))
    env.assert_zero_cost()
