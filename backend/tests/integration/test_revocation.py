"""Revogacao, troca de numero e reativacao via pipeline (AC-22, AC-23, AC-26, AC-35).

O ponto central e a corrida AC-23: a revogacao acontece ENTRE o commit do pedido de resposta
e o envio; o despacho relê o banco e descarta (`revoked_before_send`). Dados fake.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import DuplicatePhone
from app.core.values import OutboundStatus
from app.services import answering, outbox, users
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.pipeline import Pipeline
from app.services.reply_policy import FlagsReplyPolicy
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
NEW_PHONE = "5511955554444"
TEXT = "texto-secreto-do-usuario"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "welcome_template_name": "boas_vindas_jarvis",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@dataclass
class Env:
    pool: asyncpg.Pool
    settings: Settings
    clock: FakeClock
    flags: Flags
    pipe: Pipeline
    channel: FakeChannel = field(default_factory=FakeChannel)


@pytest.fixture
def env(db_pool: asyncpg.Pool, clock: FakeClock) -> Env:
    settings = make_settings()
    flags = Flags(db_pool, clock)
    return Env(db_pool, settings, clock, flags, Pipeline(db_pool, settings, flags, clock))


def msg(
    wamid: str,
    kind: ContentKind = ContentKind.TEXT,
    payload: str | None = None,
    wa_id: str = PHONE,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=wa_id),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=TEXT if is_text else None,
        text_length=len(TEXT) if is_text else None,
        button=ButtonReply(payload) if payload is not None else None,
    )


def person() -> users.ValidatedUser:
    return users.ValidatedUser(
        name="Ana Teste",
        phone_e164="+" + PHONE,
        phone_key="551187654321",
        profile="vendedor",
        company="Acme",
        ufs=("SP",),
        channels=("farma",),
        brands=("jungle",),
        manager_name="Gestora",
    )


async def active_user(env: Env) -> users.UserRow:
    """Cadastro oficial + aceite pelo botao, como em producao."""
    async with env.pool.acquire() as conn, conn.transaction():
        user = await users.register(
            conn, person(), actor="caio", settings=env.settings, clock=env.clock
        )
        notice = int(await conn.fetchval("SELECT id FROM access.optin_requests"))
    await env.pipe.handle_message(msg("wamid.acc", ContentKind.BUTTON, f"OPTIN_ACCEPT:{notice}"))
    assert (await user_row(env.pool, user.id))["status"] == "active"
    return user


async def revoke(env: Env, uid: int, reason: str = "left_company") -> None:
    async with env.pool.acquire() as conn, conn.transaction():
        await users.revoke(conn, uid, reason, "caio", clock=env.clock)


async def count(pool: asyncpg.Pool, table: str, where: str = "true") -> int:
    return int(await pool.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


async def user_row(pool: asyncpg.Pool, uid: int) -> asyncpg.Record:
    row = await pool.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
    assert row is not None
    return row


async def trace_row(pool: asyncpg.Pool, wamid: str) -> asyncpg.Record:
    row = await pool.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = $1", wamid)
    assert row is not None
    return row


class RevokingProvider:
    """Hook: o cerebro 'demora' e, enquanto isso, o administrador revoga (AC-23)."""

    def __init__(self, env: Env, uid: int) -> None:
        self._env, self._uid = env, uid
        self.calls = 0

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self.calls += 1
        await revoke(self._env, self._uid)
        return answering.Answer("under_construction", "resposta que nao pode sair")


@dataclass
class FixedProvider:
    calls: list[str] = field(default_factory=list)

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self.calls.append(ctx.text)
        return answering.Answer("under_construction", "em construcao")


# ---- AC-22: a mensagem seguinte ja e bloqueada -------------------------------------------


async def test_revogado_e_bloqueado_na_mensagem_seguinte_e_depois_de_60s(env: Env) -> None:
    user = await active_user(env)
    first = await env.pipe.handle_message(msg("wamid.ok"))
    assert first.answer_request is not None  # ainda ativo
    await revoke(env, user.id)  # sem esperar cache: a decisao le o banco
    nxt = await env.pipe.handle_message(msg("wamid.next"))
    env.clock.advance(seconds=61)
    later = await env.pipe.handle_message(msg("wamid.later"))
    for out, wamid in ((nxt, "wamid.next"), (later, "wamid.later")):
        assert out.answer_request is None and out.outbound_ids == ()
        tr = await trace_row(env.pool, wamid)
        assert tr["decision"] == "revoked" and tr["body"] is None
        assert tr["reply_kind"] in (None, "none")
    assert await count(env.pool, "ops.alerts") == 0  # tentativa registrada, sem alerta
    assert await count(env.pool, "chat.tickets") == 0


async def test_revogar_duas_vezes_nao_muda_motivo_nem_data(env: Env) -> None:
    user = await active_user(env)
    await revoke(env, user.id, "left_company")
    env.clock.advance(hours=1)
    await revoke(env, user.id, "other")
    row = await user_row(env.pool, user.id)
    assert row["revoked_reason"] == "left_company" and row["revoked_at"] == NOW
    assert await count(env.pool, "ops.audit_log", "action = 'user.revoked'") == 1


# ---- AC-23: revogacao entre o commit e o envio ---------------------------------------------


async def test_revogacao_entre_o_commit_e_o_envio_descarta_a_resposta(env: Env) -> None:
    user = await active_user(env)
    outcome = await env.pipe.handle_message(msg("wamid.q"))
    request = outcome.answer_request
    assert request is not None
    provider = RevokingProvider(env, user.id)
    outbound_ids = await answering.answer_and_enqueue(env.pool, request, provider, clock=env.clock)
    assert len(outbound_ids) == 1 and provider.calls == 1
    outbound_id = outbound_ids[0]
    assert (await user_row(env.pool, user.id))["status"] == "revoked"
    status = await outbox.dispatch(env.pool, outbound_id, channel=env.channel, clock=env.clock)
    assert status is OutboundStatus.DISCARDED
    assert env.channel.calls == []  # nada saiu
    row = await env.pool.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", outbound_id)
    assert row is not None
    assert row["status"] == "discarded" and row["discard_reason"] == "revoked_before_send"
    tr = await trace_row(env.pool, "wamid.q")
    assert tr["decision"] == "revoked_before_send" and tr["send_result"] == "discarded"
    assert tr["reply_sent_at"] is None


async def test_resposta_ja_na_fila_e_descartada_se_revogar_antes_do_despacho(env: Env) -> None:
    user = await active_user(env)
    outcome = await env.pipe.handle_message(msg("wamid.q"))
    assert outcome.answer_request is not None
    provider = FixedProvider()
    outbound_ids = await answering.answer_and_enqueue(
        env.pool, outcome.answer_request, provider, clock=env.clock
    )
    assert len(outbound_ids) == 1
    outbound_id = outbound_ids[0]
    await revoke(env, user.id, "partner_contract_end")
    status = await outbox.dispatch(env.pool, outbound_id, channel=env.channel, clock=env.clock)
    assert status is OutboundStatus.DISCARDED and env.channel.calls == []


# ---- AC-26: troca de numero --------------------------------------------------------------


async def test_troca_de_numero_revoga_o_antigo_e_cria_o_novo_com_novo_aceite(env: Env) -> None:
    old = await active_user(env)
    async with env.pool.acquire() as conn:
        revoked, created = await users.swap_number(
            conn, "+" + PHONE, "+" + NEW_PHONE, "caio", settings=env.settings, clock=env.clock
        )
    assert revoked.id == old.id and created.id != old.id
    old_row, new_row = await user_row(env.pool, old.id), await user_row(env.pool, created.id)
    assert old_row["status"] == "revoked" and old_row["revoked_reason"] == "number_change"
    assert new_row["status"] == "pending_optin" and new_row["accepted_at"] is None
    assert (new_row["name"], new_row["company"]) == (old_row["name"], old_row["company"])
    # numero antigo: silencio; numero novo: so o aviso ate tocar em Aceito
    gone = await env.pipe.handle_message(msg("wamid.old"))
    assert gone.outbound_ids == () and gone.answer_request is None
    assert (await trace_row(env.pool, "wamid.old"))["decision"] == "revoked"
    welcome = await env.pool.fetchrow(
        "SELECT id FROM chat.outbound_messages WHERE user_id = $1 AND kind = 'welcome_template'",
        created.id,
    )
    assert welcome is not None
    assert await outbox.dispatch(
        env.pool, welcome["id"], channel=env.channel, clock=env.clock
    ) is OutboundStatus.SENT
    assert env.channel.calls[-1].method == "send_template"
    assert env.channel.calls[-1].to == "+" + NEW_PHONE
    typed = await env.pipe.handle_message(msg("wamid.new", wa_id=NEW_PHONE))
    assert typed.answer_request is None  # texto nao aceita; so lembrete
    assert (await user_row(env.pool, created.id))["status"] == "pending_optin"


async def test_troca_para_numero_ja_cadastrado_desfaz_tudo(env: Env) -> None:
    old = await active_user(env)
    async with env.pool.acquire() as conn, conn.transaction():
        await users.register(
            conn,
            users.ValidatedUser(
                **{**person().__dict__, "phone_e164": "+" + NEW_PHONE, "phone_key": "551155554444"}
            ),
            actor="caio",
            enqueue_welcome=False,
            clock=env.clock,
        )
    async with env.pool.acquire() as conn:
        with pytest.raises(DuplicatePhone):
            await users.swap_number(
                conn, "+" + PHONE, "+" + NEW_PHONE, "caio", settings=env.settings, clock=env.clock
            )
    assert (await user_row(env.pool, old.id))["status"] == "active"
    assert await count(env.pool, "access.users") == 2


# ---- AC-35: reativacao exige novo aceite -------------------------------------------------


async def test_reativar_revogado_zera_aceite_e_exige_tocar_em_aceito_de_novo(env: Env) -> None:
    user = await active_user(env)
    await revoke(env, user.id)
    env.clock.advance(hours=3)
    async with env.pool.acquire() as conn, conn.transaction():
        await users.resend(conn, user.id, "caio", settings=env.settings, clock=env.clock)
    row = await user_row(env.pool, user.id)
    assert row["status"] == "pending_optin" and row["accepted_at"] is None
    assert row["revoked_at"] is None and row["revoked_reason"] is None
    old = await env.pool.fetchval(
        "SELECT old_value->>'revoked_reason' FROM ops.audit_log WHERE action = 'user.resent'"
    )
    assert old == "left_company"  # historico na auditoria
    # sem acesso ate aceitar: texto vira lembrete, nunca resposta
    out = await env.pipe.handle_message(msg("wamid.pend"))
    assert out.answer_request is None and len(out.outbound_ids) == 1
    notice = int(
        await env.pool.fetchval("SELECT id FROM access.optin_requests WHERE status = 'open'")
    )
    await env.pipe.handle_message(msg("wamid.acc2", ContentKind.BUTTON, f"OPTIN_ACCEPT:{notice}"))
    again = await user_row(env.pool, user.id)
    assert again["status"] == "active" and again["accepted_at"] == env.clock.now()


async def test_reenvio_para_ativo_e_recusado_sem_efeitos(env: Env) -> None:
    user = await active_user(env)
    before = await count(env.pool, "chat.outbound_messages")
    async with env.pool.acquire() as conn:
        with pytest.raises(users.UserAlreadyActive):
            await users.resend(conn, user.id, "caio", settings=env.settings, clock=env.clock)
    assert (await user_row(env.pool, user.id))["status"] == "active"
    assert await count(env.pool, "chat.outbound_messages") == before


async def test_pausa_silencia_resposta_mas_nao_boas_vindas_nem_aceite(env: Env) -> None:
    policy = FlagsReplyPolicy(env.flags)
    async with env.pool.acquire() as conn, conn.transaction():
        user = await users.register(
            conn, person(), actor="caio", settings=env.settings, clock=env.clock
        )
        welcome = int(await conn.fetchval("SELECT id FROM chat.outbound_messages"))
        notice = int(await conn.fetchval("SELECT id FROM access.optin_requests"))
        await env.flags.set_bool(conn, "paused", True, "caio")
    env.clock.advance(seconds=31)  # passa o cache de 30 s
    assert await outbox.dispatch(
        env.pool, welcome, channel=env.channel, policy=policy, clock=env.clock
    ) is OutboundStatus.SENT
    accepted = await env.pipe.handle_message(
        msg("wamid.acc", ContentKind.BUTTON, f"OPTIN_ACCEPT:{notice}")
    )
    assert await outbox.dispatch(
        env.pool, accepted.outbound_ids[0], channel=env.channel, policy=policy, clock=env.clock
    ) is OutboundStatus.SENT
    asked = await env.pipe.handle_message(msg("wamid.q"))
    assert asked.answer_request is not None
    out_ids = await answering.answer_and_enqueue(
        env.pool, asked.answer_request, FixedProvider(), clock=env.clock
    )
    assert len(out_ids) == 1
    out_id = out_ids[0]
    status = await outbox.dispatch(
        env.pool, out_id, channel=env.channel, policy=policy, clock=env.clock
    )
    assert status is OutboundStatus.DISCARDED
    assert env.channel.methods == ["send_template", "send_text"]  # boas-vindas e confirmacao
    row = await env.pool.fetchrow(
        "SELECT discard_reason FROM chat.outbound_messages WHERE id = $1", out_id
    )
    assert row is not None and row["discard_reason"] == "paused"
    assert (await user_row(env.pool, user.id))["status"] == "active"
