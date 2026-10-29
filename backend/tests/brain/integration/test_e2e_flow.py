"""Cerebro ponta a ponta (CB-T32): webhook assinado -> pipeline -> cerebro -> saida despachada.

App real via `create_app` (provedor `BrainProvider` ligado por settings), `FakeChannel` e Gemini
falso (`httpx.MockTransport` injetado em `LlmClient.from_settings`). Postgres de teste real.
Cobre CB-1, CB-6, CB-7, CB-24, CB-25, CB-26, CB-29. SINTETICO: nenhum dado licenciado nem real.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.clock import FakeClock
from app.core.security import compute_hub_signature
from app.panel.services.metrics_read import DEFAULT_CACHE
from app.services.brain.llm import LlmClient
from app.services.brain.provider import BrainProvider
from app.services.flags import PAUSED_KEY, brand_key
from app.services.phone import normalize_phone
from tests.brain import factories
from tests.brain.factories import FakeGemini
from tests.fakes import FakeChannel, SentCall
from tests.panel.fakes import FakeIam
from tests.panel.integration.test_access import ADMIN
from tests.panel.integration.test_access import Env as PanelEnv
from tests.panel.integration.test_access import login as panel_login

pytestmark = pytest.mark.db

BASE = "http://localhost"
SECRET = "app-secret-e2e"
VERIFY = "verify-token-e2e"
PHONE_ID = "100000000000001"
USER_PHONE = "551199990001"
URL = "/webhook/whatsapp"
FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "meta"

A = factories.BRAND_A
B = factories.BRAND_B
Q = "Qual o sabor do produto exemplo?"
MID_Q = "sabor produto exemplo alfa bravo"  # nota 0,75: faixa da IA (se nao estivesse pausado)
LONG_ANSWER = " ".join(f"Frase numero {i} do argumento sintetico completo." for i in range(40))
SHORT_ANSWER = "Sabor tropical, 300 g."


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": os.environ["TEST_DATABASE_URL"],
        "whatsapp_app_secret": SECRET,
        "whatsapp_verify_token": VERIFY,
        "whatsapp_access_token": "access-token-e2e",
        "whatsapp_phone_number_id": PHONE_ID,
        "resend_api_key": "re_fake_key",
        "resend_from_email": "jarvis@example.test",
        "alert_recipient_email": "equipe@example.test",
        "brain_enabled": True,
        "gemini_api_key": "key-sintetica",
        "brain_price_in_per_mtok_usd": 0.1,
        "brain_price_out_per_mtok_usd": 0.4,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return copy.deepcopy(data)


def text_payload(wamid: str, body: str) -> bytes:
    data = _fixture("text")
    msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["id"], msg["text"]["body"] = wamid, body
    return json.dumps(data).encode()


def tap_payload(wamid: str, button_id: str) -> bytes:
    data = _fixture("button_interactive")
    msg = data["entry"][0]["changes"][0]["value"]["messages"][0]
    msg["id"] = wamid
    msg["interactive"]["button_reply"]["id"] = button_id
    return json.dumps(data).encode()


@dataclass
class Env:
    app: FastAPI
    client: TestClient
    channel: FakeChannel
    gemini: FakeGemini
    pool: asyncpg.Pool
    settings: Settings

    def post(self, raw: bytes) -> httpx.Response:
        sig = compute_hub_signature(raw, SECRET)
        return self.client.post(URL, content=raw, headers={"X-Hub-Signature-256": sig})

    def ask(self, wamid: str, body: str) -> None:
        assert self.post(text_payload(wamid, body)).status_code == 200

    def tap(self, wamid: str, button_id: str) -> None:
        assert self.post(tap_payload(wamid, button_id)).status_code == 200

    async def settle(self, expected_calls: int, timeout: float = 8.0) -> None:
        """Espera as saidas chegarem ao canal e a fila sair de pending/sending."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            busy = await self.pool.fetchval(
                "SELECT count(*) FROM chat.outbound_messages WHERE status IN ('pending','sending')"
            )
            if len(self.channel.calls) >= expected_calls and busy == 0:
                await asyncio.sleep(0.15)  # folga para nada extra aparecer
                assert len(self.channel.calls) == expected_calls
                return
            await asyncio.sleep(0.05)
        raise AssertionError(
            f"esperava {expected_calls} envios, houve {len(self.channel.calls)}"
        )

    async def wait_for(self, check: Callable[[], Any], timeout: float = 8.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await check():
                return
            await asyncio.sleep(0.05)
        raise AssertionError("condicao nao atingida no prazo")

    async def tickets(self, **where: Any) -> list[asyncpg.Record]:
        clauses = " AND ".join(f"{col} = ${i}" for i, col in enumerate(where, start=1))
        sql = "SELECT * FROM chat.tickets" + (f" WHERE {clauses}" if clauses else "")
        return await self.pool.fetch(sql + " ORDER BY id", *where.values())

    async def add_faq(self, brand: str, question: str, answer: str) -> None:
        key = " ".join(question.lower().split())
        await self.pool.execute(
            "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
            "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
            "VALUES ($1, 'PRODUTO', $2, $3, $4, true, 'seed', now(), 'teste', now(), 'teste')",
            brand, question, key, answer,
        )  # fmt: skip


@pytest.fixture
async def env(
    db_pool: asyncpg.Pool, gemini: FakeGemini, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Env]:
    original = LlmClient.from_settings

    def _from_settings(
        settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> LlmClient:
        return original(settings, transport=gemini.transport)

    monkeypatch.setattr(LlmClient, "from_settings", staticmethod(_from_settings))
    num = normalize_phone("+" + USER_PHONE)
    now = datetime(2026, 10, 28, 12, tzinfo=UTC)
    await db_pool.execute(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
        "brands, manager_name, status, status_changed_at, created_at, created_by, updated_at, "
        "accepted_at, accepted_notice_version) VALUES ('Ana', $1, $2, 'vendedor', 'Acme', "
        "ARRAY['SP'], ARRAY['farma'], $3, 'Gestora', 'active', $4, $4, 'teste', $4, $4, 'v1')",
        num.phone_e164, num.match_key, [A, B], now,
    )  # fmt: skip
    channel = FakeChannel()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"id": "email-1"}))
    )
    cfg = make_settings()
    app = main.create_app(cfg, channel=channel, http_client=http)
    client = TestClient(app, base_url=BASE)
    client.__enter__()
    try:
        assert type(app.state.processor._provider) is BrainProvider
        yield Env(app, client, channel, gemini, db_pool, cfg)
    finally:
        client.__exit__(None, None, None)


def _ids(call: SentCall) -> list[str]:
    return [b.id for b in call.buttons]


# ------------------------------------------------ pergunta coberta (CB-1, CB-6, CB-24)


async def test_pergunta_coberta_varias_partes_em_ordem_botoes_so_na_ultima(env: Env) -> None:
    await env.add_faq(A, Q, LONG_ANSWER)
    env.ask("wamid.E2E.Q1", Q)
    await env.wait_for(lambda: _calls_at_least(env, 3))
    await env.settle(len(env.channel.calls))
    calls = env.channel.calls
    assert len(calls) >= 3
    assert [c.method for c in calls[:-1]] == ["send_text"] * (len(calls) - 1)
    assert all(c.buttons == () for c in calls[:-1])
    last = calls[-1]
    assert last.method == "send_reply_buttons" and len(last.buttons) == 2
    ticket = (await env.tickets(kind="question"))[0]
    assert _ids(last) == [f"RATE_UP:{ticket['id']}", f"RATE_DOWN:{ticket['id']}"]
    joined = "\n".join(c.body or "" for c in calls)
    assert joined.index("Frase numero 0 ") < joined.index("Frase numero 20 ")
    assert joined.index("Frase numero 20 ") < joined.index("Frase numero 39 ")
    assert (last.body or "").rstrip().endswith("— Léo · Plant Power")
    assert ticket["escalated"] is False
    assert env.gemini.requests == []  # resposta direta: sem IA
    run = await env.pool.fetchrow("SELECT outcome, path FROM chat.brain_runs")
    assert run is not None and (run["outcome"], run["path"]) == ("answered", "palavras")


async def _calls_at_least(env: Env, n: int) -> bool:
    return len(env.channel.calls) >= n


async def _answered(env: Env, wamid: str = "wamid.E2E.Q") -> asyncpg.Record:
    """Pergunta curta respondida; devolve o ticket da pergunta."""
    before = len(env.channel.calls)
    env.ask(wamid, Q)
    await env.wait_for(lambda: _calls_at_least(env, before + 1))
    await env.settle(before + 1)
    ticket = (await env.tickets(kind="question"))[-1]
    assert env.channel.calls[-1].method == "send_reply_buttons"
    return ticket


# ---------------------------------------------------------------- avaliacao (CB-25, CB-26, CB-29)


async def test_toque_positivo_grava_sem_responder(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    ticket = await _answered(env)
    sent = len(env.channel.calls)
    env.tap("wamid.E2E.UP", f"RATE_UP:{ticket['id']}")
    await env.wait_for(lambda: _rated(env, 1))
    await env.settle(sent)  # nada novo ao canal
    row = await env.pool.fetchrow("SELECT ticket_id, rating FROM chat.answer_ratings")
    assert row is not None and (row["ticket_id"], row["rating"]) == (ticket["id"], "up")
    assert await env.tickets(escalation_reason="rating_down") == []


async def _rated(env: Env, n: int) -> bool:
    return await env.pool.fetchval("SELECT count(*) FROM chat.answer_ratings") == n


async def test_toque_negativo_agradece_e_abre_escalado(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    ticket = await _answered(env)
    sent = len(env.channel.calls)
    env.tap("wamid.E2E.DN", f"RATE_DOWN:{ticket['id']}")
    await env.settle(sent + 1)
    thanks = env.channel.calls[-1]
    assert thanks.method == "send_text" and thanks.body == env.settings.text_brain_rating_thanks
    down = await env.tickets(escalation_reason="rating_down")
    assert len(down) == 1 and down[0]["escalated"] is True
    assert down[0]["id"] != ticket["id"]
    rating = await env.pool.fetchrow("SELECT rating, down_ticket_id FROM chat.answer_ratings")
    assert rating is not None and rating["rating"] == "down"
    assert rating["down_ticket_id"] == down[0]["id"]


async def test_toque_repetido_nao_duplica(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    ticket = await _answered(env)
    sent = len(env.channel.calls)
    env.tap("wamid.E2E.DN1", f"RATE_DOWN:{ticket['id']}")
    await env.settle(sent + 1)
    env.tap("wamid.E2E.DN2", f"RATE_DOWN:{ticket['id']}")  # outro toque, outro wamid
    env.post(tap_payload("wamid.E2E.DN1", f"RATE_DOWN:{ticket['id']}"))  # reentrega do mesmo
    env.tap("wamid.E2E.UP3", f"RATE_UP:{ticket['id']}")  # tentar virar a nota
    await asyncio.sleep(0.5)
    await env.settle(sent + 1)
    assert await env.pool.fetchval("SELECT count(*) FROM chat.answer_ratings") == 1
    assert len(await env.tickets(escalation_reason="rating_down")) == 1
    assert await env.pool.fetchval(
        "SELECT count(*) FROM chat.outbound_messages WHERE kind = 'brain_notice'"
    ) == 1


async def test_kpi_avaliados_como_uteis_na_pagina_metricas(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    first = await _answered(env, "wamid.E2E.K1")
    second = await _answered(env, "wamid.E2E.K2")
    sent = len(env.channel.calls)
    env.tap("wamid.E2E.KU", f"RATE_UP:{first['id']}")
    env.tap("wamid.E2E.KD", f"RATE_DOWN:{second['id']}")
    await env.wait_for(lambda: _rated(env, 2))
    await env.settle(sent + 1)  # so o agradecimento da negativa
    DEFAULT_CACHE.clear()
    panel = PanelEnv(os.environ["TEST_DATABASE_URL"], FakeIam(email=ADMIN),
                     FakeClock(datetime.now(UTC)))  # fmt: skip
    with panel.client() as pc:
        panel_login(pc)
        page = pc.get("/dashboard/metrics")
    DEFAULT_CACHE.clear()
    assert page.status_code == 200
    assert "Avaliados como úteis" in page.text
    assert "50%" in page.text  # 1 positivo em 2 avaliacoes


# ---------------------------------------------------------------- escalada (CB-7)


async def test_nao_sei_cria_ticket_escalado(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    env.ask("wamid.E2E.NS", "xyzzy plugh quux")
    await env.wait_for(lambda: _calls_at_least(env, 1))
    await env.settle(1)
    call = env.channel.calls[0]
    assert call.method == "send_text" and call.buttons == ()
    assert call.body == env.settings.text_brain_no_answer
    [ticket] = await env.tickets(kind="question")
    assert ticket["escalated"] is True and ticket["escalation_reason"] == "no_match"
    assert await env.pool.fetchval("SELECT count(*) FROM chat.answer_ratings") == 0
    assert env.gemini.requests == []


async def test_marca_sem_acesso_pede_acesso_sem_duplicar(env: Env) -> None:
    await env.add_faq("zaya", "Qual o sabor do produto zaya?", "Segredo zaya.")
    env.ask("wamid.E2E.AC1", "Qual o sabor do produto da Zaya?")
    await env.wait_for(lambda: _calls_at_least(env, 1))
    await env.settle(1)
    env.ask("wamid.E2E.AC2", "Qual o sabor do produto da Zaya?")
    await env.wait_for(lambda: _calls_at_least(env, 2))
    await env.settle(2)
    for call in env.channel.calls:
        assert call.body == env.settings.text_brain_access_request
        assert "Segredo" not in (call.body or "") and call.buttons == ()
    requests = await env.tickets(escalation_reason="brand_access_request")
    assert len(requests) == 1 and requests[0]["brand"] == "zaya"
    assert requests[0]["escalated"] is True
    assert await env.pool.fetchval("SELECT count(*) FROM chat.tickets") == 1


async def test_marca_desligada_escala(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    async with env.pool.acquire() as conn, conn.transaction():
        await env.app.state.flags.set_bool(conn, brand_key(A), False, "teste")
    env.ask("wamid.E2E.BD", "Qual o sabor do produto plant power?")
    await env.wait_for(lambda: _calls_at_least(env, 1))
    await env.settle(1)
    assert env.channel.calls[0].body == env.settings.text_brain_no_answer
    [ticket] = await env.tickets(kind="question")
    assert ticket["escalated"] is True and ticket["escalation_reason"] == "brand_disabled"
    assert env.gemini.requests == []


# ---------------------------------------------------------------- pausa (CB-22 / R4)


async def test_pausa_silencia_resposta_e_agradecimento_sem_gemini_nem_busca(env: Env) -> None:
    await env.add_faq(A, Q, SHORT_ANSWER)
    ticket = await _answered(env)  # antes da pausa: resposta normal
    sent = len(env.channel.calls)
    async with env.pool.acquire() as conn, conn.transaction():
        await env.app.state.flags.set_bool(conn, PAUSED_KEY, True, "teste", "cli")

    searches: list[str] = []
    source = env.app.state.processor._provider._source
    inner = source.search

    async def counting(*args: Any, **kwargs: Any) -> Any:
        searches.append("search")
        return await inner(*args, **kwargs)

    source.search = counting  # espia a busca (pausado nao pode buscar)

    env.ask("wamid.E2E.PZ", MID_Q)  # sem pausa iria a IA (nota 0,75)
    env.tap("wamid.E2E.PZ.DN", f"RATE_DOWN:{ticket['id']}")

    async def all_discarded() -> bool:
        n = await env.pool.fetchval(
            "SELECT count(*) FROM chat.outbound_messages WHERE status = 'discarded'"
        )
        return bool(n == 2)  # a resposta pausada e o agradecimento

    await env.wait_for(all_discarded)
    await asyncio.sleep(0.3)
    assert len(env.channel.calls) == sent  # nada novo chegou ao vendedor
    discarded = await env.pool.fetch(
        "SELECT kind, discard_reason FROM chat.outbound_messages WHERE status = 'discarded'"
    )
    assert {r["discard_reason"] for r in discarded} == {"paused"}
    assert env.gemini.requests == [] and searches == []
    paused = await env.tickets(escalation_reason="paused")
    assert len(paused) == 1 and paused[0]["escalated"] is True
    run = await env.pool.fetchrow(
        "SELECT llm_calls, cost_usd FROM chat.brain_runs ORDER BY id DESC LIMIT 1"
    )
    assert run is not None and run["llm_calls"] == 0 and float(run["cost_usd"]) == 0.0
