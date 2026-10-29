"""API do assistente (PN-T56; PN-50, PN-53, limites 12/min e 4.000 caracteres, PN-4)."""

# ruff: noqa: F811
from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.db.connection import create_pool
from app.panel.assistant.disabled import DISABLED_REASON, DisabledProvider
from app.panel.assistant.provider import (
    ChatTurn,
    ProviderError,
    ProviderReply,
    ToolCaller,
    ToolSpec,
)
from app.panel.assistant.service import AssistantService
from app.panel.assistant.tools import AssistantTools
from app.panel.auth.iam import IamClient
from app.panel.routes import mount_panel
from tests.panel.fakes import IAM_BASE_URL
from tests.panel.integration.test_access import (
    ORIGIN,
    Env,
    env,  # noqa: F401
    login,
    make_settings,
    post_headers,
    probe,
    sql,
)

pytestmark = pytest.mark.db

CHAT = "/api/assistant/chat"
STATUS = "/api/assistant/status"
COUNT = "SELECT count(*) FROM ops.assistant_questions"


class FakeProvider:
    enabled = True

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.history: list[ChatTurn] = []

    async def chat(
        self,
        *,
        system: str,
        messages: Sequence[ChatTurn],
        tools: Sequence[ToolSpec],
        call_tool: ToolCaller,
        max_tool_rounds: int = 5,
    ) -> ProviderReply:
        if self.fail:
            raise ProviderError("falha simulada")
        self.history = list(messages)
        return ProviderReply(text="resposta ok", tokens_in=3, tokens_out=2, tools_used=())


def _client(env: Env, provider: Any, *, limit: int = 200, inject: bool = True) -> TestClient:
    settings = make_settings(env.dsn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await create_pool(settings.database_url.get_secret_value(), min_size=1, max_size=4)
        app.state.pool = pool
        enabled = bool(provider.enabled)
        if inject:
            app.state.assistant_service = AssistantService(
                provider=provider,
                tools=AssistantTools(pool) if enabled else None,
                panel_pool=pool,
                daily_limit=limit,
                clock=env.clock,
            )
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = env.clock
    app.state.panel_iam = IamClient(
        env.iam.client(), env.clock, base_url=IAM_BASE_URL, cache_ttl_seconds=0
    )
    app.include_router(probe)
    mount_panel(app)
    return TestClient(app, base_url=ORIGIN, follow_redirects=False)


def _csrf(c: TestClient) -> dict[str, str]:
    """Cabecalhos de POST com o token CSRF da sessao do admin logado."""
    return post_headers(str(c.get("/dashboard/_t/token").json()["token"]))


def test_sem_sessao_da_401_json(env: Env) -> None:
    with _client(env, FakeProvider()) as c:
        assert c.get(STATUS).status_code == 401
        assert c.post(CHAT, json={"message": "oi"}).status_code == 401


def test_status_ligado_lista_agentes(env: Env) -> None:
    with _client(env, FakeProvider()) as c:
        login(c)
        r = c.get(STATUS)
    body = r.json()
    assert r.status_code == 200 and body["enabled"] is True and "reason" not in body
    assert [a["name"] for a in body["agents"]][0] == "Jarvis" and len(body["agents"]) == 6
    assert body["max_chars"] == 4000


def test_status_desligado_sem_chave(env: Env) -> None:
    with _client(env, DisabledProvider()) as c:
        login(c)
        body = c.get(STATUS).json()
    assert body["enabled"] is False and body["reason"] == DISABLED_REASON


def test_chat_desligado_da_503_sem_registrar(env: Env) -> None:
    with _client(env, DisabledProvider()) as c:
        login(c)
        r = c.post(CHAT, json={"message": "oi"}, headers=_csrf(c))
    assert r.status_code == 503 and r.json()["error"] == "assistant_disabled"
    assert sql(env.dsn, COUNT) == 0


def test_chat_exige_csrf(env: Env) -> None:
    with _client(env, FakeProvider()) as c:
        login(c)
        assert c.post(CHAT, json={"message": "oi"}).status_code == 403
        bad = c.post(CHAT, json={"message": "oi"}, headers=post_headers("errado"))
    assert bad.status_code == 403
    assert sql(env.dsn, COUNT) == 0


def test_chat_responde_e_registra(env: Env) -> None:
    provider = FakeProvider()
    with _client(env, provider) as c:
        login(c)
        hist = [{"role": "user", "text": "antes"}, {"role": "assistant", "text": "sim"}]
        r = c.post(
            CHAT,
            json={"message": " Como esta a Zaya? ", "history": hist},
            headers=_csrf(c),
        )
    body = r.json()
    assert r.status_code == 200 and body["text"] == "resposta ok"
    assert body["agent"]["name"] != "Jarvis" and body["tools_used"] == []
    assert [t.text for t in provider.history] == ["antes", "sim", "Como esta a Zaya?"]
    assert sql(env.dsn, COUNT) == 1
    assert sql(env.dsn, "SELECT asked_by FROM ops.assistant_questions") == "admin@example.test"


def test_limite_de_4000_caracteres(env: Env) -> None:
    with _client(env, FakeProvider()) as c:
        login(c)
        h = _csrf(c)
        ok = c.post(CHAT, json={"message": "a" * 4000}, headers=h)
        big = c.post(CHAT, json={"message": "a" * 4001}, headers=h)
        empty = c.post(CHAT, json={"message": "   "}, headers=h)
        long_turn = {"role": "user", "text": "a" * 4001}
        bad_hist = c.post(CHAT, json={"message": "x", "history": [long_turn]}, headers=h)
    assert ok.status_code == 200
    assert big.status_code == 413 and big.json()["error"] == "message_too_long"
    assert empty.status_code == 422 and bad_hist.status_code == 422
    assert sql(env.dsn, COUNT) == 1


def test_doze_por_minuto_por_admin(env: Env) -> None:
    with _client(env, FakeProvider()) as c:
        login(c)
        h = _csrf(c)
        codes = [c.post(CHAT, json={"message": "oi"}, headers=h).status_code for _ in range(13)]
        blocked = c.post(CHAT, json={"message": "oi"}, headers=h)
        env.clock.advance(seconds=61)
        again = c.post(CHAT, json={"message": "oi"}, headers=h)
    assert codes[:12] == [200] * 12 and codes[12] == 429
    assert int(blocked.headers["Retry-After"]) >= 1 and blocked.json()["error"] == "rate_limited"
    assert again.status_code == 200
    assert sql(env.dsn, COUNT) == 13


def test_limite_diario_da_429(env: Env) -> None:
    with _client(env, FakeProvider(), limit=1) as c:
        login(c)
        h = _csrf(c)
        assert c.post(CHAT, json={"message": "um"}, headers=h).status_code == 200
        r = c.post(CHAT, json={"message": "dois"}, headers=h)
    assert r.status_code == 429 and r.json()["error"] == "daily_limit"
    assert sql(env.dsn, COUNT) == 1


def test_falha_do_modelo_da_502_e_grava_erro_sem_vazar(env: Env) -> None:
    with _client(env, FakeProvider(fail=True)) as c:
        login(c)
        r = c.post(CHAT, json={"message": "oi"}, headers=_csrf(c))
    assert r.status_code == 502 and r.json()["error"] == "assistant_failed"
    assert "simulada" not in r.text
    assert sql(env.dsn, "SELECT status FROM ops.assistant_questions") == "error"


def test_servico_e_montado_da_configuracao_quando_nao_injetado(env: Env) -> None:
    with _client(env, DisabledProvider(), inject=False) as c:
        login(c)
        body = c.get(STATUS).json()
    assert body["enabled"] is False and body["reason"] == DISABLED_REASON
