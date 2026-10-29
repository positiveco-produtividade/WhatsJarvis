"""Pipeline parte 2: opt-in, usuario ativo, limite e midia (AC-18/19/20/21/28/29/30/36/31/32)."""
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.values import MessageOutcome
from app.services import answering, outbox
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "5511987654321"
SECRET_TEXT = "texto-secreto-do-usuario"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


def make_pipeline(pool: asyncpg.Pool, clock: FakeClock, **over: Any) -> Pipeline:
    return Pipeline(pool, make_settings(**over), Flags(pool, clock), clock)


def msg(
    wamid: str = "wamid.1",
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


async def add_user(conn: asyncpg.Connection, status: str = "active", **extra: Any) -> int:
    num = normalize_phone("+" + PHONE)
    uid = int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version, optin_reminder_at, "
            "rate_limit_notice_at, revoked_at, revoked_by) VALUES ('Ana', $1, $2, 'vendedor', "
            "'Acme', ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], 'Gestora', $3, $4, $4, "
            "'teste', $4, CASE WHEN $3 = 'active' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'active' THEN 'v1' END, $5, $6, "
            "CASE WHEN $3 = 'revoked' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'revoked' THEN 'teste' END) RETURNING id",
            num.phone_e164,
            num.match_key,
            status,
            NOW,
            extra.get("optin_reminder_at"),
            extra.get("rate_limit_notice_at"),
        )
    )
    if status == "pending_optin":
        await open_notice(conn, uid)
    return uid


async def open_notice(conn: asyncpg.Connection, uid: int) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, "
            "issued_by) VALUES ($1, 'open', 'v1', $2, 'teste') RETURNING id",
            uid,
            NOW,
        )
    )


async def count(conn: asyncpg.Connection, table: str, where: str = "true") -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))


async def seed_counted(
    conn: asyncpg.Connection, uid: int, n: int, at: datetime, tag: str = "seed"
) -> None:
    await conn.execute(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, counted_in_limit) "
        "SELECT $4::text || '.' || g, $2, 'text', $1, 'allowed', 'allowed', true "
        "FROM generate_series(1, $3::int) g",
        uid,
        at,
        n,
        tag,
    )


@dataclass
class SpyAnswerProvider:
    contexts: list[answering.AnswerContext] = field(default_factory=list)

    async def answer(self, ctx: answering.AnswerContext) -> answering.Answer:
        self.contexts.append(ctx)
        return answering.Answer("under_construction", "em construcao")


async def trace_row(conn: asyncpg.Connection, wamid: str = "wamid.1") -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE wa_message_id = $1", wamid)
    assert row is not None
    return row


# ---- texto autorizado ----------------------------------------------------------------


async def test_texto_ativo_vira_pedido_de_resposta_sem_enviar(
    db_pool: asyncpg.Pool, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
    pipe = make_pipeline(db_pool, clock)
    spy = SpyAnswerProvider()
    with caplog.at_level(logging.DEBUG):
        outcome = await pipe.handle_message(msg())
    assert spy.contexts == []  # o pipeline nunca chama o cerebro; so devolve o pedido
    req = outcome.answer_request
    assert isinstance(req, answering.AnswerRequest)
    assert outcome.outbound_ids == () and outcome.delivery_ids == ()
    assert req.context.text == SECRET_TEXT and req.context.user_id == uid
    assert req.context.profile == "vendedor" and req.context.ufs == ["SP"]
    async with db_pool.acquire() as conn:
        tr = await trace_row(conn)
        assert tr["decision"] == "allowed" and tr["body"] == SECRET_TEXT
        assert tr["reply_kind"] == "pending" and tr["counted_in_limit"]
        assert req.trace_id == tr["id"]
        assert await count(conn, "ops.alerts") == 0
        assert await count(conn, "chat.outbound_messages") == 0
    assert SECRET_TEXT not in caplog.text and PHONE not in caplog.text
    await answering.answer_and_enqueue(db_pool, req, spy)
    assert len(spy.contexts) == 1


async def test_cerebro_nao_roda_para_quem_nao_e_texto_allowed(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_user(conn)
    pipe = make_pipeline(db_pool, clock)
    outs = [
        await pipe.handle_message(msg("a", ContentKind.AUDIO)),
        await pipe.handle_message(msg("b", ContentKind.REACTION)),
        await pipe.handle_message(msg("c", text="sair")),
    ]
    assert all(o.answer_request is None for o in outs)


# ---- sair ----------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["sair", "  Sair! ", "SAIR."])
async def test_sair_liga_opt_out_e_confirma(
    db_pool: asyncpg.Pool, clock: FakeClock, text: str
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
    outcome = await make_pipeline(db_pool, clock).handle_message(msg(text=text))
    assert outcome.answer_request is None and len(outcome.outbound_ids) == 1
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
        assert user is not None and user["proactive_opt_out"] is True
        assert user["proactive_opt_out_at"] == NOW and user["status"] == "active"
        tr = await trace_row(conn)
        assert tr["decision"] == "allowed" and tr["decision_reason"] == "proactive_opt_out"
        assert tr["body"] == text and tr["reply_kind"] == "opt_out_confirmation"
        out = await conn.fetchrow("SELECT * FROM chat.outbound_messages")
        assert out is not None and out["kind"] == "opt_out_confirmation"
        assert out["id"] == outcome.outbound_ids[0] and out["inbound_trace_id"] == tr["id"]
        assert out["body"] == make_settings().text_opt_out_confirmed
        assert await count(conn, "ops.alerts") == 0


# ---- midia ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [ContentKind.AUDIO, ContentKind.IMAGE])
async def test_midia_gera_ticket_e_ack_sem_alerta_nem_email(
    db_pool: asyncpg.Pool, clock: FakeClock, kind: ContentKind
) -> None:
    async with db_pool.acquire() as conn:
        await add_user(conn)
    channel = FakeChannel()
    outcome = await make_pipeline(db_pool, clock).handle_message(msg(kind=kind))
    assert channel.calls == []  # nada externo antes do despacho pos-commit
    assert outcome.answer_request is None
    assert len(outcome.outbound_ids) == 1 and len(outcome.delivery_ids) == 1
    async with db_pool.acquire() as conn:
        tr = await trace_row(conn)
        assert tr["decision"] == "escalated_media" and tr["body"] is None
        assert tr["reply_kind"] == "media_ack" and tr["counted_in_limit"]
        tickets = await conn.fetch("SELECT * FROM chat.tickets")
        assert len(tickets) == 1 and tickets[0]["ticket_number"] == tr["ticket_number"]
        assert tickets[0]["trace_id"] == tr["id"] and tickets[0]["kind"] == "media"
        assert await count(conn, "ops.alerts") == 0
        assert await count(conn, "ops.deliveries", "channel = 'email'") == 0
        delivery = await conn.fetchrow("SELECT * FROM ops.deliveries")
        assert delivery is not None and delivery["id"] == outcome.delivery_ids[0]
        assert delivery["source_type"] == "ticket" and delivery["status"] == "panel_only"
        out = await conn.fetchrow("SELECT * FROM chat.outbound_messages")
        assert out is not None and out["kind"] == "media_ack"
        assert out["body"] == make_settings().text_media_ack
    await outbox.dispatch(db_pool, outcome.outbound_ids[0], channel=channel, clock=clock)
    assert [c.body for c in channel.calls] == [make_settings().text_media_ack]


async def test_midia_com_ack_desligado_nao_responde(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_user(conn)
    outcome = await make_pipeline(db_pool, clock, media_ack_enabled=False).handle_message(
        msg(kind=ContentKind.AUDIO)
    )
    assert outcome.outbound_ids == () and len(outcome.delivery_ids) == 1
    async with db_pool.acquire() as conn:
        assert (await trace_row(conn))["reply_kind"] == "none"
        assert await count(conn, "chat.tickets") == 1
        assert await count(conn, "chat.outbound_messages") == 0


@pytest.mark.parametrize("kind", [ContentKind.REACTION, ContentKind.SYSTEM])
async def test_tipo_ignorado_so_deixa_rastro(
    db_pool: asyncpg.Pool, clock: FakeClock, kind: ContentKind
) -> None:
    async with db_pool.acquire() as conn:
        await add_user(conn)
    outcome = await make_pipeline(db_pool, clock).handle_message(msg(kind=kind))
    assert outcome == MessageOutcome()
    async with db_pool.acquire() as conn:
        row = await trace_row(conn)
        assert row["decision"] == "ignored_type"
        assert row["counted_in_limit"] is False  # fora do limite de 60/h (decisao 2026-10-26)
        for table in ("ops.alerts", "ops.deliveries", "chat.outbound_messages", "chat.tickets"):
            assert await count(conn, table) == 0, table


# ---- limite 60/h ---------------------------------------------------------------------


async def test_61a_bloqueada_com_um_aviso_e_um_alerta(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
        await seed_counted(conn, uid, 59, NOW - timedelta(minutes=10))
    pipe = make_pipeline(db_pool, clock)
    ok = await pipe.handle_message(msg("m60"))  # a 60a ainda passa
    assert ok.answer_request is not None
    blocked = await pipe.handle_message(msg("m61"))
    assert blocked.answer_request is None
    assert len(blocked.outbound_ids) == 1 and len(blocked.delivery_ids) == 2
    again = await pipe.handle_message(msg("m62"))  # mesma janela: sem novo aviso/alerta
    assert again == MessageOutcome()
    async with db_pool.acquire() as conn:
        for wamid in ("m61", "m62"):
            tr = await trace_row(conn, wamid)
            assert tr["decision"] == "rate_limited" and not tr["counted_in_limit"]
            assert tr["body"] is None
        assert (await trace_row(conn, "m61"))["reply_kind"] == "rate_limit_notice"
        assert (await trace_row(conn, "m62"))["reply_kind"] == "none"
        assert await count(conn, "chat.outbound_messages", "kind = 'rate_limit_notice'") == 1
        alert = await conn.fetchrow("SELECT * FROM ops.alerts")
        assert alert is not None and alert["kind"] == "rate_limit"
        assert alert["dedup_key"] == f"rate_limit:{uid}"
        channels = {
            r["channel"] for r in await conn.fetch("SELECT channel FROM ops.deliveries")
        }
        assert channels == {"email", "group"}
        notice_at = await conn.fetchval(
            "SELECT rate_limit_notice_at FROM access.users WHERE id = $1", uid
        )
        assert notice_at == NOW


async def test_limite_novo_aviso_e_alerta_depois_de_uma_hora(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=1))
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(msg("m1"))
    assert len(first.outbound_ids) == 1
    clock.advance(minutes=30)
    assert (await pipe.handle_message(msg("m2"))) == MessageOutcome()
    clock.advance(minutes=31)  # 61 min depois: janela do seed ja correu
    async with db_pool.acquire() as conn:
        await seed_counted(conn, uid, 60, clock.now() - timedelta(minutes=1), "seed2")
    third = await pipe.handle_message(msg("m3"))
    assert len(third.outbound_ids) == 1 and len(third.delivery_ids) == 2
    async with db_pool.acquire() as conn:
        assert await count(conn, "ops.alerts") == 2


async def test_bloqueadas_nao_prolongam_a_janela(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=59))
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(msg("blocked"))
    clock.advance(minutes=2)  # as 60 do seed saem da janela
    out = await pipe.handle_message(msg("after"))
    assert out.answer_request is not None


async def test_limite_vale_para_midia_e_nao_cria_ticket(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
        await seed_counted(conn, uid, 60, NOW - timedelta(minutes=5))
    await make_pipeline(db_pool, clock).handle_message(msg(kind=ContentKind.AUDIO))
    async with db_pool.acquire() as conn:
        assert (await trace_row(conn))["decision"] == "rate_limited"
        assert await count(conn, "chat.tickets") == 0


# ---- pending_optin ---------------------------------------------------------------------


async def test_aceito_ativa_e_confirma(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status="pending_optin")
        notice = await conn.fetchval("SELECT id FROM access.optin_requests")
    outcome = await make_pipeline(db_pool, clock).handle_message(
        msg(kind=ContentKind.BUTTON, payload=f"OPTIN_ACCEPT:{notice}", bsuid="BR.abc123")
    )
    assert len(outcome.outbound_ids) == 1 and outcome.answer_request is None
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
        assert user is not None and user["status"] == "active"
        assert user["accepted_at"] == NOW and user["bsuid"] == "BR.abc123"
        assert await conn.fetchval("SELECT status FROM access.optin_requests") == "accepted"
        tr = await trace_row(conn)
        assert tr["decision"] == "optin_accepted" and tr["reply_kind"] == "optin_confirmation"
        assert tr["body"] is None and tr["user_id"] == uid
        out = await conn.fetchrow("SELECT * FROM chat.outbound_messages")
        assert out is not None and out["kind"] == "optin_confirmation"
        assert out["id"] == outcome.outbound_ids[0]


async def test_nao_aceito_recusa_e_envia_texto(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status="pending_optin")
        notice = await conn.fetchval("SELECT id FROM access.optin_requests")
    outcome = await make_pipeline(db_pool, clock).handle_message(
        msg(kind=ContentKind.BUTTON, payload=f"OPTIN_DECLINE:{notice}")
    )
    assert len(outcome.outbound_ids) == 1
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
        assert user is not None and user["status"] == "declined" and user["declined_at"] == NOW
        assert await conn.fetchval("SELECT status FROM access.optin_requests") == "declined"
        tr = await trace_row(conn)
        assert tr["decision"] == "optin_declined" and tr["reply_kind"] == "optin_declined"
        out = await conn.fetchrow("SELECT * FROM chat.outbound_messages")
        assert out is not None and out["kind"] == "optin_declined"


async def test_botao_de_aviso_antigo_nao_aceita(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status="pending_optin")
        notice = int(await conn.fetchval("SELECT id FROM access.optin_requests"))
    outcome = await make_pipeline(db_pool, clock).handle_message(
        msg(kind=ContentKind.BUTTON, payload=f"OPTIN_ACCEPT:{notice + 999}")
    )
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM access.users WHERE id = $1", uid) == (
            "pending_optin"
        )
        assert await conn.fetchval("SELECT status FROM access.optin_requests") == "open"
        assert (await trace_row(conn))["decision"] == "pending_optin"
    assert len(outcome.outbound_ids) == 1  # vira lembrete (1/24h)


async def test_texto_no_lugar_do_botao_nao_aceita_e_lembra_uma_vez_por_dia(
    db_pool: asyncpg.Pool, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status="pending_optin")
    pipe = make_pipeline(db_pool, clock)
    channel = FakeChannel()
    with caplog.at_level(logging.DEBUG):
        first = await pipe.handle_message(msg("t1", text="aceito"))
    assert len(first.outbound_ids) == 1 and first.answer_request is None
    clock.advance(hours=1)
    assert await pipe.handle_message(msg("t2")) == MessageOutcome()
    assert await pipe.handle_message(msg("t3", kind=ContentKind.AUDIO)) == MessageOutcome()
    clock.advance(hours=24)
    third = await pipe.handle_message(msg("t4", kind=ContentKind.IMAGE))
    assert len(third.outbound_ids) == 1
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM access.users WHERE id = $1", uid) == (
            "pending_optin"
        )
        assert await count(conn, "chat.outbound_messages", "kind = 'optin_reminder'") == 2
        assert await count(conn, "chat.message_trace", "body IS NOT NULL") == 0
        for table in ("chat.tickets", "ops.alerts", "ops.deliveries"):
            assert await count(conn, table) == 0, table
        reply_kinds = [
            r["reply_kind"]
            for r in await conn.fetch("SELECT reply_kind FROM chat.message_trace ORDER BY id")
        ]
        assert reply_kinds == ["optin_reminder", "none", "none", "optin_reminder"]
    assert channel.calls == []
    assert "aceito" not in caplog.text and PHONE not in caplog.text


async def test_reacao_de_pendente_tambem_pode_disparar_lembrete(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    """Decisao do dono (por enquanto): qualquer conteudo, inclusive reacao, pode lembrar."""
    async with db_pool.acquire() as conn:
        await add_user(conn, status="pending_optin")
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(msg("r1", kind=ContentKind.REACTION))
    second = await pipe.handle_message(msg("r2", kind=ContentKind.REACTION))
    assert len(first.outbound_ids) == 1 and second.outbound_ids == ()
