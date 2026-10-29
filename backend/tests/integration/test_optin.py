"""Aceite via pipeline, ponta a ponta: cadastro, boas-vindas, aceite, recusa, lembrete.

Cobre AC-17, AC-18, AC-19, AC-20 e AC-21 usando users.register/resend, outbox.dispatch e
FakeChannel (sem rede). Dados fake.
"""
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import OutboundStatus
from app.services import outbox, users
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.pipeline import Pipeline
from app.services.replies import ACCEPT_BUTTON_TITLE, DECLINE_BUTTON_TITLE
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
SECRET_TEXT = "aceito"


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


def msg(
    wamid: str,
    kind: ContentKind = ContentKind.TEXT,
    text: str | None = SECRET_TEXT,
    payload: str | None = None,
    bsuid: str | None = None,
) -> InboundMessage:
    is_text = kind is ContentKind.TEXT
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=PHONE, bsuid=bsuid),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=text if is_text else None,
        text_length=len(text or "") if is_text else None,
        mime_type="audio/ogg" if kind is ContentKind.AUDIO else None,
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


async def register(
    pool: asyncpg.Pool, settings: Settings, clock: FakeClock
) -> tuple[users.UserRow, int, int]:
    """Cadastra pelo servico oficial; devolve (usuario, id do aviso, id da saida)."""
    async with pool.acquire() as conn, conn.transaction():
        user = await users.register(conn, person(), actor="caio", settings=settings, clock=clock)
        notice_id = int(await conn.fetchval("SELECT id FROM access.optin_requests"))
        outbound_id = int(await conn.fetchval("SELECT id FROM chat.outbound_messages"))
    return user, notice_id, outbound_id


def pipeline(pool: asyncpg.Pool, settings: Settings, clock: FakeClock) -> Pipeline:
    return Pipeline(pool, settings, Flags(pool, clock), clock)


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


async def send_all(
    pool: asyncpg.Pool, ids: tuple[int, ...], channel: FakeChannel, clock: FakeClock
) -> list[OutboundStatus | None]:
    return [await outbox.dispatch(pool, i, channel=channel, clock=clock) for i in ids]


# ---- AC-17: boas-vindas so por modelo ---------------------------------------------------


async def test_boas_vindas_sai_so_como_modelo_aprovado(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    settings = make_settings()
    user, notice_id, welcome_id = await register(db_pool, settings, clock)
    channel = FakeChannel()
    status = await outbox.dispatch(db_pool, welcome_id, channel=channel, clock=clock)
    assert status is OutboundStatus.SENT
    assert channel.methods == ["send_template"]  # nunca texto livre
    call = channel.calls[0]
    assert call.to == "+" + PHONE and call.template is not None
    assert call.template.name == "boas_vindas_jarvis"
    assert call.template.body_params == ("Ana Teste",)
    assert call.template.quick_reply_payloads == (
        f"OPTIN_ACCEPT:{notice_id}",
        f"OPTIN_DECLINE:{notice_id}",
    )
    assert (await user_row(db_pool, user.id))["status"] == "pending_optin"


async def test_falha_da_boas_vindas_mantem_pendente_alerta_com_email_e_permite_reenvio(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    settings = make_settings()
    user, _, welcome_id = await register(db_pool, settings, clock)
    channel = FakeChannel()
    channel.fail_always("definitive", "131047")
    status = await outbox.dispatch(db_pool, welcome_id, channel=channel, clock=clock)
    assert status is OutboundStatus.FAILED
    async with db_pool.acquire() as conn, conn.transaction():
        alert_id = await outbox.check_send_failures(
            conn, welcome_id, settings=settings, flags=Flags(db_pool, clock), clock=clock
        )
    assert alert_id is not None
    assert (await user_row(db_pool, user.id))["status"] == "pending_optin"
    alert = await db_pool.fetchrow("SELECT * FROM ops.alerts")
    assert alert is not None and alert["kind"] == "welcome_failed" and alert["user_id"] == user.id
    assert await count(db_pool, "ops.deliveries", "channel = 'email'") == 1
    # reenvio pelo administrador: novo aviso, novo modelo, desta vez entregue
    async with db_pool.acquire() as conn, conn.transaction():
        await users.resend(conn, user.id, "caio", settings=settings, clock=clock)
    new_id = int(
        await db_pool.fetchval(
            "SELECT max(id) FROM chat.outbound_messages WHERE kind = 'welcome_template'"
        )
    )
    assert new_id != welcome_id
    ok = FakeChannel()
    assert await outbox.dispatch(db_pool, new_id, channel=ok, clock=clock) is OutboundStatus.SENT
    assert ok.methods == ["send_template"]


# ---- AC-18: aceite ----------------------------------------------------------------------


async def test_aceito_ativa_associa_bsuid_e_envia_confirmacao(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    settings = make_settings()
    user, notice_id, welcome_id = await register(db_pool, settings, clock)
    channel = FakeChannel()
    await outbox.dispatch(db_pool, welcome_id, channel=channel, clock=clock)
    clock.advance(minutes=5)
    outcome = await pipeline(db_pool, settings, clock).handle_message(
        msg("wamid.acc", ContentKind.BUTTON, payload=f"OPTIN_ACCEPT:{notice_id}", bsuid="BR.xyz")
    )
    row = await user_row(db_pool, user.id)
    assert row["status"] == "active" and row["accepted_at"] == clock.now()
    assert row["bsuid"] == "BR.xyz" and row["phone_key"] == "551187654321"
    assert await db_pool.fetchval("SELECT status FROM access.optin_requests") == "accepted"
    await send_all(db_pool, outcome.outbound_ids, channel, clock)
    assert channel.methods == ["send_template", "send_text"]
    assert channel.calls[-1].body == settings.text_optin_confirmed
    assert (await trace_row(db_pool, "wamid.acc"))["decision"] == "optin_accepted"


# ---- AC-19: botao antigo ----------------------------------------------------------------


@pytest.mark.parametrize("final", ["active", "declined", "revoked"])
async def test_botao_aceito_antigo_nao_muda_nada(
    db_pool: asyncpg.Pool, clock: FakeClock, final: str
) -> None:
    settings = make_settings()
    user, notice_id, _ = await register(db_pool, settings, clock)
    pipe = pipeline(db_pool, settings, clock)
    if final == "declined":
        await pipe.handle_message(
            msg("wamid.d", ContentKind.BUTTON, payload=f"OPTIN_DECLINE:{notice_id}")
        )
    else:
        await pipe.handle_message(
            msg("wamid.first", ContentKind.BUTTON, payload=f"OPTIN_ACCEPT:{notice_id}")
        )
    if final == "revoked":
        async with db_pool.acquire() as conn, conn.transaction():
            await users.revoke(conn, user.id, "left_company", "caio", clock=clock)
    before = await user_row(db_pool, user.id)
    outbound_before = await count(db_pool, "chat.outbound_messages")
    clock.advance(hours=2)
    await pipe.handle_message(
        msg("wamid.old", ContentKind.BUTTON, payload=f"OPTIN_ACCEPT:{notice_id}")
    )
    after = await user_row(db_pool, user.id)
    assert after["status"] == before["status"] == final
    assert after["accepted_at"] == before["accepted_at"]
    assert after["status_changed_at"] == before["status_changed_at"]
    confirmations = await count(db_pool, "chat.outbound_messages", "kind = 'optin_confirmation'")
    assert confirmations == (0 if final == "declined" else 1)
    if final != "active":
        assert await count(db_pool, "chat.outbound_messages") == outbound_before  # silencio


# ---- AC-20: recusa ----------------------------------------------------------------------


async def test_nao_aceito_recebe_so_o_texto_de_recusa_e_depois_silencio(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    settings = make_settings()
    user, notice_id, welcome_id = await register(db_pool, settings, clock)
    channel = FakeChannel()
    await outbox.dispatch(db_pool, welcome_id, channel=channel, clock=clock)
    pipe = pipeline(db_pool, settings, clock)
    outcome = await pipe.handle_message(
        msg("wamid.no", ContentKind.BUTTON, payload=f"OPTIN_DECLINE:{notice_id}")
    )
    await send_all(db_pool, outcome.outbound_ids, channel, clock)
    assert channel.methods == ["send_template", "send_text"]
    assert channel.calls[-1].body == settings.text_optin_declined
    assert (await user_row(db_pool, user.id))["status"] == "declined"
    clock.advance(hours=30)
    later = await pipe.handle_message(msg("wamid.later", text="oi"))
    assert later.outbound_ids == () and later.answer_request is None
    tr = await trace_row(db_pool, "wamid.later")
    assert tr["decision"] == "declined" and tr["body"] is None
    assert await count(db_pool, "chat.outbound_messages") == 2  # boas-vindas + recusa
    assert await count(db_pool, "ops.alerts") == 0


# ---- AC-21: texto/audio no lugar do botao -------------------------------------------------


async def test_texto_no_lugar_do_botao_nao_aceita_e_o_lembrete_sai_1_vez_por_dia(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    settings = make_settings()
    user, notice_id, _ = await register(db_pool, settings, clock)
    pipe = pipeline(db_pool, settings, clock)
    channel = FakeChannel()
    first = await pipe.handle_message(msg("wamid.t1", text="aceito"))
    await send_all(db_pool, first.outbound_ids, channel, clock)
    assert channel.methods == ["send_reply_buttons"]
    buttons = channel.calls[0].buttons
    assert [(b.id, b.title) for b in buttons] == [
        (f"OPTIN_ACCEPT:{notice_id}", ACCEPT_BUTTON_TITLE),
        (f"OPTIN_DECLINE:{notice_id}", DECLINE_BUTTON_TITLE),
    ]
    clock.advance(hours=2)
    again = await pipe.handle_message(msg("wamid.t2", text="aceito sim"))
    audio = await pipe.handle_message(msg("wamid.t3", ContentKind.AUDIO))
    assert again.outbound_ids == () and audio.outbound_ids == ()
    clock.advance(hours=23)  # 25 h depois do primeiro lembrete
    third = await pipe.handle_message(msg("wamid.t4", ContentKind.AUDIO))
    assert len(third.outbound_ids) == 1
    row = await user_row(db_pool, user.id)
    assert row["status"] == "pending_optin" and row["accepted_at"] is None
    assert await db_pool.fetchval("SELECT status FROM access.optin_requests") == "open"
    # nenhum conteudo guardado; midia nao baixada nem virou ticket/alerta/entrega
    assert await count(db_pool, "chat.message_trace", "body IS NOT NULL") == 0
    for table in ("chat.tickets", "ops.alerts", "ops.deliveries"):
        assert await count(db_pool, table) == 0, table
    assert set(channel.methods) <= {"send_reply_buttons"}  # o fake nem tem download
    assert clock.now() - NOW == timedelta(hours=25)
