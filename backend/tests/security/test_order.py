"""Ordem das barreiras (PF-39; AC-31): tamanho, assinatura, idempotencia, remetente,
acesso, limite, e so entao o que custa (cerebro e envio).

Espioes registram a sequencia real no app inteiro: `verify_hub_signature` do webhook,
`trace.begin` (idempotencia), `identity.resolve` (remetente), `access.decide` (acesso),
`limits.check_and_count` (limite), o `AnswerProvider` e o `FakeChannel`.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Iterator
from typing import Any

import asyncpg
import pytest

from app.api import webhook
from app.services import access, answering, identity, limits, trace
from tests.security.test_signature import (
    Env,
    add_user,
    fixture_bytes,
    signed_payload,
    start_env,
)

pytestmark = [pytest.mark.security, pytest.mark.db]

FULL = ["signature", "idempotency", "sender", "access"]


class Recorder:
    def __init__(self) -> None:
        self.events: list[str] = []

    def wrap(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            self.events.append(name)
            return await fn(*args, **kwargs)

        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            self.events.append(name)
            return fn(*args, **kwargs)

        return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper


class SpyProvider:
    """AnswerProvider espiao: registra a chamada e devolve o texto fixo."""

    def __init__(self, rec: Recorder) -> None:
        self._rec = rec

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self._rec.events.append("provider")
        return answering.Answer(kind="under_construction", text="em construcao")


@pytest.fixture
def rec(monkeypatch: pytest.MonkeyPatch) -> Recorder:
    r = Recorder()
    sig = r.wrap("signature", webhook.verify_hub_signature)
    monkeypatch.setattr(webhook, "verify_hub_signature", sig)
    monkeypatch.setattr(trace, "begin", r.wrap("idempotency", trace.begin))
    monkeypatch.setattr(identity, "resolve", r.wrap("sender", identity.resolve))
    monkeypatch.setattr(access, "decide", r.wrap("access", access.decide))
    monkeypatch.setattr(limits, "check_and_count", r.wrap("limit", limits.check_and_count))
    return r


def spy_env(rec: Recorder, **over: Any) -> Env:
    e = start_env(**over)
    e.app.state.processor._provider = SpyProvider(rec)
    send_text = e.channel.send_text

    async def recording_send_text(to: str, body: str) -> Any:
        rec.events.append("send")
        return await send_text(to, body)

    e.channel.send_text = recording_send_text  # type: ignore[method-assign]
    return e


@pytest.fixture
def env(db_pool: asyncpg.Pool, rec: Recorder) -> Iterator[Env]:
    e = spy_env(rec)
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


async def settle(rec: Recorder, expected: int, timeout: float = 5.0) -> None:
    """Espera o fundo (cerebro + envio) chegar ao numero esperado de eventos."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(rec.events) < expected:
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.2)  # nada a mais pode aparecer depois


async def test_tamanho_vem_antes_da_assinatura(env: Env, rec: Recorder) -> None:
    resp = env.post(b"x" * (env.settings.max_webhook_payload_bytes + 1))
    assert resp.status_code == 413
    assert rec.events == []  # a assinatura nem foi calculada


async def test_assinatura_vem_antes_de_qualquer_trabalho(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    assert env.post(fixture_bytes("text"), signature="sha256=" + "0" * 64).status_code == 403
    await settle(rec, 1)
    assert rec.events == ["signature"]


async def test_destinatario_errado_para_depois_da_assinatura(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    assert env.post(fixture_bytes("other_phone_number_id")).status_code == 200
    await settle(rec, 1)
    assert rec.events == ["signature"]  # nada de idempotencia, identidade ou acesso


async def test_desconhecido_para_no_acesso_sem_limite_nem_custo(
    env: Env, rec: Recorder
) -> None:
    assert env.post(fixture_bytes("text")).status_code == 200
    await settle(rec, 99)
    assert rec.events == FULL
    assert env.channel.calls == []
    assert env.http_calls == []


async def test_ativo_texto_segue_a_ordem_completa_e_so_depois_custa(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    assert env.post(fixture_bytes("text")).status_code == 200
    await settle(rec, len(FULL) + 3)
    assert rec.events == [*FULL, "limit", "provider", "send"]


async def test_duplicata_para_na_idempotencia(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    raw = fixture_bytes("text")
    assert env.post(raw).status_code == 200
    await settle(rec, len(FULL) + 3)
    rec.events.clear()
    assert env.post(raw).status_code == 200
    await settle(rec, 99)
    assert rec.events == ["signature", "idempotency"]  # sem remetente, acesso, limite, custo


async def test_acesso_vem_antes_do_limite_para_nao_ativos(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool, "revoked")
    assert env.post(fixture_bytes("text")).status_code == 200
    await settle(rec, 99)
    assert rec.events == FULL  # revogado: silencio, o limite nem e consultado
    assert env.channel.calls == []


async def test_midia_de_ativo_passa_pelo_limite_e_nao_chama_o_cerebro(
    env: Env, rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    await add_user(db_pool)
    assert env.post(fixture_bytes("audio")).status_code == 200
    await settle(rec, len(FULL) + 2)
    assert rec.events[: len(FULL) + 1] == [*FULL, "limit"]
    assert "provider" not in rec.events
    assert env.http_calls == []  # nada baixado


async def test_limite_estourado_bloqueia_antes_do_cerebro(
    rec: Recorder, db_pool: asyncpg.Pool
) -> None:
    e = spy_env(rec, rate_limit_per_hour=1)
    try:
        await add_user(db_pool)
        assert e.post(signed_payload(msg_id="wamid.ORD0001")).status_code == 200
        await settle(rec, len(FULL) + 3)
        rec.events.clear()
        assert e.post(signed_payload(msg_id="wamid.ORD0002")).status_code == 200
        await settle(rec, 99)
        assert rec.events[: len(FULL) + 1] == [*FULL, "limit"]
        assert "provider" not in rec.events  # a 2a mensagem nao chegou ao cerebro
        decision = await db_pool.fetchval(
            "SELECT decision FROM chat.message_trace WHERE wa_message_id = 'wamid.ORD0002'"
        )
        assert decision == "rate_limited"
    finally:
        e.client.__exit__(None, None, None)

