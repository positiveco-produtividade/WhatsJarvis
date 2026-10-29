"""audit.write: grava sem PII, tabela imutavel, falha propaga (AC-34)."""

import json

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services import audit

pytestmark = pytest.mark.db


async def test_write_grava_linha_com_json(db_conn: asyncpg.Connection) -> None:
    clock = FakeClock()
    row_id = await audit.write(
        db_conn, "cli", "caio", "user.revoke", "user", "12",
        {"status": "active"}, {"status": "revoked"}, clock=clock,
    )
    row = await db_conn.fetchrow(
        "SELECT at, actor_type, actor, action, target_type, target_id, "
        "old_value::text AS old_value, new_value::text AS new_value "
        "FROM ops.audit_log WHERE id = $1",
        row_id,
    )
    assert row is not None
    assert row["at"] == clock.now()
    assert (row["actor_type"], row["actor"], row["action"]) == ("cli", "caio", "user.revoke")
    assert (row["target_type"], row["target_id"]) == ("user", "12")
    assert json.loads(row["old_value"]) == {"status": "active"}
    assert json.loads(row["new_value"]) == {"status": "revoked"}


async def test_old_e_new_opcionais(db_conn: asyncpg.Connection) -> None:
    row_id = await audit.write(db_conn, "system", "jarvis", "x", "t", "1")
    row = await db_conn.fetchrow(
        "SELECT old_value, new_value FROM ops.audit_log WHERE id = $1", row_id
    )
    assert row is not None
    assert row["old_value"] is None and row["new_value"] is None


async def test_actor_type_invalido_recusado_antes_do_banco(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(InvalidValue):
        await audit.write(db_conn, "robot", "a", "x", "t", "1")
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 0


async def test_falha_ao_gravar_propaga_e_aborta_a_transacao(
    db_pool: asyncpg.Pool,
) -> None:
    async with db_pool.acquire() as conn:
        with pytest.raises(asyncpg.PostgresError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
                    "VALUES ('k', 'true'::jsonb, now(), 't')"
                )
                # action NULL viola NOT NULL: a gravacao falha
                await conn.fetchval(
                    "INSERT INTO ops.audit_log (at, actor_type, actor, action, "
                    "target_type, target_id) VALUES (now(), 'cli', 'a', NULL, 't', '1')"
                )
        assert await conn.fetchval("SELECT count(*) FROM ops.system_state") == 0


async def test_audit_log_e_imutavel(db_conn: asyncpg.Connection) -> None:
    row_id = await audit.write(db_conn, "cli", "caio", "x", "t", "1")
    with pytest.raises(asyncpg.PostgresError):
        await db_conn.execute("UPDATE ops.audit_log SET actor = 'y' WHERE id = $1", row_id)
    with pytest.raises(asyncpg.PostgresError):
        await db_conn.execute("DELETE FROM ops.audit_log WHERE id = $1", row_id)
