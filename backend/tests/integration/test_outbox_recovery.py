"""outbox.recover_stuck e check_send_failures: travados, abandono e alerta de falha (AC-17/33)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import OutboundKind
from app.services import outbox
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PHONE = "+5511900000001"


@dataclass(frozen=True)
class Target:
    id: int
    phone_e164: str


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr("postgresql://x"),
        whatsapp_app_secret=SecretStr("s"),
        whatsapp_verify_token=SecretStr("v"),
        whatsapp_access_token=SecretStr("t"),
        whatsapp_phone_number_id="1",
        resend_api_key=SecretStr("re_key"),
        resend_from_email="jarvis@example.com",
        alert_recipient_email=SecretStr("destino@example.com"),
    )


async def add_user(conn: asyncpg.Connection, status: str = "active") -> Target:
    user_id: int = await conn.fetchval(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
        "brands, manager_name, status, status_changed_at, created_at, created_by, updated_at, "
        "accepted_at, accepted_notice_version) "
        "VALUES ('Fulana', $1, '551100000001', 'vendedor', 'Acme', '{SP}', '{farma}', "
        "'{jungle}', 'Gestora', $3::text, $2::timestamptz, $2::timestamptz, 'teste', "
        "$2::timestamptz, "
        "CASE WHEN $3::text = 'active' THEN $2::timestamptz END, "
        "CASE WHEN $3::text = 'active' THEN 'v1' END) RETURNING id",
        PHONE,
        NOW,
        status,
    )
    return Target(id=user_id, phone_e164=PHONE)


async def add_trace(
    conn: asyncpg.Connection, user: Target, received_at: datetime, reply_kind: str = "pending"
) -> int:
    trace_id: int = await conn.fetchval(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, reply_kind) "
        "VALUES ('wamid.' || nextval('chat.message_trace_id_seq')::text, $1, 'text', $2, "
        "'allowed', 'allowed', $3) RETURNING id",
        received_at,
        user.id,
        reply_kind,
    )
    return trace_id


async def fetch_one(conn: asyncpg.Connection, sql: str, *args: Any) -> asyncpg.Record:
    row = await conn.fetchrow(sql, *args)
    assert row is not None
    return row


async def failed_text(
    pool: asyncpg.Pool, conn: asyncpg.Connection, user: Target, at: datetime
) -> int:
    """Enfileira um texto e o despacha com falha definitiva (4xx) no instante `at`."""
    outbound_id = await outbox.enqueue(
        conn, user, OutboundKind.UNDER_CONSTRUCTION, body="Em construcao", clock=FakeClock(at)
    )
    channel = FakeChannel()
    channel.fail_always("definitive", code="131000")
    status = await outbox.dispatch(pool, outbound_id, channel=channel, clock=FakeClock(at))
    assert status is not None and status.value == "failed"
    return outbound_id


async def alerts_of(conn: asyncpg.Connection, kind: str) -> list[asyncpg.Record]:
    return list(await conn.fetch("SELECT * FROM ops.alerts WHERE kind = $1", kind))


async def check(conn: asyncpg.Connection, outbound_id: int, at: datetime) -> int | None:
    return await outbox.check_send_failures(
        conn, outbound_id, settings=make_settings(), clock=FakeClock(at)
    )


async def test_sending_antigo_vira_unknown_sem_reenviar(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user, NOW - timedelta(minutes=1), "under_construction")
        old = await outbox.enqueue(
            conn, user, OutboundKind.UNDER_CONSTRUCTION, body="x", inbound_trace_id=trace_id
        )
        recent = await outbox.enqueue(conn, user, OutboundKind.UNDER_CONSTRUCTION, body="y")
        await conn.execute(
            "UPDATE chat.outbound_messages SET status = 'sending', claimed_at = $2 WHERE id = $1",
            old,
            NOW - timedelta(minutes=3),
        )
        await conn.execute(
            "UPDATE chat.outbound_messages SET status = 'sending', claimed_at = $2 WHERE id = $1",
            recent,
            NOW - timedelta(seconds=30),
        )
        channel = FakeChannel()
        result = await outbox.recover_stuck(conn, NOW)
        again = await outbox.recover_stuck(conn, NOW)
        assert (result.unknown, again.unknown) == (1, 0)
        assert (
            await fetch_one(conn, "SELECT status FROM chat.outbound_messages WHERE id=$1", old)
        )["status"] == "unknown"
        assert (
            await fetch_one(conn, "SELECT status FROM chat.outbound_messages WHERE id=$1", recent)
        )["status"] == "sending"
        trace_row = await fetch_one(
            conn, "SELECT send_result FROM chat.message_trace WHERE id=$1", trace_id
        )
        assert trace_row["send_result"] == "unknown"
        # recuperar nunca envia: o canal nao foi tocado
        assert channel.calls == []
        assert await outbox.dispatch(db_pool, old, channel=channel) is None
        assert channel.calls == []


async def test_rastro_pending_antigo_vira_abandoned(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        old = await add_trace(conn, user, NOW - timedelta(minutes=6))
        fresh = await add_trace(conn, user, NOW - timedelta(minutes=4))
        done = await add_trace(conn, user, NOW - timedelta(minutes=10), "under_construction")
        result = await outbox.recover_stuck(conn, NOW)
        assert result.abandoned == 1
        sql = "SELECT send_result FROM chat.message_trace WHERE id = $1"
        assert (await fetch_one(conn, sql, old))["send_result"] == "abandoned"
        assert (await fetch_one(conn, sql, fresh))["send_result"] is None
        assert (await fetch_one(conn, sql, done))["send_result"] is None
        assert (await outbox.recover_stuck(conn, NOW)).abandoned == 0


async def test_duas_falhas_nao_alertam_a_terceira_sim_uma_vez_por_hora(
    db_pool: asyncpg.Pool,
) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        results: list[int | None] = []
        for i in range(4):
            at = NOW + timedelta(minutes=i)
            outbound_id = await failed_text(db_pool, conn, user, at)
            results.append(await check(conn, outbound_id, at))
            if i == 1:
                assert await alerts_of(conn, "send_failure") == []
        assert results[0] is None and results[1] is None
        assert results[2] is not None
        assert results[3] is None  # dedup global de 1 h
        rows = await alerts_of(conn, "send_failure")
        assert len(rows) == 1 and rows[0]["severity"] == "high"
        channels = await conn.fetch(
            "SELECT channel FROM ops.deliveries WHERE source_id = $1", rows[0]["id"]
        )
        assert {r["channel"] for r in channels} == {"group", "email"}


async def test_falhas_fora_da_janela_nao_contam(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        old = [await failed_text(db_pool, conn, user, NOW - timedelta(hours=2)) for _ in range(2)]
        last = await failed_text(db_pool, conn, user, NOW)
        assert old
        assert await check(conn, last, NOW) is None


async def test_boas_vindas_falha_gera_welcome_failed_e_nao_conta_no_send_failure(
    db_pool: asyncpg.Pool,
) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, status="pending_optin")
        template = {"name": "boas_vindas", "language": "pt_BR", "body_params": ["Fulana"]}
        ids = [
            await outbox.enqueue(
                conn, user, OutboundKind.WELCOME_TEMPLATE, template=template, clock=FakeClock(NOW)
            )
            for _ in range(2)
        ]
        channel = FakeChannel()
        channel.fail_always("definitive", code="132001")
        for outbound_id in ids:
            await outbox.dispatch(db_pool, outbound_id, channel=channel, clock=FakeClock(NOW))
        first = await check(conn, ids[0], NOW)
        second = await check(conn, ids[1], NOW)
        assert first is not None and second is None  # dedup por usuario
        alert = (await alerts_of(conn, "welcome_failed"))[0]
        assert alert["severity"] == "high" and alert["user_id"] == user.id
        deliveries = await conn.fetch(
            "SELECT channel FROM ops.deliveries WHERE source_id=$1", alert["id"]
        )
        assert {r["channel"] for r in deliveries} == {"group", "email"}
        status = await conn.fetchval("SELECT status FROM access.users WHERE id = $1", user.id)
        assert status == "pending_optin"
        assert await alerts_of(conn, "send_failure") == []
        assert [c.method for c in channel.calls] == ["send_template", "send_template"]


async def test_boas_vindas_com_conteudo_invalido_tambem_alerta(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, status="pending_optin")
        outbound_id = await outbox.enqueue(
            conn,
            user,
            OutboundKind.WELCOME_TEMPLATE,
            template={"name": "", "language": "pt_BR"},
            clock=FakeClock(NOW),
        )
        with pytest.raises(InvalidValue):
            await outbox.dispatch(db_pool, outbound_id, channel=FakeChannel(), clock=FakeClock(NOW))
        assert await check(conn, outbound_id, NOW) is not None
        assert len(await alerts_of(conn, "welcome_failed")) == 1


async def test_saida_nao_falha_nao_alerta(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        pending = await outbox.enqueue(conn, user, OutboundKind.UNDER_CONSTRUCTION, body="x")
        assert await check(conn, pending, NOW) is None
        assert await check(conn, 999999, NOW) is None
