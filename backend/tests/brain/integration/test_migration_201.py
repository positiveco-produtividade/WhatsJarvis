"""Migration 201: kinds do cerebro e reply_buttons em chat.outbound_messages (CB-24)."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import asyncpg
import pytest

from app.db import migrate
from tests.brain import factories
from tests.db_support import MIGRATIONS_DIR

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
NEW_KINDS = ("brain_answer", "brain_answer_rate", "brain_notice")
OLD_KINDS = (
    "welcome_template", "optin_reminder", "optin_confirmation", "optin_declined",
    "under_construction", "rate_limit_notice", "media_ack", "opt_out_confirmation",
)


async def _insert(
    conn: asyncpg.Connection, user_id: int, kind: str, buttons: str | None = None
) -> None:
    await conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "status, next_attempt_at, created_at, reply_buttons) "
        "VALUES ($1, '+5511910000001', $2, 'active', 'x', 'pending', $3, $3, $4::jsonb)",
        user_id, kind, NOW, buttons,
    )


@pytest.mark.parametrize("kind", NEW_KINDS + OLD_KINDS)
async def test_aceita_kinds_novos_e_antigos(db_conn: asyncpg.Connection, kind: str) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    await _insert(db_conn, user_id, kind)


async def test_recusa_kind_invalido(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert(db_conn, user_id, "brain_inexistente")


async def test_um_unico_check_de_kind_com_nome_novo(db_conn: asyncpg.Connection) -> None:
    names = await db_conn.fetch(
        "SELECT c.conname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        "WHERE c.conrelid = 'chat.outbound_messages'::regclass AND c.contype = 'c' "
        "AND a.attname = 'kind'"
    )
    assert [r["conname"] for r in names] == ["outbound_messages_kind_check"]


async def test_reply_buttons_e_jsonb_nulo_por_padrao(db_conn: asyncpg.Connection) -> None:
    col = await db_conn.fetchrow(
        "SELECT data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'chat' AND table_name = 'outbound_messages' "
        "AND column_name = 'reply_buttons'"
    )
    assert col is not None
    assert (col["data_type"], col["is_nullable"]) == ("jsonb", "YES")
    user_id = await factories.create_active_user(db_conn, NOW)
    await _insert(db_conn, user_id, "brain_answer")
    assert await db_conn.fetchval("SELECT reply_buttons FROM chat.outbound_messages") is None


async def test_grava_botoes(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    buttons = [{"id": "RATE_UP:1", "title": "Util"}, {"id": "RATE_DOWN:1", "title": "Nao"}]
    await _insert(db_conn, user_id, "brain_answer_rate", json.dumps(buttons))
    stored = await db_conn.fetchval("SELECT reply_buttons::text FROM chat.outbound_messages")
    assert json.loads(stored) == buttons


async def test_migration_idempotente(db_conn: asyncpg.Connection) -> None:
    sql = (MIGRATIONS_DIR / "201_brain_outbound.sql").read_text(encoding="utf-8")
    await db_conn.execute(sql)
    await db_conn.execute(sql)
    user_id = await factories.create_active_user(db_conn, NOW)
    await _insert(db_conn, user_id, "brain_notice")


async def test_migrate_check_sem_pendentes(db_conn: asyncpg.Connection) -> None:
    migrations = migrate.discover(MIGRATIONS_DIR)
    assert any(m.version == "201_brain_outbound" for m in migrations)
    assert await migrate.is_up_to_date(db_conn, MIGRATIONS_DIR)
