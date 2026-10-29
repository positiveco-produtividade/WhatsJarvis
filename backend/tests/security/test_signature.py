"""Assinatura, tamanho e handshake no app real (PF-39; AC-1, AC-2, AC-4, AC-31).

Postgres de teste real, `TestClient` com o lifespan completo, `FakeChannel` e
`httpx.MockTransport`. Este modulo tambem concentra os auxiliares compartilhados pelos
outros testes de seguranca (`Env`, `start_env`, `signed_payload`, `count_effects`).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Iterator
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
from app.core.security import compute_hub_signature
from app.services.phone import normalize_phone
from tests.fakes import FakeChannel

pytestmark = [pytest.mark.security, pytest.mark.db]

BASE = "http://localhost"
SECRET = "app-secret-sec"
VERIFY = "verify-token-sec"
PHONE_ID = "100000000000001"
USER_PHONE = "551199990001"
URL = "/webhook/whatsapp"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "meta"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
EFFECT_TABLES = (
    "chat.message_trace",
    "chat.outbound_messages",
    "chat.tickets",
    "access.access_attempts",
    "ops.alerts",
    "ops.deliveries",
)


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": os.environ["TEST_DATABASE_URL"],
        "whatsapp_app_secret": SECRET,
        "whatsapp_verify_token": VERIFY,
        "whatsapp_access_token": "access-token-sec",
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

    def post(
        self, raw: bytes, signature: str | bytes | None = "auto", **headers: str
    ) -> httpx.Response:
        hdrs: dict[str, Any] = dict(headers)
        if signature == "auto":
            hdrs["X-Hub-Signature-256"] = compute_hub_signature(raw, SECRET)
        elif signature is not None:
            hdrs["X-Hub-Signature-256"] = signature
        return self.client.post(URL, content=raw, headers=hdrs)

    async def counter(self, kind: str, expected: int | None = None) -> int:
        """Contador de seguranca: memoria + o que o laco de fundo ja gravou.

        Um flush em curso no laco do app (o primeiro ciclo roda logo na subida e, em CI lento,
        pode coincidir com a requisicao) ja esvaziou a memoria mas ainda nao gravou no banco;
        nesse instante a soma parece menor. Com `expected`, espera ate 10 s o valor convergir
        (a assercao continua com o chamador); sem ele, devolve a leitura estavel de sempre.
        """
        deadline = time.monotonic() + 10.0
        conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])  # loop do teste
        try:
            while True:
                before = await self._stored(conn, kind)
                pending = sum(
                    v for (_, k), v in self.app.state.counters.pending().items() if k == kind
                )
                if await self._stored(conn, kind) == before:  # sem flush no meio
                    total = before + pending
                    if expected is None or total == expected or time.monotonic() >= deadline:
                        return total
                await asyncio.sleep(0.05)
        finally:
            await conn.close()

    @staticmethod
    async def _stored(conn: asyncpg.Connection, kind: str) -> int:
        value = await conn.fetchval(
            "SELECT COALESCE(sum(count), 0) FROM ops.security_counters WHERE kind = $1", kind
        )
        return int(value)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


def signed_payload(
    name: str = "text",
    *,
    wa_id: str | None = None,
    msg_id: str | None = None,
    body: str | None = None,
    timestamp: str | None = None,
) -> bytes:
    """Fixture da Meta com remetente, id, corpo ou instante trocados (bytes para assinar)."""
    data = json.loads(fixture_bytes(name))
    value = data["entry"][0]["changes"][0]["value"]
    message = value["messages"][0]
    if wa_id is not None:
        message["from"] = wa_id
        if value.get("contacts"):
            value["contacts"][0]["wa_id"] = wa_id
    if msg_id is not None:
        message["id"] = msg_id
    if body is not None:
        message["text"]["body"] = body
    if timestamp is not None:
        message["timestamp"] = timestamp
    return json.dumps(data).encode("utf-8")


def start_env(**over: Any) -> Env:
    channel = FakeChannel()
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"id": "email-fake-1"})

    cfg = make_settings(**over)
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = main.create_app(cfg, channel=channel, http_client=http)
    client = TestClient(app, base_url=BASE)
    client.__enter__()
    return Env(app, client, channel, calls, cfg)


@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Iterator[Env]:  # db_pool limpa as tabelas antes
    e = start_env()
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


async def add_user(pool: asyncpg.Pool, status: str = "active", phone: str = USER_PHONE) -> int:
    num = normalize_phone("+" + phone)
    return int(
        await pool.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version, revoked_at, "
            "revoked_by) VALUES ('Ana', "
            "$1, $2, 'vendedor', 'Acme', ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], "
            "'Gestora', $3, $4, $4, 'teste', $4, "
            "CASE WHEN $3 = 'active' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'active' THEN 'v1' END, "
            "CASE WHEN $3 = 'revoked' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'revoked' THEN 'teste' END) RETURNING id",
            num.phone_e164,
            num.match_key,
            status,
            NOW,
        )
    )


async def count_effects(pool: asyncpg.Pool) -> dict[str, int]:
    """Linhas em cada tabela que um evento aceito poderia escrever."""
    return {t: int(await pool.fetchval(f"SELECT count(*) FROM {t}")) for t in EFFECT_TABLES}


async def assert_no_effects(env: Env, pool: asyncpg.Pool) -> None:
    assert await count_effects(pool) == dict.fromkeys(EFFECT_TABLES, 0)
    assert env.channel.calls == []
    assert env.http_calls == []


class SpyProcessor:
    """Substitui o processador do app: registra se foi chamado (sem parse, sem banco)."""

    def __init__(self) -> None:
        self.messages = 0
        self.statuses = 0

    async def handle_message(self, msg: Any) -> Any:
        self.messages += 1
        raise AssertionError("o processador nao pode ser chamado")

    async def handle_status(self, st: Any) -> Any:
        self.statuses += 1
        raise AssertionError("o processador nao pode ser chamado")


# ---------------------------------------------------------------- AC-2: assinatura invalida

GOOD = fixture_bytes("text")
BAD_SIGNATURES: list[tuple[str, str | bytes | None]] = [
    ("ausente", None),
    ("vazia", ""),
    ("so_prefixo", "sha256="),
    ("sem_prefixo", compute_hub_signature(GOOD, SECRET).removeprefix("sha256=")),
    ("prefixo_sha1", "sha1=" + compute_hub_signature(GOOD, SECRET).removeprefix("sha256=")),
    ("prefixo_maiusculo", compute_hub_signature(GOOD, SECRET).replace("sha256=", "SHA256=")),
    ("hex_maiusculo", "sha256=" + compute_hub_signature(GOOD, SECRET)[7:].upper()),
    ("curta", "sha256=abcd"),
    ("longa", compute_hub_signature(GOOD, SECRET) + "00"),
    ("zeros", "sha256=" + "0" * 64),
    ("nao_hex", "sha256=" + "z" * 64),
    ("segredo_errado", compute_hub_signature(GOOD, "outro-segredo")),
    ("espacos", " " + compute_hub_signature(GOOD, SECRET)),
    ("nao_ascii_utf8", "sha256=é".encode()),
    ("nao_ascii_latin1", b"sha256=\xe9\xff" + b"0" * 60),
    ("lixo", "lixo"),
]


@pytest.mark.parametrize("sig", [s for _, s in BAD_SIGNATURES], ids=[n for n, _ in BAD_SIGNATURES])
async def test_assinatura_invalida_403_vazio_sem_rastro_sem_saida(
    env: Env, db_pool: asyncpg.Pool, sig: str | bytes | None
) -> None:
    await add_user(db_pool)
    spy = SpyProcessor()
    env.app.state.processor = spy

    resp = env.post(GOOD, signature=sig)

    assert resp.status_code == 403
    assert resp.content == b""  # nenhuma resposta com conteudo
    assert await env.counter("invalid_signature", expected=1) == 1
    assert (spy.messages, spy.statuses) == (0, 0)  # nao chegou ao dominio
    await assert_no_effects(env, db_pool)


async def test_assinatura_invalida_nao_faz_parse_do_corpo(env: Env) -> None:
    """Corpo que nao e JSON com assinatura ruim da 403 (e nao 400): nao houve parse."""
    spy = SpyProcessor()
    env.app.state.processor = spy
    for garbage in (b"not json at all", b'{"entry": [', b"\xff\xfe\x00", b"[" * 5000):
        resp = env.post(garbage, signature="sha256=" + "0" * 64)
        assert resp.status_code == 403 and resp.content == b""
    assert await env.counter("invalid_json") == 0
    assert (spy.messages, spy.statuses) == (0, 0)


async def test_assinatura_valida_com_json_invalido_da_400_sem_efeito(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    resp = env.post(b'{"entry": [')
    assert resp.status_code == 400
    assert await env.counter("invalid_json", expected=1) == 1
    await assert_no_effects(env, db_pool)


async def test_assinatura_sobre_bytes_brutos_corpo_adulterado_e_recusado(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    signature = compute_hub_signature(GOOD, SECRET)
    tampered = GOOD.replace(b"Jungle", b"Jungli")
    assert tampered != GOOD
    assert env.post(tampered, signature=signature).status_code == 403
    # mesmo JSON, reserializado com outros espacos: bytes diferentes, assinatura nao vale
    reserialized = json.dumps(json.loads(GOOD), indent=4).encode()
    assert env.post(reserialized, signature=signature).status_code == 403
    await assert_no_effects(env, db_pool)


async def test_assinatura_valida_e_aceita(env: Env, db_pool: asyncpg.Pool) -> None:
    assert env.post(GOOD).status_code == 200
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 1


async def test_enxurrada_de_assinaturas_invalidas_nao_escreve_no_banco(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    for _ in range(50):
        assert env.post(GOOD, signature="sha256=" + "1" * 64).status_code == 403
    assert await env.counter("invalid_signature", expected=50) == 50
    # agregado: no maximo uma linha por dia e tipo, nunca uma escrita por requisicao
    assert await db_pool.fetchval("SELECT count(*) FROM ops.security_counters") <= 1
    await assert_no_effects(env, db_pool)


async def test_limite_por_ip_responde_429_antes_de_ler_a_assinatura(db_pool: asyncpg.Pool) -> None:
    e = start_env(rl_webhook_max=3)
    try:
        codes = [e.post(GOOD, signature=None).status_code for _ in range(6)]
        assert codes == [403, 403, 403, 429, 429, 429]
        limited = e.post(GOOD)  # ate com assinatura certa, o IP esgotado fica barrado
        assert limited.status_code == 429 and limited.headers["Retry-After"]
        await assert_no_effects(e, db_pool)
    finally:
        e.client.__exit__(None, None, None)


# ---------------------------------------------------------------- AC-4 e AC-31: tamanho primeiro


async def test_corpo_grande_413_antes_da_assinatura_mesmo_com_assinatura_ruim(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    big = b"x" * (env.settings.max_webhook_payload_bytes + 1)
    resp = env.post(big, signature="sha256=" + "0" * 64)
    assert resp.status_code == 413  # e nao 403: o tamanho vem antes da assinatura
    assert await env.counter("oversize_payload", expected=1) == 1
    assert await env.counter("invalid_signature") == 0
    await assert_no_effects(env, db_pool)


async def test_corpo_grande_413_mesmo_com_assinatura_valida(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    big = json.dumps({"pad": "a" * 70000}).encode()
    assert env.post(big).status_code == 413
    await assert_no_effects(env, db_pool)


async def test_corpo_no_limite_exato_passa_pela_checagem_de_tamanho(env: Env) -> None:
    limit = env.settings.max_webhook_payload_bytes
    body = b" " * limit
    assert len(body) == limit
    assert env.post(body, signature="sha256=" + "0" * 64).status_code == 403


# ---------------------------------------------------------------- AC-1: verify token


def _handshake(env: Env, **params: str) -> httpx.Response:
    return env.client.get(URL, params=params)


async def test_verify_token_correto_ecoa_o_challenge(env: Env) -> None:
    resp = _handshake(
        env, **{"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "4321"}
    )
    assert resp.status_code == 200 and resp.text == "4321"


@pytest.mark.parametrize(
    "params",
    [
        {"hub.mode": "subscribe", "hub.verify_token": "errado", "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "subscribe", "hub.verify_token": "", "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "subscribe", "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "unsubscribe", "hub.verify_token": VERIFY, "hub.challenge": "ECO-SECRETO"},
        {"hub.verify_token": VERIFY, "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY + "x", "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "subscribe", "hub.verify_token": VERIFY[:-1], "hub.challenge": "ECO-SECRETO"},
        {"hub.mode": "subscribe", "hub.verify_token": "é" * 5, "hub.challenge": "ECO-SECRETO"},
    ],
    ids=[
        "token_errado",
        "token_vazio",
        "sem_token",
        "modo_errado",
        "sem_modo",
        "token_com_sufixo",
        "token_truncado",
        "token_nao_ascii",
    ],
)
async def test_verify_token_errado_403_nunca_ecoa_o_challenge(
    env: Env, params: dict[str, str]
) -> None:
    resp = _handshake(env, **params)
    assert resp.status_code == 403
    assert "ECO-SECRETO" not in resp.text
    assert "ECO-SECRETO" not in json.dumps(dict(resp.headers))
    assert resp.content == b""


async def test_handshake_sem_challenge_nao_responde_200(env: Env) -> None:
    resp = _handshake(env, **{"hub.mode": "subscribe", "hub.verify_token": VERIFY})
    assert resp.status_code == 403


async def test_verify_token_errado_conta_mas_nao_grava_efeito(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    for _ in range(5):
        _handshake(env, **{"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "c"})
    assert await env.counter("bad_verify_token", expected=5) == 5
    await assert_no_effects(env, db_pool)
