"""Latencia simulada e rastro do cerebro (CB-20, CB-23, spec 4.10 e NFR). SINTETICO.

O tempo da IA e virtual: o relogio monotonico do provedor soma o atraso do Gemini falso sem
dormir de verdade, entao a suite continua rapida e o p95 e deterministico.
"""
from __future__ import annotations

import json
import time as real_time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import BrainOutcome
from app.services.answering import Answer, AnswerContext
from app.services.brain import provider as provider_module
from app.services.brain.effects import BrainEffects
from app.services.brain.faq_source import FaqSource
from app.services.brain.llm import LlmClient, LlmError, LlmRequest, LlmResult
from app.services.brain.provider import BrainProvider
from app.services.faq_reader import FaqReader
from app.services.flags import Flags
from tests.brain import factories
from tests.brain.factories import FakeGemini

pytestmark = pytest.mark.db

A = factories.BRAND_A
Q = "Qual o sabor do produto exemplo?"
MID_Q = "sabor produto exemplo alfa bravo"  # nota 0,75: faixa da IA
ITEM = "Sabor tropical, 300 g."
GEMINI_DELAY_S = 2.0
RUNS = 20
P95_LIMIT_MS = 8000
WORDS_LIMIT_MS = 1000


class VirtualTime:
    """`time.monotonic` real somado a um deslocamento virtual (o atraso do Gemini falso)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def monotonic(self) -> float:
        return real_time.monotonic() + self.offset


class DelayedLlm:
    """Cliente real com atraso virtual de `delay` s antes de cada resposta."""

    def __init__(self, inner: LlmClient, vt: VirtualTime, delay: float) -> None:
        self._inner, self._vt, self._delay = inner, vt, delay

    async def complete(self, request: LlmRequest) -> LlmResult:
        self._vt.offset += self._delay
        return await self._inner.complete(request)


class BrokenLlm:
    async def complete(self, request: LlmRequest) -> LlmResult:
        raise LlmError("timeout")


@pytest.fixture
def vtime(monkeypatch: pytest.MonkeyPatch) -> VirtualTime:
    vt = VirtualTime()
    monkeypatch.setattr(provider_module, "time", SimpleNamespace(monotonic=vt.monotonic))
    return vt


class Env:
    def __init__(
        self, pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock,
        gemini: FakeGemini, user: int,
    ) -> None:  # fmt: skip
        self.conn, self.clock, self.gemini, self.user = conn, clock, gemini, user
        self.flags = Flags(pool, clock, team_group_initial=True)
        self.source = FaqSource(FaqReader(conn, clock=clock))
        self.n = 0

    def provider(self, llm: Any = None) -> BrainProvider:
        settings = Settings(  # type: ignore[call-arg]
            brain_price_in_per_mtok_usd=1.0, brain_price_out_per_mtok_usd=2.0
        )
        return BrainProvider(
            self.source, self.flags, settings, self.clock, llm=llm, db=self.conn
        )

    def real_llm(self, vt: VirtualTime, delay: float = GEMINI_DELAY_S) -> DelayedLlm:
        inner = LlmClient(api_key="k", model="gemini-3.8-flash", transport=self.gemini.transport)
        return DelayedLlm(inner, vt, delay)

    async def faq(self, question: str, answer: str) -> str:
        item_id = await self.conn.fetchval(
            "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
            "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
            "VALUES ($1, 'PRODUTO', $2, $3, $4, true, 'seed', $5, 'teste', $5, 'teste') "
            "RETURNING id",
            A, question, " ".join(question.lower().split()), answer, self.clock.now(),
        )  # fmt: skip
        return f"faq:{item_id}"

    async def ask(self, provider: BrainProvider, text: str) -> BrainEffects:
        self.n += 1
        ctx = AnswerContext(
            user_id=self.user, profile="vendedor", ufs=["SP"], channels=["farma"],
            brands=[A, factories.BRAND_B], text=text, inbound_wa_message_id=f"wamid.lt.{self.n}",
            received_at=datetime(2026, 10, 28, 12, tzinfo=UTC), trace_id=None,
        )  # fmt: skip
        answer: Answer = await provider.answer(ctx)
        assert isinstance(answer.effects, BrainEffects)
        return answer.effects

    async def persist(self, fx: BrainEffects, text: str = Q) -> asyncpg.Record:
        """Aplica os efeitos (grava `brain_runs`) e devolve a linha."""
        self.n += 1
        trace = await factories.create_pending_trace(
            self.conn, self.user, self.clock.now(), text=text, wa_message_id=f"wamid.lp.{self.n}"
        )
        await fx.apply(self.conn, trace, self.user)
        row = await self.conn.fetchrow("SELECT * FROM chat.brain_runs WHERE trace_id = $1", trace)
        assert row is not None
        return row


@pytest.fixture
def env(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock,
    gemini: FakeGemini, brain_user: int,
) -> Env:
    return Env(db_pool, db_conn, clock, gemini, brain_user)


def p95(values: list[int]) -> int:
    """Percentil 95 por posto mais proximo."""
    ordered = sorted(values)
    return ordered[max(0, -(-95 * len(ordered) // 100) - 1)]


def _reply(ref: str) -> str:
    return json.dumps({"intent": "answer", "item_refs": [ref], "answer_text": "É tropical, 300 g."})


async def test_p95_com_gemini_de_2s_fica_abaixo_de_8s(env: Env, vtime: VirtualTime) -> None:
    ref = await env.faq(Q, ITEM)
    provider = env.provider(env.real_llm(vtime))
    totals: list[int] = []
    for _ in range(RUNS):
        env.gemini.queue_text(_reply(ref))
        vtime.offset = 0.0  # o inicio de cada mensagem usa o relogio real: zera o atraso virtual
        fx = await env.ask(provider, MID_Q)
        assert fx.path == "ia" and fx.outcome == BrainOutcome.ANSWERED
        assert fx.timings_ms["ai"] >= GEMINI_DELAY_S * 1000 - 100
        totals.append(fx.timings_ms["total"])
    assert len(env.gemini.requests) == RUNS
    assert p95(totals) <= P95_LIMIT_MS
    assert min(totals) >= GEMINI_DELAY_S * 1000 - 100


async def test_caminho_por_palavras_fica_abaixo_de_1s(env: Env) -> None:
    await env.faq(Q, ITEM)
    for _ in range(5):
        fx = await env.ask(env.provider(), Q)
        assert fx.path == "palavras" and fx.llm_calls == 0
        assert fx.timings_ms["total"] <= WORDS_LIMIT_MS
    assert env.gemini.requests == []


async def test_falha_da_ia_ainda_responde_dentro_do_p95(env: Env, vtime: VirtualTime) -> None:
    """Gemini lento e depois falha: cai para palavras sem estourar o limite."""
    await env.faq(Q, ITEM)

    class SlowBroken:
        async def complete(self, request: LlmRequest) -> LlmResult:
            vtime.offset += GEMINI_DELAY_S
            raise LlmError("timeout")

    fx = await env.ask(env.provider(SlowBroken()), MID_Q)
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "palavras"
    assert fx.timings_ms["total"] <= P95_LIMIT_MS


async def test_rastro_da_ia_preenche_todos_os_campos(env: Env, vtime: VirtualTime) -> None:
    ref = await env.faq(Q, ITEM)
    env.gemini.queue_text(_reply(ref), prompt_tokens=1000, output_tokens=500)
    fx = await env.ask(env.provider(env.real_llm(vtime)), MID_Q)
    row = await env.persist(fx)
    assert row["outcome"] == "answered" and row["path"] == "ia" and row["brand"] == A
    assert row["item_refs"] == [f"{ref}@v1"]
    assert 0.30 <= float(row["best_score"]) < 0.85
    assert (row["llm_calls"], row["tokens_in"], row["tokens_out"]) == (1, 1000, 500)
    assert float(row["cost_usd"]) == pytest.approx(0.002)
    assert row["fidelity"] == "ok" and row["prompt_version"] == "v1"
    assert row["model"] == "gemini-3.8-flash" and row["ticket_id"] is not None
    timings = json.loads(row["timings_ms"])
    assert timings["ai"] >= GEMINI_DELAY_S * 1000 - 100 and timings["total"] >= timings["ai"]
    assert row["created_at"] is not None


async def test_rastro_por_palavras_e_flags_de_falha(env: Env) -> None:
    await env.faq(Q, ITEM)
    direct = await env.persist(await env.ask(env.provider(), Q))
    assert direct["outcome"] == "answered" and direct["path"] == "palavras"
    assert direct["llm_calls"] == 0 and direct["tokens_in"] == 0 and direct["model"] is None
    assert float(direct["best_score"]) >= 0.85 and json.loads(direct["timings_ms"])["total"] >= 0
    failed = await env.persist(await env.ask(env.provider(BrokenLlm()), MID_Q))
    assert failed["path"] == "palavras" and "ai_failed" in failed["flags"]
    assert failed["llm_calls"] == 1 and failed["item_refs"]


async def test_rastro_sem_resposta_registra_desfecho_e_caminho(env: Env) -> None:
    fx = await env.ask(env.provider(), "assunto totalmente desconhecido zzz")
    row = await env.persist(fx)
    assert row["outcome"] == "no_answer" and row["item_refs"] == []
    assert row["path"] in {"palavras", "nenhum"} and row["llm_calls"] == 0


async def test_rastro_nao_guarda_texto_de_pergunta_nem_resposta(
    env: Env, vtime: VirtualTime
) -> None:
    ref = await env.faq(Q, ITEM)
    env.gemini.queue_text(_reply(ref))
    row = await env.persist(await env.ask(env.provider(env.real_llm(vtime)), MID_Q), text=MID_Q)
    dump = json.dumps({k: str(v) for k, v in dict(row).items()}, ensure_ascii=False)
    for secret in (MID_Q, Q, ITEM, "É tropical"):
        assert secret not in dump
