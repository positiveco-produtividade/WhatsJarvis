"""Fluxo do WAHA ponta a ponta no banco (spec canal-waha.1; tasks WA-T12 a WA-T15 e WA-T20).

Cadastro sem envio (WA-11), aviso na 1a mensagem (WA-12), aceite e recusa por texto (WA-13),
enquete de marca depois do aceite (WA-15), resposta sem avaliacao (WA-14), regra "so responde"
(WA-16), envio da enquete e partes em ordem (WA-17). Dados ficticios, FakeChannel sem botoes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import OutboundStatus
from app.services import answering, brand_session, outbox, users, workers
from app.services.answering import AppliedEffects
from app.services.channel import WAHA_CAPABILITIES
from app.services.flags import Flags
from app.services.inbound import ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.brain import factories
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
# Telefone ficticio no formato dos testes (wa_id sem "+"); a chave sai do normalizador oficial.
PHONE = "5511910000002"
PHONE_KEY = normalize_phone("+" + PHONE).match_key


def waha_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "channel_provider": "waha",
        "jarvis_waha_api_key": SecretStr("k"),
        "jarvis_waha_hmac_key": SecretStr("h"),
        "jarvis_whatsapp_number": "+5500000000001",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def person(brands: tuple[str, ...] = ("jungle", "possible")) -> users.ValidatedUser:
    return users.ValidatedUser(
        name="Vendedora Teste",
        phone_e164="+" + PHONE,
        phone_key=PHONE_KEY,
        profile="vendedor",
        company="Acme",
        ufs=("CE",),
        channels=("farma",),
        brands=brands,
        manager_name="Gestora",
    )


def text_msg(wamid: str, text: str) -> InboundMessage:
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=PHONE),
        kind=ContentKind.TEXT,
        timestamp=int(NOW.timestamp()),
        text=text,
        text_length=len(text),
        raw_type="waha:message",
    )


@dataclass
class Env:
    pool: asyncpg.Pool
    settings: Settings
    clock: FakeClock
    pipeline: Pipeline


@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Env:
    clock = FakeClock(NOW)
    settings = waha_settings()
    return Env(db_pool, settings, clock, Pipeline(db_pool, settings, Flags(db_pool, clock), clock))


async def register(env: Env, brands: tuple[str, ...] = ("jungle", "possible")) -> users.UserRow:
    async with env.pool.acquire() as conn, conn.transaction():
        return await users.register(
            conn, person(brands), actor="caio", settings=env.settings, clock=env.clock
        )


async def outbound(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(await conn.fetch("SELECT * FROM chat.outbound_messages ORDER BY id"))


async def user_row(pool: asyncpg.Pool) -> asyncpg.Record:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM access.users")
    assert row is not None
    return row


# ---------------------------------------------------------------- opt-in (WA-11 a WA-13)


async def test_cadastro_nao_envia_nada(env: Env) -> None:
    await register(env)
    assert await outbound(env.pool) == []
    async with env.pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM access.optin_requests") == "open"


async def test_primeira_mensagem_recebe_o_aviso_completo(env: Env) -> None:
    await register(env)
    await env.pipeline.handle_message(text_msg("w1", "Oi Jarvis"))
    [row] = await outbound(env.pool)
    assert row["kind"] == "optin_reminder"
    assert row["body"] == env.settings.text_optin_notice
    assert "ACEITO" in row["body"]


async def test_sim_na_primeira_mensagem_nao_aceita(env: Env) -> None:
    await register(env)
    await env.pipeline.handle_message(text_msg("w1", "sim"))
    assert (await user_row(env.pool))["status"] == "pending_optin"


async def test_aceito_por_texto_ativa_e_manda_enquete_de_marca(env: Env) -> None:
    await register(env)
    await env.pipeline.handle_message(text_msg("w1", "Oi Jarvis"))
    env.clock.advance(minutes=1)
    result = await env.pipeline.handle_message(text_msg("w2", "ACEITO"))
    user = await user_row(env.pool)
    assert user["status"] == "active" and user["accepted_at"] is not None
    rows = await outbound(env.pool)
    assert [r["kind"] for r in rows] == ["optin_reminder", "optin_confirmation", "brand_poll"]
    poll = rows[-1]
    assert poll["body"] == env.settings.text_brand_poll_start
    assert [o["title"] for o in json.loads(poll["reply_buttons"])] == [
        "Jungle", "Possible",
    ]  # fmt: skip
    assert result.outbound_ids == (rows[1]["id"], rows[2]["id"])  # confirmacao antes da enquete


async def test_aceite_com_uma_marca_fixa_a_marca_sem_enquete(env: Env) -> None:
    await register(env, brands=("zaya",))
    await env.pipeline.handle_message(text_msg("w1", "Oi"))
    await env.pipeline.handle_message(text_msg("w2", "aceito"))
    assert [r["kind"] for r in await outbound(env.pool)] == [
        "optin_reminder", "optin_confirmation",
    ]  # fmt: skip
    assert (await user_row(env.pool))["active_brand"] == "zaya"


async def test_nao_por_texto_recusa(env: Env) -> None:
    await register(env)
    await env.pipeline.handle_message(text_msg("w1", "Oi"))
    await env.pipeline.handle_message(text_msg("w2", "NÃO"))
    assert (await user_row(env.pool))["status"] == "declined"


# ---------------------------------------------------------------- respostas sem avaliacao (WA-14)


class _RateEffects:
    async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects:
        return AppliedEffects(ticket_id=1, ticket_number=None, rate=True)


class _RateProvider:
    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        return answering.Answer(
            kind="brain_answer_rate", text="Resposta.", parts=("Parte 1.", "Parte 2."),
            effects=_RateEffects(),
        )  # fmt: skip


async def _active_with_pending_trace(env: Env) -> tuple[int, int]:
    user = await register(env)
    async with env.pool.acquire() as conn:
        await conn.execute(
            "UPDATE access.users SET status = 'active', accepted_at = $2, "
            "accepted_notice_version = 'v' WHERE id = $1", user.id, NOW,
        )  # fmt: skip
        trace_id = await factories.create_pending_trace(conn, user.id, NOW, wa_message_id="w9")
    return user.id, trace_id


async def test_resposta_sem_botoes_de_avaliacao(env: Env) -> None:
    user_id, trace_id = await _active_with_pending_trace(env)
    ctx = answering.AnswerContext(
        user_id=user_id, profile="vendedor", ufs=["CE"], channels=["farma"],
        brands=["jungle"], text="pergunta", inbound_wa_message_id="w9", received_at=NOW,
    )  # fmt: skip
    request = answering.AnswerRequest(trace_id=trace_id, phone_e164="+" + PHONE, context=ctx)
    await answering.answer_and_enqueue(env.pool, request, _RateProvider(), rating=False)
    rows = await outbound(env.pool)
    assert [r["kind"] for r in rows] == ["brain_answer", "brain_answer"]
    assert all(r["reply_buttons"] is None for r in rows)
    async with env.pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT reply_kind FROM chat.message_trace WHERE id = $1", trace_id
        ) == "brain_answer"


# ---------------------------------------------------------------- saida (WA-16, WA-17)


async def _enqueue(env: Env, user_id: int, kind: str, *, trace_id: int | None = None,
                   **extra: Any) -> int:  # fmt: skip
    target = outbox._Recipient(user_id, "+" + PHONE, None)  # noqa: SLF001
    async with env.pool.acquire() as conn:
        return await outbox.enqueue(
            conn, target, kind, inbound_trace_id=trace_id, clock=env.clock, **extra
        )


async def test_so_responde_descarta_quem_nao_escreveu_em_24h(env: Env) -> None:
    user_id, trace_id = await _active_with_pending_trace(env)
    async with env.pool.acquire() as conn:
        await conn.execute(
            "UPDATE chat.message_trace SET received_at = $1", NOW - timedelta(hours=25)
        )
    out_id = await _enqueue(env, user_id, "brain_notice", body="Oi")
    channel = FakeChannel(capabilities=WAHA_CAPABILITIES)
    status = await outbox.dispatch(
        env.pool, out_id, channel=channel, clock=env.clock, reply_only=True
    )
    assert status is OutboundStatus.DISCARDED and channel.calls == []
    [row] = await outbound(env.pool)
    assert row["discard_reason"] == "no_inbound_window"


async def test_so_responde_envia_a_quem_escreveu(env: Env) -> None:
    user_id, _ = await _active_with_pending_trace(env)
    out_id = await _enqueue(env, user_id, "brain_notice", body="Oi")
    channel = FakeChannel(capabilities=WAHA_CAPABILITIES)
    status = await outbox.dispatch(
        env.pool, out_id, channel=channel, clock=env.clock, reply_only=True
    )
    assert status is OutboundStatus.SENT and channel.methods == ["send_text"]


async def test_enquete_sai_como_enquete(env: Env) -> None:
    user_id, _ = await _active_with_pending_trace(env)
    async with env.pool.acquire() as conn:
        target = outbox._Recipient(user_id, "+" + PHONE, None)  # noqa: SLF001
        out_id = await brand_session.enqueue_poll(
            conn, target, "Sobre qual marca?", ["possible", "jungle"], clock=env.clock
        )
    channel = FakeChannel(capabilities=WAHA_CAPABILITIES)
    status = await outbox.dispatch(env.pool, out_id, channel=channel, clock=env.clock)
    assert status is OutboundStatus.SENT
    [call] = channel.calls
    assert call.method == "send_poll" and call.body == "Sobre qual marca?"
    assert [o.title for o in call.options] == ["Jungle", "Possible"]


async def test_varredura_nao_pega_parte_2_com_a_1_pendente(env: Env) -> None:
    user_id, trace_id = await _active_with_pending_trace(env)
    first = await _enqueue(env, user_id, "brain_answer", body="Parte 1", trace_id=trace_id)
    second = await _enqueue(env, user_id, "brain_answer", body="Parte 2", trace_id=trace_id)
    async with env.pool.acquire() as conn:
        due = [r["id"] for r in await conn.fetch(workers._DUE_OUTBOUND, NOW, 50)]  # noqa: SLF001
        assert due == [first]
        await conn.execute("UPDATE chat.outbound_messages SET status = 'sent' WHERE id = $1", first)
        due = [r["id"] for r in await conn.fetch(workers._DUE_OUTBOUND, NOW, 50)]  # noqa: SLF001
        assert due == [second]
