"""tickets.create_media_ticket (AC-36, R1, AC-13, decisao 9) contra Postgres real."""

from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services.flags import Flags
from app.services.tickets import create_media_ticket, format_ticket_number, media_label
from tests.integration.test_schema_access_chat import add_trace, add_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


async def _setup(conn: asyncpg.Connection) -> tuple[int, int]:
    await conn.execute("SELECT setval('ops.ticket_seq', 1, false)")
    user_id = await add_user(
        conn, status="active", accepted_at=NOW, accepted_notice_version="v1"
    )
    trace_id = await add_trace(conn, user_id=user_id, msg_type="audio")
    return user_id, trace_id


async def _enable_group(pool: asyncpg.Pool, flags: Flags, value: bool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, "team_group_enabled", value, "caio")


async def test_ticket_com_campos_exatos(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        flags = Flags(db_pool, FakeClock(NOW))
        result = await create_media_ticket(
            conn, user_id, trace_id, "audio", flags=flags, clock=FakeClock(NOW)
        )
        row = await conn.fetchrow("SELECT * FROM chat.tickets WHERE id = $1", result.id)
    assert row is not None
    assert result.ticket_number == "J-0001"
    expected = {
        "ticket_number": "J-0001", "user_id": user_id, "trace_id": trace_id, "kind": "media",
        "brand": None, "classification": "MIDIA", "message": "[áudio]", "escalated": True,
        "status": "awaiting", "urgency": "urgente", "created_at": NOW, "updated_at": NOW,
        "confidence": None, "answered_by": None, "answered_at": None,
    }
    for key, value in expected.items():
        assert row[key] == value, key


async def test_uma_linha_de_grupo_panel_only_com_chave_desligada(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        result = await create_media_ticket(
            conn, user_id, trace_id, "image", flags=Flags(db_pool, FakeClock(NOW))
        )
        rows = await conn.fetch("SELECT * FROM ops.deliveries")
    assert len(rows) == 1
    row = rows[0]
    assert (row["source_type"], row["source_id"], row["channel"]) == ("ticket", result.id, "group")
    assert row["status"] == "panel_only"


async def test_grupo_pending_com_chave_ligada(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _enable_group(db_pool, flags, True)
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        await create_media_ticket(conn, user_id, trace_id, "audio", flags=flags)
        status = await conn.fetchval("SELECT status FROM ops.deliveries")
    assert status == "pending"


async def test_sem_alerta_e_sem_email(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _enable_group(db_pool, flags, True)
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        await create_media_ticket(conn, user_id, trace_id, "audio", flags=flags)
        assert await conn.fetchval("SELECT count(*) FROM ops.alerts") == 0
        assert await conn.fetchval("SELECT count(*) FROM ops.deliveries WHERE channel='email'") == 0


async def test_numero_acima_de_9999_nao_trunca(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        await conn.execute("SELECT setval('ops.ticket_seq', 10000, false)")
        result = await create_media_ticket(
            conn, user_id, trace_id, "audio", flags=Flags(db_pool, FakeClock(NOW))
        )
    assert result.ticket_number == "J-10000"


async def test_numeros_sequenciais(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _setup(conn)
        flags = Flags(db_pool, FakeClock(NOW))
        first = await create_media_ticket(conn, user_id, trace_id, "audio", flags=flags)
        second = await create_media_ticket(conn, user_id, None, "image", flags=flags)
    assert (first.ticket_number, second.ticket_number) == ("J-0001", "J-0002")


def test_rotulos_e_numero_puros() -> None:
    assert media_label("audio") == "[áudio]"
    assert media_label("image") == "[imagem]"
    assert media_label("desconhecido") == "[mídia]"
    assert format_ticket_number(7) == "J-0007"
    assert format_ticket_number(12345) == "J-12345"
