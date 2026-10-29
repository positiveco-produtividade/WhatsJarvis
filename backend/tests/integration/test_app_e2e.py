"""App real ponta a ponta (PF-38): webhook assinado -> pipeline -> saida despachada.

Postgres de teste real, `TestClient` com o lifespan completo, `FakeChannel` injetado e
`httpx.MockTransport` no e-mail/WAHA. Cobre AC-1..4, AC-3 (503), AC-30/31, R4 (politica
instalada no lifespan) e o NFR "200 em menos de 1 s".
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.errors import TransientDbError
from app.core.security import compute_hub_signature
from app.services import outbox
from app.services.phone import normalize_phone
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

BASE = "http://localhost"
SECRET = "app-secret-e2e"
VERIFY = "verify-token-e2e"
PHONE_ID = "100000000000001"
USER_PHONE = "551199990001"
URL = "/webhook/whatsapp"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "meta"
SECRET_TEXT = "Jungle tem glúten?"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


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
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@dataclass
class Env:
    app: FastAPI
    client: TestClient
    channel: FakeChannel
    http_calls: list[httpx.Request]
    settings: Settings

    def post(self, raw: bytes, signature: str | None = "auto") -> httpx.Response:
        headers: dict[str, str] = {}
        if signature == "auto":
            headers["X-Hub-Signature-256"] = compute_hub_signature(raw, SECRET)
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post(URL, content=raw, headers=headers)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def build_app(channel: FakeChannel, **over: Any) -> tuple[FastAPI, list[httpx.Request], Settings]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "email-fake-1"})

    cfg = make_settings(**over)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return main.create_app(cfg, channel=channel, http_client=http), calls, cfg


def start_env(**over: Any) -> Env:
    channel = FakeChannel()
    app, calls, cfg = build_app(channel, **over)
    client = TestClient(app, base_url=BASE)
    client.__enter__()
    return Env(app, client, channel, calls, cfg)


@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Any:  # db_pool limpa as tabelas antes
    e = start_env()
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


async def add_user(pool: asyncpg.Pool, status: str = "active") -> int:
    num = normalize_phone("+" + USER_PHONE)
    uid = int(
        await pool.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version) VALUES ('Ana', "
            "$1, $2, 'vendedor', 'Acme', ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], "
            "'Gestora', $3, $4, $4, 'teste', $4, "
            "CASE WHEN $3 = 'active' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'active' THEN 'v1' END) RETURNING id",
            num.phone_e164,
            num.match_key,
            status,
            NOW,
        )
    )
    return uid


async def wait_for(check: Callable[[], Any], timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condicao nao atingida no prazo")


# ---------------------------------------------------------------- health e handshake


async def test_health_ok_com_migrations_em_dia(env: Env) -> None:
    resp = env.client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_handshake_get_usa_o_app_real(env: Env) -> None:
    ok = env.client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "4321"}
    )
    assert ok.status_code == 200 and ok.text == "4321"
    bad = env.client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "ECO"}
    )
    assert bad.status_code == 403 and "ECO" not in bad.text
    assert env.app.state.counters.pending()  # bad_verify_token contado


# ---------------------------------------------------------------- fluxo feliz (AC-3, NFR)


async def test_texto_de_ativo_200_rapido_e_resposta_em_construcao(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    started = time.monotonic()
    resp = env.post(fixture_bytes("text"))
    elapsed = time.monotonic() - started
    assert resp.status_code == 200 and resp.json() == {"status": "ok"}
    assert elapsed < 1.0  # NFR: 200 em menos de 1 s

    async def sent() -> bool:
        return bool(env.channel.calls)

    await wait_for(sent)
    call = env.channel.calls[0]
    expected_to = normalize_phone("+" + USER_PHONE).phone_e164.lstrip("+")
    assert call.method == "send_text" and call.to.lstrip("+") == expected_to
    assert call.body == env.settings.text_under_construction
    row = await db_pool.fetchrow("SELECT status, kind FROM chat.outbound_messages")
    assert row is not None and row["kind"] == "under_construction"

    async def final() -> bool:
        return await db_pool.fetchval("SELECT status FROM chat.outbound_messages") == "sent"

    await wait_for(final)
    trace = await db_pool.fetchrow(
        "SELECT decision, reply_kind, send_result FROM chat.message_trace"
    )
    assert trace is not None
    assert trace["decision"] == "allowed" and trace["reply_kind"] == "under_construction"
    assert trace["send_result"] == "sent"


async def test_reentrega_do_mesmo_payload_nao_responde_duas_vezes(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    raw = fixture_bytes("text")
    assert env.post(raw).status_code == 200

    async def sent() -> bool:
        return len(env.channel.calls) == 1

    await wait_for(sent)
    assert env.post(raw).status_code == 200
    await asyncio.sleep(0.3)
    assert len(env.channel.calls) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 1


# ---------------------------------------------------------------- desconhecido e bordas


async def test_desconhecido_200_e_nenhuma_chamada_de_saida(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    resp = env.post(fixture_bytes("text"))
    assert resp.status_code == 200
    await asyncio.sleep(0.3)
    assert env.channel.calls == [] and env.http_calls == []
    assert await db_pool.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0
    assert await db_pool.fetchval("SELECT count(*) FROM access.access_attempts") == 1


async def test_assinatura_invalida_403_sem_efeito(env: Env, db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    for sig in (None, "sha256=" + "0" * 64, "lixo"):
        assert env.post(fixture_bytes("text"), signature=sig).status_code == 403
    await asyncio.sleep(0.2)
    assert env.channel.calls == []
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 0


async def test_corpo_grande_413_antes_da_assinatura(env: Env) -> None:
    resp = env.post(b"x" * 70000, signature=None)
    assert resp.status_code == 413


async def test_outro_phone_number_id_200_sem_saida(env: Env, db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    assert env.post(fixture_bytes("other_phone_number_id")).status_code == 200
    await asyncio.sleep(0.2)
    assert env.channel.calls == []
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 0


async def test_erro_transitorio_vira_503(
    env: Env, monkeypatch: pytest.MonkeyPatch, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)

    async def boom(msg: Any) -> Any:
        raise TransientDbError("pool esgotado")

    monkeypatch.setattr(env.app.state.processor._pipeline, "handle_message", boom)
    resp = env.post(fixture_bytes("text"))
    assert resp.status_code == 503
    assert env.channel.calls == []


# ---------------------------------------------------------------- pausa (R4)


async def test_pausa_ligada_interrompe_a_resposta(env: Env, db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    async with db_pool.acquire() as conn, conn.transaction():
        await env.app.state.flags.set_bool(conn, "paused", True, "teste", "cli")
    assert env.post(fixture_bytes("text")).status_code == 200

    async def discarded() -> bool:
        status = await db_pool.fetchval("SELECT status FROM chat.outbound_messages")
        return bool(status == "discarded")

    await wait_for(discarded)
    assert env.channel.calls == []
    reason = await db_pool.fetchval("SELECT discard_reason FROM chat.outbound_messages")
    assert reason == "paused"


# ---------------------------------------------------------------- midia e falha de envio


GROUP_ON = {
    "team_group_enabled": True,
    "waha_api_key": "waha-key-e2e",
    "team_group_chat_id": "120363000000000000@g.us",
}


async def _media_flow(db_pool: asyncpg.Pool, **over: Any) -> Env:
    await add_user(db_pool)
    env = start_env(**over)
    try:
        assert env.post(fixture_bytes("image")).status_code == 200

        async def done() -> bool:
            rows = await db_pool.fetch("SELECT status FROM ops.deliveries")
            return bool(env.channel.calls) and bool(rows) and rows[0]["status"] != "pending"

        await wait_for(done)
        return env
    finally:
        env.client.__exit__(None, None, None)


async def test_midia_com_grupo_ligado_envia_ack_e_aviso_ao_grupo(db_pool: asyncpg.Pool) -> None:
    env = await _media_flow(db_pool, **GROUP_ON)
    assert env.channel.calls[0].method == "send_text"
    assert len(env.http_calls) == 1  # WAHA pelo MockTransport
    assert env.http_calls[0].url.path == "/api/sendText"
    assert await db_pool.fetchval("SELECT status FROM ops.deliveries") == "sent"
    sent_body = env.http_calls[0].content.decode()
    assert USER_PHONE not in sent_body


async def test_midia_com_grupo_desligado_fica_so_no_painel(db_pool: asyncpg.Pool) -> None:
    env = await _media_flow(db_pool)
    assert env.http_calls == []
    assert await db_pool.fetchval("SELECT status FROM ops.deliveries") == "panel_only"


async def test_falha_definitiva_de_envio_marca_failed(env: Env, db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    env.channel.fail_always("definitive")
    assert env.post(fixture_bytes("text")).status_code == 200

    async def failed() -> bool:
        return await db_pool.fetchval("SELECT status FROM chat.outbound_messages") == "failed"

    await wait_for(failed)
    assert env.channel.calls  # tentou enviar


# ---------------------------------------------------------------- reinicio (recuperacao)


@dataclass(frozen=True)
class Target:
    id: int
    phone_e164: str


async def test_reinicio_recupera_pendencias(db_pool: asyncpg.Pool) -> None:
    uid = await add_user(db_pool)
    target = Target(uid, "+" + USER_PHONE)
    old = datetime.now(UTC) - timedelta(minutes=10)
    async with db_pool.acquire() as conn:
        pending_id = await outbox.enqueue(conn, target, "under_construction", body="pendente")
        stuck_id = await outbox.enqueue(conn, target, "under_construction", body="travada")
        await conn.execute(
            "UPDATE chat.outbound_messages SET status = 'sending', claimed_at = $2 WHERE id = $1",
            stuck_id,
            old,
        )
    # o laco de fundo roda a recuperacao na subida e varre o que venceu
    env = start_env()
    try:

        async def drained() -> bool:
            rows = {
                r["id"]: r["status"]
                for r in await db_pool.fetch("SELECT id, status FROM chat.outbound_messages")
            }
            return rows[pending_id] == "sent" and rows[stuck_id] == "unknown"

        await wait_for(drained)
        assert [c.body for c in env.channel.calls] == ["pendente"]  # a travada NAO e reenviada
    finally:
        env.client.__exit__(None, None, None)


async def test_politica_volta_ao_padrao_no_desligamento(db_pool: asyncpg.Pool) -> None:
    env = start_env()
    installed = type(outbox._policy).__name__
    env.client.__exit__(None, None, None)
    assert installed == "FlagsReplyPolicy"
    assert type(outbox._policy).__name__ == "AllowAllPolicy"

