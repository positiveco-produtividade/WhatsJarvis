"""BrainProvider com IA (CB-T28): chamada unica, fidelidade, memoria, custo, reserva. SINTETICO."""
from __future__ import annotations

import asyncio
import itertools
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import BrainOutcome
from app.services.answering import Answer, AnswerContext
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
B = factories.BRAND_B
Q = "Qual o sabor do produto exemplo?"
MID_Q = "sabor produto exemplo alfa bravo"  # nota por palavras = 0,75 (faixa da IA)
PRICE_Q = "Qual o preço do produto exemplo alfa bravo?"
ITEM = "Sabor tropical, 300 g."
_N = itertools.count(1)
_INSERT_OUT = (
    "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
    "inbound_trace_id, status, next_attempt_at, created_at) "
    "SELECT $1, phone_e164, 'brain_answer', 'active', $2, $3, 'sent', $4, $4 "
    "FROM access.users WHERE id = $1"
)


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "brain_price_in_per_mtok_usd": 1.0,
        "brain_price_out_per_mtok_usd": 2.0,
    }
    base.update(over)
    return Settings(**base)


async def _faq(conn: asyncpg.Connection, clock: FakeClock, question: str, answer: str) -> str:
    key = " ".join(question.lower().split())
    item_id = await conn.fetchval(
        "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
        "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
        "VALUES ($1, 'PRODUTO', $2, $3, $4, true, 'seed', $5, 'teste', $5, 'teste') "
        "RETURNING id",
        A, question, key, answer, clock.now(),
    )  # fmt: skip
    return f"faq:{item_id}"


def _reply(intent: str, refs: list[str], text: str = "") -> str:
    return json.dumps({"intent": intent, "item_refs": refs, "answer_text": text})


class SlowLlm:
    """Cliente real com latencia simulada antes da resposta."""

    def __init__(self, inner: LlmClient, delay: float) -> None:
        self._inner, self._delay = inner, delay

    async def complete(self, request: LlmRequest) -> LlmResult:
        await asyncio.sleep(self._delay)
        return await self._inner.complete(request)


class BrokenLlm:
    async def complete(self, request: LlmRequest) -> LlmResult:
        raise LlmError("timeout")


class Env:
    def __init__(
        self, pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock,
        gemini: FakeGemini, user: int,
    ) -> None:  # fmt: skip
        self.pool, self.conn, self.clock, self.gemini, self.user = pool, conn, clock, gemini, user
        self.flags = Flags(pool, clock, team_group_initial=True)
        self.source = FaqSource(FaqReader(conn, clock=clock))

    def provider(self, settings: Settings | None = None, llm: Any = None) -> BrainProvider:
        cfg = settings or _settings()
        client = llm or LlmClient(
            api_key="k", model="gemini-3.8-flash", transport=self.gemini.transport
        )
        return BrainProvider(self.source, self.flags, cfg, self.clock, llm=client, db=self.conn)

    async def ask(
        self, provider: BrainProvider, text: str, *, trace: int | None = None
    ) -> Answer:
        ctx = AnswerContext(
            user_id=self.user, profile="vendedor", ufs=["SP"], channels=["farma"],
            brands=[A, B], text=text, inbound_wa_message_id="wamid.T28",
            received_at=datetime(2026, 10, 28, 12, tzinfo=UTC), trace_id=trace,
        )  # fmt: skip
        return await provider.answer(ctx)

    async def trace(self, text: str = Q) -> int:
        return await factories.create_pending_trace(
            self.conn, self.user, self.clock.now(), text=text,
            wa_message_id=f"wamid.t28.{next(_N)}",
        )  # fmt: skip


@pytest.fixture
def env(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock, gemini: FakeGemini,
    brain_user: int,
) -> Env:
    return Env(db_pool, db_conn, clock, gemini, brain_user)


def _fx(answer: Answer) -> BrainEffects:
    assert isinstance(answer.effects, BrainEffects)
    return answer.effects


async def test_ia_reescreve_texto_fundamentado_e_grava_custo(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(
        _reply("answer", [ref], "É tropical, 300 g."), prompt_tokens=1000, output_tokens=500
    )
    ans = await env.ask(env.provider(), MID_Q)
    fx = _fx(ans)
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "ia" and fx.fidelity == "ok"
    assert "É tropical, 300 g." in ans.parts[-1] and ITEM not in ans.parts[-1]
    assert (fx.llm_calls, fx.tokens_in, fx.tokens_out) == (1, 1000, 500)
    assert fx.cost_usd == pytest.approx(0.002) and fx.model == "gemini-3.8-flash"
    assert fx.prompt_version == "v1" and fx.item_refs == [f"{ref}@v1"]
    assert 0.30 <= (fx.best_score or 0) < 0.85 and fx.timings_ms["ai"] >= 0
    assert len(env.gemini.requests) == 1


async def test_numero_inventado_vira_texto_do_item(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 500 g."))
    ans = await env.ask(env.provider(), MID_Q)
    fx = _fx(ans)
    assert fx.outcome == BrainOutcome.ANSWERED and fx.fidelity == "repaired"
    assert ITEM in ans.parts[-1] and "500" not in ans.parts[-1]


async def test_preco_inventado_sem_preco_no_item_e_nao_sei(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, "Qual o preço do produto exemplo?", ITEM)
    env.gemini.queue_text(_reply("answer", [ref], "Custa R$ 9,90."))
    fx = _fx(await env.ask(env.provider(), PRICE_Q))
    assert fx.outcome == BrainOutcome.NO_ANSWER and fx.reason == "no_price_in_source"
    assert fx.classification == "PRECO" and fx.fidelity == "failed" and fx.llm_calls == 1


async def test_preco_inventado_com_preco_no_item_usa_texto_do_item(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, "Qual o preço do produto exemplo?", "Custa R$ 12,50.")
    env.gemini.queue_text(_reply("answer", [ref], "Custa R$ 9,90."))
    ans = await env.ask(env.provider(), PRICE_Q)
    assert _fx(ans).fidelity == "repaired" and "R$ 12,50" in ans.parts[-1]
    assert "9,90" not in ans.parts[-1]


async def test_modo_verbatim_so_escolhe_e_devolve_o_item(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("answer", [ref], "texto livre que deve ser ignorado"))
    ans = await env.ask(env.provider(_settings(brain_answer_mode="verbatim")), MID_Q)
    assert ITEM in ans.parts[-1] and "ignorado" not in ans.parts[-1]
    system = env.gemini.last_json()["systemInstruction"]["parts"][0]["text"]
    assert "Nao escreva" in system
    assert _fx(ans).fidelity == "ok" and _fx(ans).path == "ia"


async def test_ia_diz_no_answer_escala_sem_ressuscitar_por_palavras(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("no_answer", []))
    fx = _fx(await env.ask(env.provider(), MID_Q))
    assert fx.outcome == BrainOutcome.NO_ANSWER and fx.reason == "no_match"
    assert fx.llm_calls == 1 and fx.tokens_in == 100


async def test_ia_diz_ask_brand_e_out_of_scope(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("ask_brand", []))
    env.gemini.queue_text(_reply("out_of_scope", []))
    assert _fx(await env.ask(env.provider(), MID_Q)).outcome == BrainOutcome.ASK_BRAND
    assert _fx(await env.ask(env.provider(), MID_Q)).outcome == BrainOutcome.REFUSAL


async def test_nota_direta_nao_chama_a_ia(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    fx = _fx(await env.ask(env.provider(), Q))
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "palavras" and fx.llm_calls == 0
    assert env.gemini.requests == [] and fx.model is None


async def test_falha_http_cai_para_palavras_e_alerta_deduplicado(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    for _ in range(2):
        env.gemini.queue_status(500)
    t1, t2 = await env.trace(), await env.trace()
    first = _fx(await env.ask(env.provider(), MID_Q, trace=t1))
    assert first.outcome == BrainOutcome.ANSWERED and first.path == "palavras"
    assert first.llm_calls == 1 and "ai_failed" in first.run_flags
    assert len(env.gemini.requests) == 1
    await first.apply(env.conn, t1, env.user)
    second = _fx(await env.ask(env.provider(), MID_Q, trace=t2))
    await second.apply(env.conn, t2, env.user)
    rows = await env.conn.fetch("SELECT * FROM ops.alerts WHERE kind = 'brain_api_failure'")
    assert len(rows) == 1


@pytest.mark.parametrize(
    "body",
    ["isto nao e json", json.dumps({"intent": "xyz"}), _reply("answer", ["faq:999999"], "x")],
)
async def test_saida_invalida_cai_para_palavras_com_falha(env: Env, body: str) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(body)
    fx = _fx(await env.ask(env.provider(), MID_Q))
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "palavras"
    assert "ai_failed" in fx.run_flags and fx.llm_calls == 1


async def test_falha_com_nota_baixa_escala(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    provider = env.provider(llm=BrokenLlm())
    fx = _fx(await env.ask(provider, "sabor produto exemplo alfa bravo charlie delta echo"))
    assert fx.outcome == BrainOutcome.NO_ANSWER and fx.reason == "no_match"
    assert "ai_failed" in fx.run_flags


async def test_orcamento_estourado_pula_a_ia(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    other = await env.trace()
    await env.conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, outcome, path, cost_usd, created_at) "
        "VALUES ($1, 'answered', 'ia', 100, $2)",
        other, env.clock.now(),
    )  # fmt: skip
    fx = _fx(await env.ask(env.provider(), MID_Q))
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "palavras"
    assert env.gemini.requests == [] and "budget_blocked" in fx.run_flags and fx.llm_calls == 0


async def test_followup_usa_janela_e_soma_tokens_da_ultima_pergunta(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    prior = await env.trace(Q)
    at = env.clock.now() - timedelta(minutes=5)
    await env.conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, outcome, path, created_at) "
        "VALUES ($1, 'answered', 'ia', $2)",
        prior, at,
    )  # fmt: skip
    await env.conn.execute(_INSERT_OUT, env.user, "Resposta anterior sintetica.", prior, at)
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    ans = await env.ask(env.provider(), "e a cor?", trace=await env.trace("e a cor?"))
    fx = _fx(ans)
    assert fx.outcome == BrainOutcome.ANSWERED and fx.path == "ia" and fx.context_turns == 1
    assert "follow_up" in fx.run_flags
    sent = env.gemini.last_json()["contents"][0]["parts"][0]["text"]
    assert Q in sent and "Resposta anterior sintetica." in sent and "e a cor?" in sent


async def test_sem_janela_pergunta_curta_nao_vira_followup(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("no_answer", []))
    fx = _fx(await env.ask(env.provider(), "sabor?", trace=await env.trace("sabor?")))
    assert "follow_up" not in fx.run_flags and fx.context_turns == 0


async def test_nada_que_identifique_o_vendedor_vai_ao_gemini(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    phone = await env.conn.fetchval("SELECT phone_e164 FROM access.users WHERE id = $1", env.user)
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    await env.ask(env.provider(), f"{MID_Q} meu fone {phone} e 11999998888")
    raw = env.gemini.requests[-1].content.decode()
    assert phone not in raw and "11999998888" not in raw and "[telefone]" in raw
    assert "user_id" not in raw and "wamid" not in raw


async def test_latencia_simulada_de_2s_cabe_em_8s(env: Env) -> None:
    ref = await _faq(env.conn, env.clock, Q, ITEM)
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    inner = LlmClient(api_key="k", model="gemini-3.8-flash", transport=env.gemini.transport)
    fx = _fx(await env.ask(env.provider(llm=SlowLlm(inner, 2.0)), MID_Q))
    assert fx.timings_ms["ai"] >= 1900 and fx.timings_ms["total"] < 8000
    assert fx.path == "ia"


async def test_sem_cliente_nunca_chama_a_ia(env: Env) -> None:
    await _faq(env.conn, env.clock, Q, ITEM)
    provider = BrainProvider(env.source, env.flags, _settings(), env.clock)
    fx = _fx(await env.ask(provider, MID_Q))
    assert fx.outcome == BrainOutcome.ANSWERED and fx.llm_calls == 0 and fx.path == "palavras"
