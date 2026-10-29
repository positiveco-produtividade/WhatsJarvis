"""Pipeline parte 1: idempotencia, identidade, acesso e silencio (AC-5/7/8/10-12/14/22/27)."""
import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import TransientDbError
from app.core.values import AccessDecision, Decision, MessageOutcome, ReplyKind
from app.services import trace
from app.services.flags import Flags
from app.services.inbound import ButtonReply, ContentKind, InboundMessage, InboundStatus, Sender
from app.services.phone import normalize_phone
from app.services.pipeline import Pipeline
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE_A = "5511987654321"
PHONE_B = "5521987654321"
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


def make_pipeline(pool: asyncpg.Pool, clock: FakeClock, **over: Any) -> Pipeline:
    return Pipeline(pool, make_settings(**over), Flags(pool, clock), clock)


def text_msg(
    wamid: str = "wamid.1",
    wa_id: str | None = PHONE_A,
    bsuid: str | None = None,
    text: str = SECRET_TEXT,
    kind: ContentKind = ContentKind.TEXT,
) -> InboundMessage:
    return InboundMessage(
        wa_message_id=wamid,
        sender=Sender(wa_id=wa_id, bsuid=bsuid),
        kind=kind,
        timestamp=int(NOW.timestamp()),
        text=text if kind is ContentKind.TEXT else None,
        text_length=len(text) if kind is ContentKind.TEXT else None,
    )


async def add_user(
    conn: asyncpg.Connection, phone: str = "+5511987654321", status: str = "active",
    bsuid: str | None = None,
) -> int:
    num = normalize_phone(phone)
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, bsuid, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version, revoked_at, "
            "revoked_by) VALUES ('Ana', $1, $2, $3, 'vendedor', 'Acme', "
            "ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], 'Gestora', $4, $5, $5, 'teste', $5, "
            "CASE WHEN $4 = 'active' THEN $5::timestamptz END, "
            "CASE WHEN $4 = 'active' THEN 'v1' END, "
            "CASE WHEN $4 = 'revoked' THEN $5::timestamptz END, "
            "CASE WHEN $4 = 'revoked' THEN 'teste' END) "
            "RETURNING id",
            num.phone_e164, num.match_key, bsuid, status, NOW,
        )
    )


async def count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))


async def no_side_effects(conn: asyncpg.Connection) -> None:
    for table in ("ops.alerts", "ops.deliveries", "chat.outbound_messages", "chat.tickets"):
        assert await count(conn, table) == 0, table


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


async def test_desconhecido_silencio_e_tentativa(
    db_pool: asyncpg.Pool, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    channel = FakeChannel()
    pipe = make_pipeline(db_pool, clock)
    with caplog.at_level(logging.DEBUG):
        outcome = await pipe.handle_message(text_msg())
    assert outcome == MessageOutcome()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM chat.message_trace")
        assert len(rows) == 1
        tr = rows[0]
        assert tr["access_decision"] == "unknown" and tr["decision"] == "unknown"
        assert tr["body"] is None and tr["reply_kind"] == ReplyKind.NONE.value
        assert not tr["counted_in_limit"] and tr["attempt_id"] is not None
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["reason"] == "unknown" and att["attempt_count"] == 1
        assert await count(conn, "access.unknown_first_messages") == 0
        await no_side_effects(conn)
    assert channel.calls == []
    assert SECRET_TEXT not in caplog.text and PHONE_A not in caplog.text


async def test_desconhecido_guarda_primeiro_texto_so_com_flag(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock, unknown_first_text_store=True)
    await pipe.handle_message(text_msg("wamid.1"))
    await pipe.handle_message(text_msg("wamid.2", text="outro texto"))
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT body FROM access.unknown_first_messages") == SECRET_TEXT
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 2


@pytest.mark.parametrize("status", ["revoked", "declined"])
async def test_revogado_ou_recusado_silencio(
    db_pool: asyncpg.Pool, clock: FakeClock, status: str
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, status=status)
    pipe = make_pipeline(db_pool, clock)
    button = InboundMessage(
        "wamid.B", Sender(wa_id=PHONE_A), ContentKind.BUTTON, button=ButtonReply("OPTIN_ACCEPT:1")
    )
    for msg in (text_msg("wamid.T"), button):
        assert await pipe.handle_message(msg) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 2
        assert await conn.fetchval(
            "SELECT count(*) FROM chat.message_trace "
            "WHERE decision = $1 AND user_id = $2 AND body IS NULL",
            status, uid,
        ) == 2
        att = await conn.fetchrow("SELECT * FROM access.access_attempts")
        assert att is not None and att["reason"] == status and att["attempt_count"] == 2
        assert att["user_id"] == uid
        await no_side_effects(conn)
        assert await conn.fetchval("SELECT status FROM access.users WHERE id = $1", uid) == status


async def test_mesmo_message_id_sem_efeito(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(text_msg())
    assert await pipe.handle_message(text_msg()) == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 1


async def test_duplicata_concorrente_um_rastro(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    pipe = make_pipeline(db_pool, clock)
    await asyncio.gather(*(pipe.handle_message(text_msg()) for _ in range(3)))
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 1
        assert await conn.fetchval("SELECT attempt_count FROM access.access_attempts") == 1


async def test_conflito_bsuid_de_outro_usuario_alerta_com_dedup(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        await add_user(conn, bsuid="bsuid-A")
        await add_user(conn, phone="+5521987654321")
    pipe = make_pipeline(db_pool, clock)
    first = await pipe.handle_message(text_msg("wamid.1", wa_id=PHONE_B, bsuid="bsuid-A"))
    async with db_pool.acquire() as conn:
        tr = await conn.fetchrow("SELECT * FROM chat.message_trace")
        assert tr is not None and tr["decision"] == "identity_conflict", tr["decision_reason"]
        assert tr["attempt_id"] is not None and tr["body"] is None
        assert await count(conn, "ops.alerts") == 1
        alert = await conn.fetchrow("SELECT kind, payload::text AS p FROM ops.alerts")
        assert alert is not None and alert["kind"] == "identity_conflict"
        assert "bsuid-A" not in alert["p"] and PHONE_B not in alert["p"]
        assert await count(conn, "ops.deliveries") >= 1
        assert await count(conn, "chat.outbound_messages") == 0
    assert len(first.delivery_ids) >= 1 and first.outbound_ids == ()
    clock.advance(hours=1)
    second = await pipe.handle_message(text_msg("wamid.2", wa_id=PHONE_B, bsuid="bsuid-A"))
    assert second == MessageOutcome()
    async with db_pool.acquire() as conn:
        assert await count(conn, "ops.alerts") == 1
        assert await count(conn, "chat.message_trace") == 2
        assert await conn.fetchval("SELECT sum(attempt_count) FROM access.access_attempts") == 2
    clock.advance(hours=24)
    await pipe.handle_message(text_msg("wamid.3", wa_id=PHONE_B, bsuid="bsuid-A"))
    async with db_pool.acquire() as conn:
        assert await count(conn, "ops.alerts") == 2


async def test_bsuid_sob_outro_numero_e_conflito(db_pool: asyncpg.Pool, clock: FakeClock) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn, bsuid="bsuid-A")
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(text_msg(wa_id=PHONE_B, bsuid="bsuid-A"))
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT decision FROM chat.message_trace") == "identity_conflict"
        assert await count(conn, "ops.alerts") == 1
        assert await conn.fetchval("SELECT status FROM access.users WHERE id = $1", uid) == "active"


async def test_remetente_sem_identificador_vira_erro_sem_tentativa(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    await pipe.handle_message(text_msg(wa_id=None, bsuid=None))
    async with db_pool.acquire() as conn:
        tr = await conn.fetchrow("SELECT * FROM chat.message_trace")
        assert tr is not None and tr["decision"] == "error"
        assert tr["decision_reason"] == "sender_missing"
        assert await count(conn, "access.access_attempts") == 0
        await no_side_effects(conn)


@pytest.mark.parametrize(
    ("status", "decision"),
    [("active", AccessDecision.ALLOWED), ("pending_optin", AccessDecision.PENDING_OPTIN)],
)
async def test_decisao_sem_tratador_grava_rastro_error_sem_vazar_texto(
    db_pool: asyncpg.Pool,
    clock: FakeClock,
    caplog: pytest.LogCaptureFixture,
    status: str,
    decision: AccessDecision,
) -> None:
    # PF-32 registrou os tratadores; aqui um deles é removido para exercitar o gancho.
    async with db_pool.acquire() as conn:
        await add_user(conn, status=status)
    pipe = make_pipeline(db_pool, clock)
    del pipe._handlers[decision]
    with caplog.at_level(logging.DEBUG):
        outcome = await pipe.handle_message(text_msg())
    assert outcome == MessageOutcome()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM chat.message_trace")
        assert len(rows) == 1
        assert rows[0]["decision"] == "error"
        assert rows[0]["decision_reason"] == "NotImplementedError"
        assert rows[0]["body"] is None
        await no_side_effects(conn)
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [r.getMessage() for r in errors] == ["message_processing_error"]
    assert SECRET_TEXT not in caplog.text and PHONE_A not in caplog.text


async def test_erro_transitorio_sobe_como_transient(clock: FakeClock) -> None:
    class BrokenPool:
        def acquire(self) -> Any:
            raise asyncpg.InterfaceError("pool fechado")

    pipe = Pipeline(BrokenPool(), make_settings(), Flags(BrokenPool(), clock), clock)
    with pytest.raises(TransientDbError):
        await pipe.handle_message(text_msg())
    with pytest.raises(TransientDbError):
        await pipe.handle_status(InboundStatus("wamid.S", "failed", error_code=131000))


async def _seed_outbound(conn: asyncpg.Connection, uid: int) -> tuple[int, int]:
    trace_id = await trace.begin(conn, "wamid.IN", msg_type="text", clock=FakeClock(NOW))
    assert trace_id is not None
    await trace.finalize(
        conn, trace_id, access_decision=AccessDecision.ALLOWED, decision=Decision.ALLOWED,
        user_id=uid, body="oi", reply_kind=ReplyKind.UNDER_CONSTRUCTION,
    )
    out_id = int(
        await conn.fetchval(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
            "inbound_trace_id, status, attempts, next_attempt_at, wa_message_id, created_at) "
            "VALUES ($1, '+5511987654321', 'under_construction', 'active', 'x', $2, 'sent', 1, "
            "$3, 'wamid.OUT', $3) RETURNING id",
            uid, trace_id, NOW,
        )
    )
    return trace_id, out_id


async def test_status_failed_atualiza_saida_e_rastro_sem_resposta(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as conn:
        uid = await add_user(conn)
        trace_id, out_id = await _seed_outbound(conn, uid)
    pipe = make_pipeline(db_pool, clock)
    for status in ("sent", "delivered", "read"):
        assert await pipe.handle_status(InboundStatus("wamid.OUT", status)) is None
    async with db_pool.acquire() as conn:
        assert await conn.fetchval("SELECT status FROM chat.outbound_messages") == "sent"
    assert await pipe.handle_status(InboundStatus("wamid.OUT", "failed", error_code=131047)) is None
    assert await pipe.handle_status(InboundStatus("wamid.OUT", "failed", error_code=131047)) is None
    async with db_pool.acquire() as conn:
        out = await conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", out_id)
        assert out is not None and out["status"] == "failed" and out["error_code"] == "131047"
        tr = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE id = $1", trace_id)
        assert tr is not None and tr["send_result"] == "failed" and tr["decision"] == "send_failed"
        assert tr["send_error"] == "131047"
        assert await count(conn, "chat.message_trace") == 1
        assert await count(conn, "access.access_attempts") == 0
        assert await count(conn, "ops.alerts") == 0


async def test_status_failed_de_desconhecido_nao_faz_nada(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    pipe = make_pipeline(db_pool, clock)
    assert await pipe.handle_status(InboundStatus("wamid.NADA", "failed")) is None
    async with db_pool.acquire() as conn:
        assert await count(conn, "chat.message_trace") == 0
        await no_side_effects(conn)


async def test_message_decision_nao_loga_pii(
    db_pool: asyncpg.Pool, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    pipe = make_pipeline(db_pool, clock)
    with caplog.at_level(logging.INFO):
        await pipe.handle_message(text_msg(bsuid="bsuid-ZZ"))
    events = [r.getMessage() for r in caplog.records]
    assert "message_decision" in events
    assert "bsuid-ZZ" not in caplog.text and SECRET_TEXT not in caplog.text
