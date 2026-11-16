"""Marca da conversa no cerebro (spec canal-waha.1, WA-21, WA-22, WA-23; task WA-T21).

Caminho por palavras (sem IA), banco real. Itens SINTETICOS em duas marcas do cadastro.
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import BrainOutcome, ReplyKind
from app.services import brand_session
from app.services.answering import Answer, AnswerContext
from app.services.brain.effects import BrainEffects
from app.services.brain.faq_source import FaqSource
from app.services.brain.provider import BrainProvider
from app.services.brain.source import Scored
from app.services.faq_reader import FaqReader
from app.services.flags import Flags
from tests.brain import factories

pytestmark = pytest.mark.db

A = factories.BRAND_A  # plant_power
B = factories.BRAND_B  # a_tal_da_castanha
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
Q = "Quais sao os sabores do produto exemplo?"


class SpySource:
    name = "spy"

    def __init__(self, inner: FaqSource) -> None:
        self._inner = inner
        self.calls: list[list[str]] = []

    async def search(
        self, query: str, *, brands: Sequence[str], limit: int, floor: float, conn: Any = None
    ) -> list[Scored]:
        self.calls.append(list(brands))
        return await self._inner.search(query, brands=brands, limit=limit, floor=floor, conn=conn)


def _settings(**extra: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        channel_provider="waha",
        database_url="postgresql://u:p@localhost/x_test",
        jarvis_waha_api_key="chave",
        jarvis_waha_hmac_key="hmac",
        jarvis_whatsapp_number="+5500000000001",
        **extra,
    )


async def _faq(conn: asyncpg.Connection, brand: str, question: str, answer: str) -> None:
    key = " ".join(question.lower().split())
    await conn.execute(
        "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
        "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
        "VALUES ($1, 'PRODUTO', $2, $3, $4, true, 'seed', $5, 'teste', $5, 'teste')",
        brand, question, key, answer, NOW,
    )  # fmt: skip


class Env:
    def __init__(self, pool: asyncpg.Pool, conn: asyncpg.Connection, **extra: Any) -> None:
        self.pool, self.conn = pool, conn
        clock = FakeClock(NOW)
        self.source = SpySource(FaqSource(FaqReader(conn, clock=clock)))
        self.provider = BrainProvider(
            self.source, Flags(pool, clock), _settings(**extra), clock, db=pool
        )

    async def ask(
        self,
        text: str,
        user: int,
        *,
        brands: list[str] | None = None,
        at: datetime = NOW,
        poll_vote_id: str | None = None,
    ) -> Answer:
        ctx = AnswerContext(
            user_id=user, profile="vendedor", ufs=["SP"], channels=["farma"],
            brands=brands if brands is not None else [A, B], text=text,
            inbound_wa_message_id="wamid.WA-T21", received_at=at, trace_id=None,
            poll_vote_id=poll_vote_id,
        )  # fmt: skip
        return await self.provider.answer(ctx)

    async def brand(self, user: int) -> str | None:
        return (await brand_session.load(self.pool, user)).brand


@pytest.fixture
async def env(db_pool: asyncpg.Pool, db_conn: asyncpg.Connection) -> Env:
    await _faq(db_conn, A, Q, "Sabores do exemplo A: morango e uva.")
    await _faq(db_conn, B, Q, "Sabores do exemplo B: castanha e cacau.")
    return Env(db_pool, db_conn)


def _outcome(answer: Answer) -> str:
    assert isinstance(answer.effects, BrainEffects)
    return str(answer.effects.outcome)


async def test_sem_marca_e_duas_marcas_manda_enquete(env: Env, brain_user: int) -> None:
    ans = await env.ask(Q, brain_user)
    assert ans.kind == ReplyKind.BRAND_POLL.value
    assert ans.text == "Sobre qual marca é a sua pergunta?"
    assert ans.poll_brands == (A, B)
    assert _outcome(ans) == BrainOutcome.ASK_BRAND
    assert env.source.calls == []  # nao buscou antes de saber a marca


async def test_nome_da_marca_sem_pergunta_pendente_confirma(env: Env, brain_user: int) -> None:
    ans = await env.ask("Plant Power", brain_user)
    assert ans.kind == ReplyKind.BRAIN_NOTICE.value
    assert ans.text == "Certo, vamos falar de Plant Power. Pode perguntar!"
    assert await env.brand(brain_user) == A


async def test_marca_valida_busca_so_nela(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, B, NOW - timedelta(minutes=10))
    await env.ask(Q, brain_user)
    assert env.source.calls == [[B]]


async def test_marca_vencida_manda_enquete_de_novo(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, B, NOW - timedelta(minutes=31))
    ans = await env.ask(Q, brain_user)
    assert ans.kind == ReplyKind.BRAND_POLL.value


async def test_marca_valida_renova_a_janela(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, B, NOW - timedelta(minutes=20))
    await env.ask(Q, brain_user)
    state = await brand_session.load(env.pool, brain_user)
    assert state.at == NOW


async def test_citar_outra_marca_do_cadastro_troca(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, B, NOW)
    await env.ask("E na Plant Power, quais sao os sabores do produto exemplo?", brain_user)
    assert await env.brand(brain_user) == A
    assert env.source.calls == [[A]]


async def test_marca_fora_do_cadastro_segue_pedido_de_acesso(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, B, NOW)
    ans = await env.ask("E na Zaya, quais sao os sabores?", brain_user)
    assert _outcome(ans) == BrainOutcome.ACCESS_REQUEST
    assert await env.brand(brain_user) == B


async def test_trocar_marca_limpa_e_manda_enquete(env: Env, brain_user: int) -> None:
    await brand_session.set_brand(env.pool, brain_user, A, NOW)
    ans = await env.ask("Trocar marca", brain_user)
    assert ans.kind == ReplyKind.BRAND_POLL.value
    assert ans.text == "Sobre qual marca você quer falar?"
    assert await env.brand(brain_user) is None


async def test_uma_marca_so_nunca_tem_enquete(env: Env, brain_user: int) -> None:
    ans = await env.ask(Q, brain_user, brands=[A])
    assert ans.kind != ReplyKind.BRAND_POLL.value
    assert env.source.calls == [[A]] and await env.brand(brain_user) == A


async def test_voto_em_enquete_antiga_reenvia_a_enquete(env: Env, brain_user: int) -> None:
    ans = await env.ask("Plant Power", brain_user, poll_vote_id="true_x@c.us_ANTIGA")
    assert ans.kind == ReplyKind.BRAND_POLL.value
    assert await env.brand(brain_user) is None


async def test_desligado_com_zero_minutos(db_pool: asyncpg.Pool, db_conn: asyncpg.Connection,
                                          brain_user: int) -> None:  # fmt: skip
    await _faq(db_conn, A, Q, "Sabores do exemplo A: morango e uva.")
    env = Env(db_pool, db_conn, brand_session_minutes=0)
    ans = await env.ask(Q, brain_user)
    assert ans.kind != ReplyKind.BRAND_POLL.value
    assert env.source.calls == [[A, B]]


async def test_saudacao_nao_dispara_enquete(env: Env, brain_user: int) -> None:
    ans = await env.ask("Oi, bom dia!", brain_user)
    assert _outcome(ans) == BrainOutcome.GREETING and env.source.calls == []
