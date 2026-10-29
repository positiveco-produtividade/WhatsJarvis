"""Resposta fixa, "sair" e midia via pipeline, ponta a ponta (AC-28, AC-30, AC-36).

Usa Pipeline -> answering.answer_and_enqueue -> outbox.dispatch com FakeChannel. O canal falso
so sabe enviar: se algo tentasse baixar midia, nao haveria por onde. Dados fake.
"""
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import OutboundStatus, UserStatus
from app.services import answering, outbox, users
from app.services.access import AccessUser, can_send_proactive
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.pipeline import Pipeline
from app.services.reply_policy import FlagsReplyPolicy
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
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


async def build_env(pool: asyncpg.Pool, clock: FakeClock, **over: Any) -> Env:
    """Ambiente com um usuario ACTIVE criado pelo fluxo oficial (cadastro + botao Aceito)."""
    settings = make_settings(**over)
    flags = Flags(pool, clock)
    env = Env(pool, settings, clock, flags, Pipeline(pool, settings, flags, clock))
    person = users.ValidatedUser(
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
    async with pool.acquire() as conn, conn.transaction():
        await users.register(conn, person, actor="caio", settings=settings, clock=clock)
        notice = int(await conn.fetchval("SELECT id FROM access.optin_requests"))
    await env.pipe.handle_message(msg("wamid.acc", ContentKind.BUTTON, f"OPTIN_ACCEPT:{notice}"))
    return env


@pytest.fixture
async def env(db_pool: asyncpg.Pool, clock: FakeClock) -> Env:
    return await build_env(db_pool, clock)


def msg(
    wamid: str,
    kind: ContentKind = ContentKind.TEXT,
    payload: str | None = None,
    text: str = TEXT,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=PHONE),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=text if is_text else None,
        text_length=len(text) if is_text else None,
        mime_type={ContentKind.AUDIO: "audio/ogg", ContentKind.IMAGE: "image/jpeg"}.get(kind),
        button=ButtonReply(payload) if payload is not None else None,
    )


async def count(pool: asyncpg.Pool, table: str, where: str = "true") -> int:
    return int(await pool.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


async def trace_row(pool: asyncpg.Pool, wamid: str) -> asyncpg.Record:
    row = await pool.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = $1", wamid)
    assert row is not None
    return row


async def active_user_row(pool: asyncpg.Pool) -> asyncpg.Record:
    row = await pool.fetchrow("SELECT * FROM access.users")
    assert row is not None
    return row


@dataclass
class SpyProvider:
    texts: list[str] = field(default_factory=list)

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self.texts.append(ctx.text)
        return answering.Answer("under_construction", "nao usado")


# ---- AC-30: texto fixo ---------------------------------------------------------------------


async def test_texto_de_usuario_ativo_recebe_a_resposta_fixa_sem_modelo(env: Env) -> None:
    outcome = await env.pipe.handle_message(msg("wamid.q"))
    request = outcome.answer_request
    assert request is not None
    provider = answering.UnderConstructionProvider(env.settings)
    outbound_ids = await answering.answer_and_enqueue(
        env.pool, request, provider, clock=env.clock
    )
    assert len(outbound_ids) == 1
    outbound_id = outbound_ids[0]
    # ainda nada saiu: o envio e depois do commit, pelo despacho
    sent_before = len(env.channel.calls)
    status = await outbox.dispatch(env.pool, outbound_id, channel=env.channel, clock=env.clock)
    assert status is OutboundStatus.SENT
    assert sent_before == 0 and env.channel.methods == ["send_text"]
    assert env.channel.calls[0].body == env.settings.text_under_construction
    assert env.channel.calls[0].template is None
    tr = await trace_row(env.pool, "wamid.q")
    assert tr["body"] == TEXT  # corpo guardado no rastro
    assert tr["decision"] == "allowed" and tr["reply_kind"] == "under_construction"
    assert tr["send_result"] == "sent" and tr["counted_in_limit"]
    assert await count(env.pool, "ops.alerts") == 0


async def test_cerebro_so_e_chamado_para_texto_permitido(env: Env) -> None:
    spy = SpyProvider()
    for index, kind in enumerate((ContentKind.AUDIO, ContentKind.IMAGE, ContentKind.REACTION)):
        out = await env.pipe.handle_message(msg(f"wamid.m{index}", kind))
        assert out.answer_request is None
    text = await env.pipe.handle_message(msg("wamid.t"))
    assert text.answer_request is not None
    assert spy.texts == []  # o pipeline nunca chama o provedor sozinho


# ---- AC-28: sair ---------------------------------------------------------------------------


async def test_sair_liga_opt_out_confirma_e_a_consulta_continua(env: Env) -> None:
    out = await env.pipe.handle_message(msg("wamid.sair", text="Sair!"))
    assert out.answer_request is None and len(out.outbound_ids) == 1
    await outbox.dispatch(env.pool, out.outbound_ids[0], channel=env.channel, clock=env.clock)
    assert env.channel.calls[0].body == env.settings.text_opt_out_confirmed
    row = await active_user_row(env.pool)
    assert row["proactive_opt_out"] is True and row["status"] == "active"
    snapshot = AccessUser(
        status=UserStatus(row["status"]),
        accepted_at=row["accepted_at"],
        proactive_opt_out=row["proactive_opt_out"],
    )
    assert can_send_proactive(snapshot) is False
    assert can_send_proactive(
        AccessUser(status=UserStatus.ACTIVE, accepted_at=row["accepted_at"])
    ) is True
    nxt = await env.pipe.handle_message(msg("wamid.depois", text="qual o preco?"))
    assert nxt.answer_request is not None  # consulta segue funcionando


# ---- AC-36: audio e imagem -----------------------------------------------------------------


@pytest.mark.parametrize("kind", [ContentKind.AUDIO, ContentKind.IMAGE])
async def test_midia_abre_ticket_avisa_a_equipe_sem_alerta_nem_email(
    env: Env, kind: ContentKind
) -> None:
    out = await env.pipe.handle_message(msg("wamid.media", kind))
    assert out.answer_request is None and len(out.outbound_ids) == 1
    await outbox.dispatch(env.pool, out.outbound_ids[0], channel=env.channel, clock=env.clock)
    assert env.channel.methods == ["send_text"]  # so o aviso curto; nada baixado/interpretado
    assert env.channel.calls[0].body == env.settings.text_media_ack
    tr = await trace_row(env.pool, "wamid.media")
    assert tr["decision"] == "escalated_media" and tr["reply_kind"] == "media_ack"
    assert tr["body"] is None and tr["msg_type"] == kind.value
    ticket = await env.pool.fetchrow("SELECT * FROM chat.tickets")
    assert ticket is not None and ticket["kind"] == "media"
    assert ticket["trace_id"] == tr["id"] and ticket["ticket_number"] == tr["ticket_number"]
    assert ticket["user_id"] == tr["user_id"] and ticket["message"] != TEXT
    assert str(ticket["ticket_number"]).startswith("J-")
    assert await count(env.pool, "ops.alerts") == 0
    assert await count(env.pool, "ops.deliveries", "channel = 'email'") == 0
    delivery = await env.pool.fetchrow("SELECT * FROM ops.deliveries")
    assert delivery is not None and delivery["source_type"] == "ticket"
    assert delivery["status"] == "panel_only"  # grupo desligado: so painel


async def test_midia_com_grupo_ligado_avisa_o_grupo_mas_nunca_por_email(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    env = await build_env(
        db_pool,
        clock,
        team_group_enabled=True,
        waha_api_key=SecretStr("fake-waha"),
        team_group_chat_id="120363000000000000@g.us",
    )
    out = await env.pipe.handle_message(msg("wamid.media", ContentKind.AUDIO))
    channels = [r["channel"] for r in await db_pool.fetch("SELECT channel FROM ops.deliveries")]
    assert channels == ["group"] and len(out.delivery_ids) == 1
    assert await count(db_pool, "ops.alerts") == 0


async def test_midia_com_ack_desligado_cria_ticket_mas_nao_responde(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    env = await build_env(db_pool, clock, media_ack_enabled=False)
    sent_before = await count(db_pool, "chat.outbound_messages")
    out = await env.pipe.handle_message(msg("wamid.media", ContentKind.IMAGE))
    assert out.outbound_ids == ()
    tr = await trace_row(db_pool, "wamid.media")
    assert tr["reply_kind"] == "none" and tr["decision"] == "escalated_media"
    assert await count(db_pool, "chat.tickets") == 1
    assert await count(db_pool, "chat.outbound_messages") == sent_before
    assert await count(db_pool, "ops.alerts") == 0


async def test_pausa_silencia_o_aviso_de_midia_mas_o_ticket_continua(env: Env) -> None:
    async with env.pool.acquire() as conn, conn.transaction():
        await env.flags.set_bool(conn, "paused", True, "caio")
    env.clock.advance(seconds=31)
    out = await env.pipe.handle_message(msg("wamid.media", ContentKind.AUDIO))
    status = await outbox.dispatch(
        env.pool,
        out.outbound_ids[0],
        channel=env.channel,
        policy=FlagsReplyPolicy(env.flags),
        clock=env.clock,
    )
    assert status is OutboundStatus.DISCARDED and env.channel.calls == []
    assert await count(env.pool, "chat.tickets") == 1
    assert await count(env.pool, "ops.alerts") == 0
