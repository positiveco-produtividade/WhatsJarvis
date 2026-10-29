"""E-mail de alerta sem PII, sem bloquear o webhook, com retentativa (PF-40; AC-37 e AC-13).

App real com `FakeChannel` e `httpx.MockTransport` capturando o corpo da requisicao ao Resend
e ao WAHA. Os alertas nascem de fluxos reais do pipeline (conflito de identidade e limite de
mensagens). O e-mail jamais carrega telefone, nome, BSUID nem texto de mensagem.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app import main
from app.services import deliveries
from tests.fakes import FakeChannel
from tests.integration.test_trace_logs import (
    ALL_PHONES,
    ALL_TEXTS,
    BSUID_OTHER,
    BSUID_STORED,
    NAME_ACTIVE,
    NAME_CONFLICT,
    PHONE_ACTIVE,
    PHONE_CONFLICT,
    PHONE_UNKNOWN,
    PROFILE_NAME,
    RECIPIENT,
    TEXT_ACTIVE,
    TEXT_CONFLICT,
    TEXT_UNKNOWN,
    WAHA_KEY,
    Env,
    add_user,
    find_leaks,
    make_settings,
    payload,
    phone_variants,
    record_dump,
    settle,
    start_env,
    wait_for,
)

pytestmark = pytest.mark.db

RESEND_HOST = "api.resend.com"
GROUP_ON = {
    "team_group_enabled": True,
    "waha_api_key": WAHA_KEY,
    "team_group_chat_id": "120363000000000000@g.us",
}


def resend_calls(env: Env) -> list[httpx.Request]:
    return [c for c in env.http_calls if c.url.host == RESEND_HOST]


def waha_calls(env: Env) -> list[httpx.Request]:
    return [c for c in env.http_calls if c.url.path == "/api/sendText"]


def email_text(call: httpx.Request) -> str:
    """Assunto + corpo do e-mail como o provedor os recebe."""
    body = json.loads(call.content)
    return f"{body['subject']}\n{body['text']}"


def assert_email_is_clean(call: httpx.Request) -> None:
    text = email_text(call)
    for digits in ALL_PHONES:
        assert not any(v in text for v in phone_variants(digits))
    for forbidden in (*ALL_TEXTS, PROFILE_NAME, NAME_ACTIVE, NAME_CONFLICT):
        assert forbidden not in text
    for bsuid in (BSUID_STORED, BSUID_OTHER):
        assert bsuid not in text
    assert "wamid" not in text
    assert "@" not in text  # nem o e-mail do destinatario
    assert find_leaks(text) == []


async def make_conflict(env: Env, pool: asyncpg.Pool, wamid: str = "wamid.MAIL.CONFLICT") -> None:
    await add_user(pool, name=NAME_CONFLICT, phone=PHONE_CONFLICT, bsuid=BSUID_STORED)
    raw = payload(wamid, wa_id=PHONE_CONFLICT, bsuid=BSUID_OTHER, text=TEXT_CONFLICT)
    assert env.post(raw).status_code == 200


async def delivery_status(pool: asyncpg.Pool, channel: str) -> str | None:
    value = await pool.fetchval("SELECT status FROM ops.deliveries WHERE channel = $1", channel)
    return None if value is None else str(value)


@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Any:  # db_pool limpa as tabelas antes
    e = start_env()
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


# ------------------------------------------------------------------ AC-37: conteudo do e-mail


async def test_conflito_gera_um_email_imediato_sem_pii(env: Env, db_pool: asyncpg.Pool) -> None:
    await make_conflict(env, db_pool)
    calls = resend_calls(env)
    assert len(calls) == 1  # novo alerta = e-mail imediato (depois do 200, no mesmo despacho)
    assert calls[0].method == "POST" and calls[0].url.path == "/emails"
    body = json.loads(calls[0].content)
    assert body["to"] == [RECIPIENT] and body["from"] == "jarvis@example.test"
    assert_email_is_clean(calls[0])
    assert "identity_conflict" in body["subject"] + body["text"]
    assert await delivery_status(db_pool, "email") == "sent"


async def test_alerta_de_limite_envia_email_sem_pii(db_pool: asyncpg.Pool) -> None:
    env = start_env(rate_limit_per_hour=1)
    try:
        await add_user(db_pool, name=NAME_ACTIVE, phone=PHONE_ACTIVE)
        for i, text in enumerate((TEXT_ACTIVE, TEXT_UNKNOWN)):
            raw = payload(f"wamid.MAIL.RATE{i}", wa_id=PHONE_ACTIVE, text=text)
            assert env.post(raw).status_code == 200
            await settle(db_pool)
        calls = resend_calls(env)
        assert len(calls) == 1
        assert "rate_limit" in email_text(calls[0])
        assert_email_is_clean(calls[0])
        # o usuario aparece so como numero interno
        assert f"Usuário #{await db_pool.fetchval('SELECT id FROM access.users')}" in email_text(
            calls[0]
        )
    finally:
        env.client.__exit__(None, None, None)


async def test_tentativa_de_desconhecido_e_ticket_de_midia_nao_geram_email(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool, name=NAME_ACTIVE, phone=PHONE_ACTIVE)
    unknown = payload("wamid.MAIL.UNK", wa_id=PHONE_UNKNOWN, text=TEXT_UNKNOWN)
    assert env.post(unknown).status_code == 200
    img = payload("wamid.MAIL.IMG", wa_id=PHONE_ACTIVE, kind="image", text="legenda")
    assert env.post(img).status_code == 200
    await settle(db_pool)
    assert await db_pool.fetchval("SELECT count(*) FROM access.access_attempts") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM chat.tickets") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM ops.deliveries WHERE channel='email'") == 0
    assert env.http_calls == []  # nem e-mail, nem grupo (chave desligada)


async def test_alerta_repetido_na_janela_nao_envia_segundo_email(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await make_conflict(env, db_pool, "wamid.MAIL.C1")
    again = payload("wamid.MAIL.C2", wa_id=PHONE_CONFLICT, bsuid=BSUID_OTHER, text=TEXT_CONFLICT)
    assert env.post(again).status_code == 200
    assert len(resend_calls(env)) == 1  # so NOVO alerta gera e-mail
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 1


# ------------------------------------------------------------------ AC-37: nao bloqueia o webhook


class _Timeline:
    """Middleware ASGI puro: marca o instante em que o 200 comeca a ser enviado."""

    def __init__(self, app: ASGIApp, events: list[tuple[str, float]]) -> None:
        self.app = app
        self.events = events

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def wrapped(message: Message) -> None:
            if message["type"] == "http.response.start" and scope["path"] == "/webhook/whatsapp":
                self.events.append(("response_start", time.monotonic()))
            await send(message)

        await self.app(scope, receive, wrapped)


async def test_email_sai_depois_da_resposta_do_webhook(db_pool: asyncpg.Pool) -> None:
    events: list[tuple[str, float]] = []

    async def slow_resend(request: httpx.Request) -> httpx.Response:
        events.append(("email_request", time.monotonic()))
        await asyncio.sleep(0.3)  # provedor lento
        return httpx.Response(200, json={"id": "email-fake-1"})

    channel = FakeChannel()
    cfg = make_settings()
    http = httpx.AsyncClient(transport=httpx.MockTransport(slow_resend))
    app: FastAPI = main.create_app(cfg, channel=channel, http_client=http)
    app.add_middleware(_Timeline, events=events)
    from fastapi.testclient import TestClient

    client = TestClient(app, base_url="http://localhost")
    client.__enter__()
    try:
        env = Env(app, client, channel, [], cfg)
        await make_conflict(env, db_pool)
    finally:
        client.__exit__(None, None, None)
    order = [name for name, _ in sorted(events, key=lambda e: e[1])]
    assert order == ["response_start", "email_request"]  # 200 sai antes de falar com o Resend
    assert await delivery_status(db_pool, "email") == "sent"


# ------------------------------------------------------------------ AC-37: falha e retentativa


async def test_falha_do_email_e_logada_sem_pii_marcada_e_retentada(
    db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = [
        httpx.Response(503, json={"error": "indisponivel"}),
        httpx.Response(200, json={"id": "x"}),
    ]

    def flaky(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    env = start_env(handler=flaky)
    try:
        await make_conflict(env, db_pool)
        row = await db_pool.fetchrow(
            "SELECT id, status, attempts, last_error, next_attempt_at FROM ops.deliveries "
            "WHERE channel = 'email'"
        )
        assert row is not None
        assert row["status"] == "pending" and row["attempts"] == 1  # marcada, volta a fila
        assert row["last_error"] == "http_503" and row["next_attempt_at"] is not None
        assert any("email send failed" in r.getMessage() for r in caplog.records)
        # retentativa (o laco de fundo faria o mesmo quando vencer o backoff)
        status = await deliveries.dispatch(
            db_pool,
            int(row["id"]),
            flags=env.app.state.flags,
            settings=env.settings,
            email_client=env.app.state.processor._dispatcher._http_client,
        )
        assert status == "sent"
        assert await delivery_status(db_pool, "email") == "sent"
        assert len(resend_calls(env)) == 2
        assert_email_is_clean(resend_calls(env)[1])
        assert find_leaks(record_dump(caplog.records)) == []
    finally:
        env.client.__exit__(None, None, None)


async def test_falha_permanente_do_email_nao_e_retentada(db_pool: asyncpg.Pool) -> None:
    env = start_env(handler=lambda request: httpx.Response(401, json={"message": "invalid key"}))
    try:
        await make_conflict(env, db_pool)
        row = await db_pool.fetchrow(
            "SELECT status, last_error FROM ops.deliveries WHERE channel = 'email'"
        )
        assert row is not None and row["status"] == "failed" and row["last_error"] == "http_401"
        assert len(resend_calls(env)) == 1
    finally:
        env.client.__exit__(None, None, None)


# ------------------------------------------------------------------ AC-13: grupo x e-mail


async def test_grupo_desligado_zero_chamadas_ao_waha_e_email_independe(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await make_conflict(env, db_pool)
    assert waha_calls(env) == []
    assert len(resend_calls(env)) == 1  # e-mail independe da chave do grupo
    assert await delivery_status(db_pool, "group") == "panel_only"


async def test_grupo_ligado_envia_aviso_sem_pii_alem_do_email(db_pool: asyncpg.Pool) -> None:
    env = start_env(**GROUP_ON)
    try:
        await make_conflict(env, db_pool)
        assert len(waha_calls(env)) == 1 and len(resend_calls(env)) == 1
        group_text = json.loads(waha_calls(env)[0].content)["text"]
        assert find_leaks(group_text) == []
        assert await delivery_status(db_pool, "group") == "sent"
    finally:
        env.client.__exit__(None, None, None)


async def test_waha_com_falha_mantem_o_registro_e_o_email_sai(
    db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sendText":
            return httpx.Response(500, json={"error": "waha fora"})
        return httpx.Response(200, json={"id": "email-fake-1"})

    env = start_env(handler=handler, **GROUP_ON)
    try:
        await make_conflict(env, db_pool)
        assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 1
        group = await db_pool.fetchrow(
            "SELECT status, attempts, last_error FROM ops.deliveries WHERE channel = 'group'"
        )
        assert group is not None and group["status"] == "pending"  # registro mantido p/ retentar
        assert group["attempts"] == 1 and group["last_error"] == "http_500"
        assert await delivery_status(db_pool, "email") == "sent"  # falha do grupo nao afeta
        assert find_leaks(record_dump(caplog.records)) == []
    finally:
        env.client.__exit__(None, None, None)


async def test_espera_de_entrega_nao_deixa_pendencia_orfa(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await make_conflict(env, db_pool)

    async def done() -> bool:
        return await db_pool.fetchval(
            "SELECT count(*) FROM ops.deliveries WHERE status IN ('pending', 'sending')"
        ) == 0

    await wait_for(done)
