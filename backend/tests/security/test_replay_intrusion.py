"""Replay, eventos antigos e intrusao na borda HTTP (PF-39; AC-2, AC-4, AC-6, AC-7, AC-31).

Tudo assinado com `compute_hub_signature` contra o app real. Um payload assinado
reapresentado vale um unico efeito; lixo bem assinado nao derruba o app nem escreve;
o app nao expoe superficie extra (docs, metodos, host).
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from app.core.security import compute_hub_signature
from tests.security.test_signature import (
    SECRET,
    URL,
    Env,
    add_user,
    assert_no_effects,
    count_effects,
    fixture_bytes,
    signed_payload,
    start_env,
)

pytestmark = [pytest.mark.security, pytest.mark.db]

@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Any:
    e = start_env()
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


async def settle() -> None:
    await asyncio.sleep(0.4)


# ---------------------------------------------------------------- replay (AC-7)


async def test_mesmo_payload_assinado_varias_vezes_tem_um_unico_efeito(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    raw = fixture_bytes("text")
    signature = compute_hub_signature(raw, SECRET)
    for _ in range(5):
        assert env.post(raw, signature=signature).status_code == 200
        await asyncio.sleep(0.05)
    await settle()
    effects = await count_effects(db_pool)
    assert effects["chat.message_trace"] == 1
    assert effects["chat.outbound_messages"] == 1
    assert len(env.channel.calls) == 1
    # a reentrega nao consome o limite horario
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 1


async def test_replay_de_desconhecido_nao_incrementa_tentativas(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    raw = fixture_bytes("text")
    for _ in range(4):
        assert env.post(raw).status_code == 200
    await settle()
    row = await db_pool.fetchrow("SELECT attempt_count FROM access.access_attempts")
    assert row is not None and row["attempt_count"] == 1
    assert env.channel.calls == [] and env.http_calls == []


async def test_replay_com_bytes_diferentes_e_mesmo_message_id_tem_um_efeito(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    """Atacante com payload capturado nao ganha efeito novo reformatando o JSON e reassinando
    (so seria possivel com o segredo, mas a idempotencia vale pelo id da mensagem)."""
    await add_user(db_pool)
    raw = fixture_bytes("text")
    other = json.dumps(json.loads(raw), indent=2).encode()
    assert other != raw
    assert env.post(raw).status_code == 200
    assert env.post(other).status_code == 200
    await settle()
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 1
    assert len(env.channel.calls) == 1


async def test_replay_do_botao_de_aceite_nao_reativa_nem_envia_de_novo(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool, "revoked")
    raw = fixture_bytes("button_template")
    for _ in range(3):
        assert env.post(raw).status_code == 200
    await settle()
    assert await db_pool.fetchval("SELECT status FROM access.users") == "revoked"
    assert env.channel.calls == []


# ---------------------------------------------------------------- eventos antigos


@pytest.mark.parametrize("timestamp", ["1", "1577836800", "0", "99999999999"])
async def test_evento_antigo_ou_data_absurda_nao_derruba_e_segue_a_mesma_regra(
    env: Env, db_pool: asyncpg.Pool, timestamp: str
) -> None:
    # desconhecido com carimbo antigo: ainda silencio total
    raw = signed_payload(timestamp=timestamp, msg_id=f"wamid.OLD{timestamp}")
    assert env.post(raw).status_code == 200
    await settle()
    assert env.channel.calls == [] and env.http_calls == []
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 0


async def test_status_antigo_reapresentado_nao_cria_rastro_nem_envio(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    for name in ("status_delivered", "status_read", "status_sent"):
        raw = fixture_bytes(name)
        assert env.post(raw).status_code == 200
        assert env.post(raw).status_code == 200
    await settle()
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 0
    assert env.channel.calls == [] and env.http_calls == []


# ---------------------------------------------------------------- outro phone_number_id (AC-6)


async def test_payload_de_outro_numero_assinado_nao_tem_efeito_nenhum(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    for _ in range(3):
        assert env.post(fixture_bytes("other_phone_number_id")).status_code == 200
    await settle()
    await assert_no_effects(env, db_pool)
    assert await env.counter("wrong_recipient", expected=3) == 3


async def test_payload_multiplo_processa_so_o_destinatario_certo(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    data = json.loads(fixture_bytes("multiple"))
    values = [c["value"] for e in data["entry"] for c in e["changes"]]
    for value in values:
        value["metadata"]["phone_number_id"] = "999999999999999"
    assert env.post(json.dumps(data).encode()).status_code == 200
    await settle()
    await assert_no_effects(env, db_pool)


# ---------------------------------------------------------------- lixo bem assinado


GARBAGE: list[tuple[str, bytes]] = [
    ("lista", b"[]"),
    ("nulo", b"null"),
    ("numero", b"123"),
    ("string", b'"oi"'),
    ("objeto_vazio", b"{}"),
    ("entry_nao_lista", b'{"object": "x", "entry": "abc"}'),
    ("entry_com_nulos", b'{"entry": [null, 1, "a", [], {"changes": null}]}'),
    ("changes_invalido", b'{"entry": [{"changes": [{"field": 5, "value": []}]}]}'),
    (
        "mensagem_sem_campos",
        b'{"entry":[{"changes":[{"field":"messages","value":{"messages":[{}]}}]}]}',
    ),
    (
        "mensagem_tipo_estranho",
        b'{"entry":[{"changes":[{"field":"messages","value":'
        b'{"metadata":{"phone_number_id":"100000000000001"},'
        b'"messages":[{"id":1,"from":[],"type":{}}]}}]}]}',
    ),
    ("aninhamento_profundo", b"[" * 20000 + b"]" * 20000),
    ("numero_gigante", b'{"x": ' + b"9" * 5000 + b"}"),
    ("utf8_invalido", b'{"x": "\xff\xfe"}'),
    ("json_truncado", b'{"entry": [{"changes": ['),
]


@pytest.mark.parametrize("raw", [r for _, r in GARBAGE], ids=[n for n, _ in GARBAGE])
async def test_lixo_bem_assinado_nao_gera_5xx_nem_efeito(
    env: Env, db_pool: asyncpg.Pool, raw: bytes
) -> None:
    resp = env.post(raw)
    assert resp.status_code in (200, 400)
    assert "Traceback" not in resp.text and "Error" not in resp.text
    await settle()
    await assert_no_effects(env, db_pool)
    # o app continua de pe
    assert env.client.get("/health").status_code == 200


# ---------------------------------------------------------------- injecao em texto de usuario


async def test_texto_com_sql_e_html_de_ativo_e_so_dado(env: Env, db_pool: asyncpg.Pool) -> None:
    await add_user(db_pool)
    evil = "'); DROP TABLE access.users; -- <script>alert(1)</script> \x00"
    raw = signed_payload(body=evil.replace("\x00", ""), msg_id="wamid.EVIL0001")
    assert env.post(raw).status_code == 200
    await settle()
    assert await db_pool.fetchval("SELECT count(*) FROM access.users") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM chat.message_trace") == 1
    assert len(env.channel.calls) == 1
    assert evil.split(" \x00")[0] not in (env.channel.calls[0].body or "")  # sem eco ao usuario


# ---------------------------------------------------------------- superficie exposta


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
async def test_metodos_nao_previstos_no_webhook_sao_recusados(env: Env, method: str) -> None:
    resp = env.client.request(method.upper(), URL, content=b"{}")
    assert resp.status_code == 405


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json", "/admin", "/webhook"])
async def test_sem_documentacao_nem_rotas_extras(env: Env, path: str) -> None:
    resp = env.client.get(path)
    assert resp.status_code == 404
    assert "swagger" not in resp.text.lower() and "openapi" not in resp.text.lower()


async def test_host_nao_confiavel_e_recusado_antes_do_webhook(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    raw = fixture_bytes("text")
    resp = env.client.post(
        URL,
        content=raw,
        headers={
            "Host": "evil.example.com",
            "X-Hub-Signature-256": compute_hub_signature(raw, SECRET),
        },
    )
    assert resp.status_code == 400
    await assert_no_effects(env, db_pool)


async def test_cabecalhos_de_seguranca_e_request_id_novo_por_requisicao(env: Env) -> None:
    first = env.post(b"x", signature=None, **{"X-Request-ID": "forjado"})
    second = env.post(b"x", signature=None)
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert first.headers["Cache-Control"] == "no-store"
    assert first.headers["X-Request-ID"] != "forjado"  # nunca aceito do cliente
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_verify_token_errado_repetido_nao_gera_efeito_nem_eco(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    for i in range(10):
        resp = env.client.get(
            URL,
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": f"palpite{i}",
                "hub.challenge": "SEGREDO",
            },
        )
        assert resp.status_code == 403 and "SEGREDO" not in resp.text
    await assert_no_effects(env, db_pool)
