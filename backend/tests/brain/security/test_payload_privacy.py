"""Privacidade do payload ao Gemini (CB-13, spec 4.9): nada que identifique o vendedor sai.

Roda o `BrainProvider` de verdade (fonte = FaqSource no banco de teste) com o `LlmClient` real
sobre `httpx.MockTransport`; varre o corpo, a URL e os cabecalhos da requisicao capturada,
inclusive a janela de contexto. SINTETICO: nenhum dado real.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services.answering import Answer, AnswerContext
from app.services.brain.faq_source import FaqSource
from app.services.brain.llm import LlmClient
from app.services.brain.provider import BrainProvider
from app.services.faq_reader import FaqReader
from app.services.flags import Flags
from tests.brain import factories
from tests.brain.factories import FakeGemini

pytestmark = [pytest.mark.security, pytest.mark.db]

A = factories.BRAND_A
B = factories.BRAND_B
Q = "Qual o sabor do produto exemplo?"
MID_Q = "sabor produto exemplo alfa bravo"  # nota por palavras 0,75: faixa da IA
ITEM = "Sabor tropical, 300 g."

USER_NAME = "Vendedor Cerebro 1"
COMPANY = "Empresa Teste"
MANAGER = "Gestora Teste"
PHONE_KEY = "5511910000001"
WAMID_IN = "wamid.PRIVACIDADE.ENTRADA.0001"
WAMID_PRIOR = "wamid.PRIVACIDADE.ANTERIOR.0001"
BSUID = "BR.PRIV8472910365"
EMAIL = "vendedor.secreto@empresa-teste.com.br"

# Formas de escrever o MESMO telefone do vendedor (ele mesmo pode digitar o proprio numero).
PHONE_FORMS = (
    "+5511910000001",
    "5511910000001",
    "11910000001",
    "(11) 91000-0001",
    "11 91000-0001",
    "+55 11 91000-0001",
    "55 (11) 9 1000 0001",
    "11 9 1000 0001",
    "11.91000.0001",
)
# Trechos que nunca podem aparecer no corpo (comparados em minusculas, sem pontuacao de telefone).
_FORBIDDEN_TEXT = (USER_NAME, COMPANY, MANAGER, WAMID_IN, WAMID_PRIOR, BSUID, EMAIL)
_FORBIDDEN_KEYS = ("user_id", "phone", "bsuid", "wa_id", "wamid", "manager", "company")


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=SecretStr("postgresql://x"),
        whatsapp_app_secret=SecretStr("s"),
        whatsapp_verify_token=SecretStr("v"),
        whatsapp_access_token=SecretStr("t"),
        whatsapp_phone_number_id="1",
        brain_price_in_per_mtok_usd=1.0,
        brain_price_out_per_mtok_usd=2.0,
    )


def _reply(intent: str, refs: list[str], text: str = "") -> str:
    return json.dumps({"intent": intent, "item_refs": refs, "answer_text": text})


class Env:
    def __init__(
        self, pool: asyncpg.Pool, conn: asyncpg.Connection, clock: FakeClock,
        gemini: FakeGemini, user: int,
    ) -> None:  # fmt: skip
        self.pool, self.conn, self.clock, self.gemini, self.user = pool, conn, clock, gemini, user
        flags = Flags(pool, clock, team_group_initial=True)
        self.source = FaqSource(FaqReader(conn, clock=clock))
        client = LlmClient(
            api_key="k-secreta", model="gemini-3.8-flash", transport=gemini.transport
        )
        self.provider = BrainProvider(
            self.source, flags, _settings(), clock, llm=client, db=conn
        )

    async def faq(self) -> str:
        key = " ".join(Q.lower().split())
        item_id = await self.conn.fetchval(
            "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
            "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
            "VALUES ($1, 'PRODUTO', $2, $3, $4, true, 'seed', $5, 'teste', $5, 'teste') "
            "RETURNING id",
            A, Q, key, ITEM, self.clock.now(),
        )  # fmt: skip
        return f"faq:{item_id}"

    async def seed_exchange(
        self, question: str, answer: str, *, outcome: str = "answered",
        flags: list[str] | None = None, minutes_ago: int = 5, wamid: str = WAMID_PRIOR,
    ) -> int:  # fmt: skip
        """Troca anterior do vendedor (pergunta, rastro, brain_run e resposta enviada)."""
        trace = await factories.create_pending_trace(
            self.conn, self.user, self.clock.now(), text=question, wa_message_id=wamid
        )
        at = self.clock.now() - timedelta(minutes=minutes_ago)
        await self.conn.execute(
            "INSERT INTO chat.brain_runs (trace_id, outcome, path, flags, created_at) "
            "VALUES ($1, $2, 'ia', $3::text[], $4)",
            trace, outcome, flags or [], at,
        )  # fmt: skip
        await self.conn.execute(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
            "inbound_trace_id, status, next_attempt_at, created_at) "
            "SELECT $1, phone_e164, 'brain_answer', 'active', $2, $3, 'sent', $4, $4 "
            "FROM access.users WHERE id = $1",
            self.user, answer, trace, at,
        )  # fmt: skip
        await self.conn.execute(
            "UPDATE chat.message_trace SET received_at = $2 WHERE id = $1", trace, at
        )
        return trace

    async def ask(self, text: str) -> Answer:
        trace = await factories.create_pending_trace(
            self.conn, self.user, self.clock.now(), text=text, wa_message_id=WAMID_IN
        )
        ctx = AnswerContext(
            user_id=self.user, profile="vendedor", ufs=["SP"], channels=["farma"],
            brands=[A, B], text=text, inbound_wa_message_id=WAMID_IN,
            received_at=datetime(2026, 10, 28, 12, tzinfo=UTC), trace_id=trace,
        )  # fmt: skip
        return await self.provider.answer(ctx)


@pytest.fixture
async def env(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock,
    gemini: FakeGemini, brain_user: int,
) -> Env:
    await db_conn.execute(
        "UPDATE access.users SET name = $2, company = $3, manager_name = $4, bsuid = $5, "
        "phone_e164 = $6, phone_key = $7 WHERE id = $1",
        brain_user, USER_NAME, COMPANY, MANAGER, BSUID, "+" + PHONE_KEY, PHONE_KEY,
    )  # fmt: skip
    return Env(db_pool, db_conn, clock, gemini, brain_user)


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _assert_clean(env: Env) -> str:
    """Varre a requisicao capturada (corpo, URL, cabecalhos) e devolve o texto enviado."""
    assert len(env.gemini.requests) == 1, "a IA deveria ter sido chamada exatamente uma vez"
    request = env.gemini.requests[0]
    raw = request.content.decode()
    low = raw.lower()
    for forbidden in _FORBIDDEN_TEXT:
        assert forbidden.lower() not in low, f"vazou: {forbidden!r}"
    # Telefone em qualquer formato: tambem no corpo apenas com digitos (pega 1 000-0001 etc.).
    only_digits = _digits(raw)
    for needle in ("5511910000001", "11910000001", "910000001", "91000-0001"):
        assert _digits(needle) not in only_digits, f"vazou telefone: {needle}"
    for form in PHONE_FORMS:
        assert form not in raw, f"vazou telefone formatado: {form}"
    for key in _FORBIDDEN_KEYS:
        assert f'"{key}"' not in low
    # URL e cabecalhos: so a chave de API (cabecalho proprio) e metadados do transporte.
    meta = str(request.url) + " " + " ".join(f"{k}={v}" for k, v in request.headers.items())
    for forbidden in (*_FORBIDDEN_TEXT, PHONE_KEY):
        assert forbidden.lower() not in meta.lower()
    sent = json.loads(raw)["contents"][0]["parts"][0]["text"]
    assert isinstance(sent, str)
    return sent


async def test_pergunta_com_telefone_em_qualquer_formato_e_mascarada(env: Env) -> None:
    ref = await env.faq()
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    text = f"{MID_Q} meu contato " + " ou ".join(PHONE_FORMS) + f" e {EMAIL}"
    await env.ask(text)
    sent = _assert_clean(env)
    assert "[telefone]" in sent and "[email]" in sent


async def test_nada_do_cadastro_vai_no_corpo_mesmo_sem_pii_na_pergunta(env: Env) -> None:
    ref = await env.faq()
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    await env.ask(MID_Q)
    sent = _assert_clean(env)
    assert MID_Q in sent  # a pergunta vai (so o texto), o resto do cadastro nao
    body = json.loads(env.gemini.requests[0].content)
    assert set(body) == {"systemInstruction", "contents", "generationConfig"}


async def test_historico_com_telefone_e_email_vai_mascarado(env: Env) -> None:
    ref = await env.faq()
    await env.seed_exchange(
        f"{Q} meu fone {PHONE_FORMS[3]} e {PHONE_FORMS[0]}, email {EMAIL}",
        f"Resposta anterior sintetica. Fale com {PHONE_FORMS[2]} ou {EMAIL}.",
    )
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    fx_answer = await env.ask(MID_Q)
    sent = _assert_clean(env)
    assert "<contexto>" in sent and "<fala" in sent  # o historico realmente foi enviado
    assert "Resposta anterior sintetica" in sent
    assert "[telefone]" in sent and "[email]" in sent
    effects: Any = fx_answer.effects
    assert effects.context_turns == 1


@pytest.mark.parametrize("form", PHONE_FORMS)
async def test_cada_formato_de_telefone_no_historico_e_mascarado(env: Env, form: str) -> None:
    ref = await env.faq()
    await env.seed_exchange(f"{Q} ligue para {form}", f"Anotado: {form}.")
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    await env.ask(MID_Q)
    sent = _assert_clean(env)
    assert form not in sent


async def test_historico_de_outro_vendedor_nao_entra_na_janela(env: Env) -> None:
    ref = await env.faq()
    other = await factories.create_active_user(env.conn, env.clock.now(), seq=2)
    trace = await factories.create_pending_trace(
        env.conn, other, env.clock.now(), text=f"{Q} segredo-do-outro", wa_message_id="wamid.OUTRO"
    )
    await env.conn.execute(
        "INSERT INTO chat.brain_runs (trace_id, outcome, path, created_at) "
        "VALUES ($1, 'answered', 'ia', $2)", trace, env.clock.now() - timedelta(minutes=3),
    )  # fmt: skip
    await env.conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "inbound_trace_id, status, next_attempt_at, created_at) "
        "SELECT $1, phone_e164, 'brain_answer', 'active', 'resposta-do-outro', $2, 'sent', $3, $3 "
        "FROM access.users WHERE id = $1",
        other, trace, env.clock.now() - timedelta(minutes=3),
    )  # fmt: skip
    env.gemini.queue_text(_reply("answer", [ref], "É tropical, 300 g."))
    await env.ask(MID_Q)
    sent = _assert_clean(env)
    assert "segredo-do-outro" not in sent and "resposta-do-outro" not in sent
