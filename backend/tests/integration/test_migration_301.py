"""Migration 301: marca da conversa e kind brand_poll (spec canal-waha.1 9.2; task WA-T03)."""
from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from app.db import migrate
from tests.brain import factories
from tests.db_support import MIGRATIONS_DIR

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
BRANDS = ("jungle", "plant_power", "possible", "a_tal_da_castanha", "zaya")
KINDS = (
    "welcome_template", "optin_reminder", "optin_confirmation", "optin_declined",
    "under_construction", "rate_limit_notice", "media_ack", "opt_out_confirmation",
    "brain_answer", "brain_answer_rate", "brain_notice", "brand_poll",
)  # fmt: skip


async def _insert_outbound(conn: asyncpg.Connection, user_id: int, kind: str) -> None:
    await conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "status, next_attempt_at, created_at) "
        "VALUES ($1, '+5500000000002', $2, 'active', 'x', 'pending', $3, $3)",
        user_id, kind, NOW,
    )  # fmt: skip


async def _set_brand(
    conn: asyncpg.Connection, user_id: int, brand: str | None, at: datetime | None
) -> None:
    await conn.execute(
        "UPDATE access.users SET active_brand = $2, active_brand_at = $3 WHERE id = $1",
        user_id, brand, at,
    )  # fmt: skip


async def test_colunas_da_marca_da_conversa(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'access' AND table_name = 'users' "
        "AND column_name IN ('active_brand', 'active_brand_at') ORDER BY column_name"
    )
    assert [(r["column_name"], r["data_type"], r["is_nullable"]) for r in rows] == [
        ("active_brand", "text", "YES"),
        ("active_brand_at", "timestamp with time zone", "YES"),
    ]


async def test_usuario_novo_sem_marca(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    row = await db_conn.fetchrow(
        "SELECT active_brand, active_brand_at FROM access.users WHERE id = $1", user_id
    )
    assert row is not None and row["active_brand"] is None and row["active_brand_at"] is None


@pytest.mark.parametrize("brand", BRANDS)
async def test_aceita_as_cinco_marcas(db_conn: asyncpg.Connection, brand: str) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    await _set_brand(db_conn, user_id, brand, NOW)


async def test_recusa_marca_invalida(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _set_brand(db_conn, user_id, "todas", NOW)


@pytest.mark.parametrize(("brand", "at"), [("jungle", None), (None, NOW)])
async def test_marca_e_data_andam_juntas(
    db_conn: asyncpg.Connection, brand: str | None, at: datetime | None
) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _set_brand(db_conn, user_id, brand, at)


@pytest.mark.parametrize("kind", KINDS)
async def test_aceita_brand_poll_e_kinds_antigos(db_conn: asyncpg.Connection, kind: str) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    await _insert_outbound(db_conn, user_id, kind)


async def test_recusa_kind_invalido(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _insert_outbound(db_conn, user_id, "enquete_inexistente")


async def test_um_unico_check_de_kind(db_conn: asyncpg.Connection) -> None:
    names = await db_conn.fetch(
        "SELECT c.conname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        "WHERE c.conrelid = 'chat.outbound_messages'::regclass AND c.contype = 'c' "
        "AND a.attname = 'kind'"
    )
    assert [r["conname"] for r in names] == ["outbound_messages_kind_check"]


async def test_migration_idempotente(db_conn: asyncpg.Connection) -> None:
    sql = (MIGRATIONS_DIR / "301_brand_session.sql").read_text(encoding="utf-8")
    await db_conn.execute(sql)
    await db_conn.execute(sql)
    user_id = await factories.create_active_user(db_conn, NOW)
    await _insert_outbound(db_conn, user_id, "brand_poll")
    await _set_brand(db_conn, user_id, "zaya", NOW)


async def test_migrate_check_sem_pendentes(db_conn: asyncpg.Connection) -> None:
    migrations = migrate.discover(MIGRATIONS_DIR)
    assert any(m.version == "301_brand_session" for m in migrations)
    assert await migrate.is_up_to_date(db_conn, MIGRATIONS_DIR)
