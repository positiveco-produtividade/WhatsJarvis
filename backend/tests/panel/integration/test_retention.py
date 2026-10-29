"""Rotina de retencao (PN-58): 12 meses e 1 dia some, dry-run so conta, auditoria sem PII."""

from __future__ import annotations

import itertools
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.panel.services import retention
from app.panel.services.workers import discover_loops
from tests.panel import factories

pytestmark = pytest.mark.db

_N = itertools.count(1)
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
OLD = datetime(2025, 10, 25, 12, 0, tzinfo=UTC)  # 12 meses e 1 dia
EDGE = datetime(2025, 10, 26, 13, 0, tzinfo=UTC)  # dentro dos 12 meses
RECENT = NOW - timedelta(days=3)


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "resend_api_key": SecretStr("re_key"),
        "resend_from_email": "jarvis@example.com",
        "alert_recipient_email": SecretStr("destino@example.com"),
        "retention_dry_run": False,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(NOW)


@pytest.fixture
async def uid(db_conn: asyncpg.Connection) -> int:
    return await factories.insert_user(
        db_conn, name="Vendedor R", seq=61, brands=["jungle"], now=NOW
    )


async def _trace(conn: asyncpg.Connection, uid: int, at: datetime) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, body) VALUES ($1, $2, 'text', $3, 'allowed', 'answered', "
            "'corpo secreto') RETURNING id",
            f"wamid.ret.{next(_N)}",
            at,
            uid,
        )
    )


async def _ticket(
    conn: asyncpg.Connection, uid: int, at: datetime, trace_id: int | None = None
) -> int:
    t = await factories.insert_ticket(
        conn, number=next(_N), user_id=uid, status="awaiting", created_at=at
    )
    if trace_id is not None:
        await conn.execute("UPDATE chat.tickets SET trace_id = $1 WHERE id = $2", trace_id, t.id)
    return t.id


async def _outbound(
    conn: asyncpg.Connection, uid: int, at: datetime, trace_id: int | None = None
) -> None:
    await conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, status, "
        "next_attempt_at, created_at, inbound_trace_id) "
        "VALUES ($1, '+5511900000061', 'media_ack', 'active', 'sent', $2, $2, $3)",
        uid,
        at,
        trace_id,
    )


async def _count(conn: asyncpg.Connection, table: str) -> int:
    queries = {
        "tickets": "SELECT count(*) FROM chat.tickets",
        "trace": "SELECT count(*) FROM chat.message_trace",
        "outbound": "SELECT count(*) FROM chat.outbound_messages",
        "audit": "SELECT count(*) FROM ops.audit_log WHERE action = 'retention.purge'",
    }
    return int(await conn.fetchval(queries[table]))


async def _seed_chain(conn: asyncpg.Connection, uid: int, at: datetime) -> None:
    trace = await _trace(conn, uid, at)
    await _ticket(conn, uid, at, trace)
    await _outbound(conn, uid, at, trace)


async def test_old_rows_gone_recent_kept(
    db_conn: asyncpg.Connection, uid: int, clock: FakeClock
) -> None:
    await _seed_chain(db_conn, uid, OLD)
    await _seed_chain(db_conn, uid, EDGE)
    await _seed_chain(db_conn, uid, RECENT)
    result = await retention.purge(db_conn, make_settings(), clock)
    assert result.counts["tickets"] == 1
    assert result.counts["outbound_messages"] == 1
    assert result.counts["message_trace"] == 1
    assert await _count(db_conn, "tickets") == 2
    assert await _count(db_conn, "trace") == 2
    assert await _count(db_conn, "outbound") == 2


async def test_dry_run_only_counts(db_conn: asyncpg.Connection, uid: int, clock: FakeClock) -> None:
    await _seed_chain(db_conn, uid, OLD)
    result = await retention.purge(db_conn, make_settings(retention_dry_run=True), clock)
    assert result.dry_run is True
    assert result.counts["tickets"] == 1
    assert result.counts["message_trace"] == 1  # dry-run conta o mesmo que a purga real
    assert await _count(db_conn, "tickets") == 1
    assert await _count(db_conn, "trace") == 1
    assert await _count(db_conn, "outbound") == 1


async def test_recent_ticket_keeps_its_old_trace(
    db_conn: asyncpg.Connection, uid: int, clock: FakeClock
) -> None:
    trace = await _trace(db_conn, uid, OLD)
    await _ticket(db_conn, uid, RECENT, trace)
    result = await retention.purge(db_conn, make_settings(), clock)
    assert result.counts["message_trace"] == 0
    assert await _count(db_conn, "trace") == 1


async def test_batches(
    db_conn: asyncpg.Connection, uid: int, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(retention, "BATCH_SIZE", 2)
    for _ in range(5):
        await _trace(db_conn, uid, OLD)
    result = await retention.purge(db_conn, make_settings(), clock)
    assert result.counts["message_trace"] == 5
    assert await _count(db_conn, "trace") == 0


async def test_other_targets(db_conn: asyncpg.Connection, uid: int, clock: FakeClock) -> None:
    attempt = int(
        await db_conn.fetchval(
            "INSERT INTO access.access_attempts (phone_key, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type) "
            "VALUES ('5511900099999', 'unknown', 1, $1, $1, 'text') RETURNING id",
            OLD,
        )
    )
    await db_conn.execute(
        "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at) "
        "VALUES ($1, 'oi', $2)",
        attempt,
        OLD,
    )
    for at in (OLD, RECENT):
        await db_conn.execute(
            "INSERT INTO ops.assistant_questions (asked_at, asked_by, agent, question, status) "
            "VALUES ($1, 'a@x.com', 'geral', 'q', 'ok')",
            at,
        )
    for exp in (NOW - timedelta(hours=1), NOW + timedelta(hours=1)):
        await db_conn.execute(
            "INSERT INTO ops.import_batches (id, kind, actor, filename, sha256, size_bytes, rows, "
            "summary, status, created_at, expires_at) VALUES ($1, 'faq', 'a', 'f.xlsx', 'h', 1, "
            "'[]', '{}', 'preview', $2, $3)",
            uuid.uuid4(),
            NOW - timedelta(hours=2),
            exp,
        )
        await db_conn.execute(
            "INSERT INTO ops.panel_sessions (id_hash, email, name, csrf_secret, created_at, "
            "last_seen_at, last_iam_check_at, expires_at) "
            "VALUES ($1, 'a@x.com', 'A', 's', $2, $2, $2, $3)",
            f"h{next(_N)}",
            NOW - timedelta(hours=2),
            exp,
        )
    result = await retention.purge(db_conn, make_settings(), clock)
    assert result.counts["unknown_first_messages"] == 1
    assert result.counts["assistant_questions"] == 1
    assert result.counts["import_batches"] == 1
    assert result.counts["panel_sessions"] == 1
    assert await db_conn.fetchval("SELECT count(*) FROM ops.panel_sessions") == 1


async def test_audit_has_counts_only_and_audit_log_untouched(
    db_conn: asyncpg.Connection, uid: int, clock: FakeClock
) -> None:
    await db_conn.execute(
        "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id) "
        "VALUES ($1, 'cli', 'x', 'old.thing', 't', '1')",
        OLD - timedelta(days=400),
    )
    await _seed_chain(db_conn, uid, OLD)
    await retention.purge(db_conn, make_settings(), clock)
    row = await db_conn.fetchrow(
        "SELECT actor_type, new_value FROM ops.audit_log WHERE action = 'retention.purge'"
    )
    assert row is not None and row["actor_type"] == "system"
    payload = json.loads(row["new_value"])
    assert payload["counts"]["message_trace"] == 1
    assert payload["dry_run"] is False
    assert "corpo secreto" not in row["new_value"]
    assert (
        await db_conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE action = 'old.thing'") == 1
    )


def test_month_arithmetic_and_floor() -> None:
    assert retention.floor_for(datetime(2026, 10, 26, 12, tzinfo=UTC)) == datetime(
        2025, 10, 26, 12, tzinfo=UTC
    )
    assert retention.months_back(datetime(2024, 3, 31, tzinfo=UTC), 1) == datetime(
        2024, 2, 29, tzinfo=UTC
    )
    assert retention.months_back(datetime(2026, 1, 15, tzinfo=UTC), 2) == datetime(
        2025, 11, 15, tzinfo=UTC
    )


def test_schedule_is_due_after_0230_sao_paulo_once_a_day() -> None:
    # 02:30 SP = 05:30 UTC (UTC-3)
    before = datetime(2026, 10, 26, 5, 29, tzinfo=UTC)
    after = datetime(2026, 10, 26, 5, 31, tzinfo=UTC)
    assert retention.is_due(before, None) is False
    assert retention.is_due(after, None) is True
    assert retention.is_due(after, datetime(2026, 10, 26, 5, 31, tzinfo=UTC)) is False
    assert retention.is_due(after + timedelta(hours=10), after) is False
    assert retention.is_due(after + timedelta(days=1), after) is True


async def test_run_if_due_is_idempotent(db_conn: asyncpg.Connection, uid: int) -> None:
    clock = FakeClock(datetime(2026, 10, 26, 6, 0, tzinfo=UTC))
    await _seed_chain(db_conn, uid, OLD)
    first = await retention.run_if_due(db_conn, make_settings(), clock)
    assert first is not None and first.counts["tickets"] == 1
    clock.advance(hours=3)
    assert await retention.run_if_due(db_conn, make_settings(), clock) is None
    assert await _count(db_conn, "audit") == 1


async def test_read_volumes(db_conn: asyncpg.Connection, uid: int, clock: FakeClock) -> None:
    await _trace(db_conn, uid, OLD)  # fora do piso
    await _trace(db_conn, uid, EDGE + timedelta(days=10))  # expira em < 30 dias
    await _trace(db_conn, uid, RECENT)
    await _ticket(db_conn, uid, RECENT)
    vols = await retention.read_volumes(db_conn, make_settings(), clock)
    assert sum(m.messages for m in vols.months) == 2
    assert vols.months[0].month.isoformat() == "2026-10-01"
    assert vols.months[0].messages == 1 and vols.months[0].tickets == 1
    assert vols.expiring_messages == 1
    assert vols.expiring_tickets == 0
    assert vols.expiring_days == 30


def test_loop_is_discovered() -> None:
    assert "retention" in {loop.name for loop in discover_loops()}
