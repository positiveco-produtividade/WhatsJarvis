"""Migration 105: visoes sem PII do assistente e papel somente leitura (PN-T51)."""
from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest

pytestmark = pytest.mark.db

SKIP_REASON = "rode backend/scripts/dev_db_setup.sql para criar jarvis_assistant"
VIEWS = {"people", "tickets", "messages", "faq", "alerts"}
FORBIDDEN_COLUMNS = {
    "body", "phone_e164", "phone_key", "bsuid", "to_phone", "wa_message_id",
    "reply_wa_message_id", "message", "send_error", "payload", "old_value", "new_value",
}


async def _columns(conn: asyncpg.Connection) -> dict[str, set[str]]:
    rows = await conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'assistant'"
    )
    out: dict[str, set[str]] = {}
    for r in rows:
        out.setdefault(r["table_name"], set()).add(r["column_name"])
    return out


async def test_migration_registrada(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch("SELECT version FROM ops.schema_migrations")
    assert "105_roles_assistant" in {r["version"] for r in rows}


async def test_visoes_existem_e_sem_pii(db_conn: asyncpg.Connection) -> None:
    cols = await _columns(db_conn)
    assert set(cols) == VIEWS
    for view, names in cols.items():
        assert not (names & FORBIDDEN_COLUMNS), view
    tables = await db_conn.fetch(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'assistant'"
    )
    assert not {r["table_name"] for r in tables} & {"unknown_first_messages", "audit_log"}


async def test_visoes_consultaveis_pelo_dono(db_conn: asyncpg.Connection) -> None:
    for view in VIEWS:
        await db_conn.fetch(f"SELECT * FROM assistant.{view} LIMIT 1")  # nome fixo, sem entrada


async def test_apply_grants_idempotente(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute("SELECT assistant.apply_grants()")
    await db_conn.execute("SELECT assistant.apply_grants()")


@pytest.fixture
async def as_assistant(db_conn: asyncpg.Connection) -> AsyncIterator[asyncpg.Connection]:
    exists = await db_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_assistant')"
    )
    if not exists:
        pytest.skip(SKIP_REASON)
    await db_conn.execute("SELECT assistant.apply_grants()")
    tx = db_conn.transaction()
    await tx.start()
    try:
        await db_conn.execute("SET LOCAL ROLE jarvis_assistant")
    except asyncpg.InsufficientPrivilegeError:
        await tx.rollback()
        pytest.skip("usuario de teste nao pode SET ROLE jarvis_assistant (sem associacao)")
    try:
        yield db_conn
    finally:
        await tx.rollback()


async def _denied(conn: asyncpg.Connection, sql: str) -> None:
    sp = conn.transaction()
    await sp.start()
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(sql)
    finally:
        await sp.rollback()


async def test_leitura_das_visoes_permitida(as_assistant: asyncpg.Connection) -> None:
    for view in VIEWS:
        await as_assistant.fetch(f"SELECT * FROM assistant.{view} LIMIT 1")


@pytest.mark.parametrize("verb", [
    "INSERT INTO assistant.faq (id) VALUES (1)",
    "UPDATE assistant.faq SET answer = 'x'",
    "DELETE FROM assistant.faq",
    "UPDATE assistant.people SET name = 'x'",
])
async def test_escrita_negada(as_assistant: asyncpg.Connection, verb: str) -> None:
    await _denied(as_assistant, verb)


@pytest.mark.parametrize("sql", [
    "SELECT body FROM chat.message_trace",
    "SELECT * FROM access.unknown_first_messages",
    "SELECT phone_e164, phone_key FROM access.users",
    "SELECT * FROM ops.audit_log",
    "SELECT * FROM chat.tickets",
    "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id) "
    "VALUES (now(), 'cli', 'x', 'x', 'x', 'x')",
    "SELECT assistant.apply_grants()",
])
async def test_acesso_a_dados_sensiveis_negado(as_assistant: asyncpg.Connection, sql: str) -> None:
    await _denied(as_assistant, sql)
