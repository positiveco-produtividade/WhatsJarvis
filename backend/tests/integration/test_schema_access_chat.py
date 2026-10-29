"""Schema das migrations 001-003 contra Postgres real: CHECKs, UNIQUEs e auditoria imutavel."""

import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
BACKEND_DIR = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.db

USER_SQL = """
INSERT INTO access.users (name, phone_e164, phone_key, bsuid, profile, company, ufs, channels,
  brands, manager_name, status, status_changed_at, accepted_at, accepted_notice_version,
  revoked_at, revoked_by, proactive_opt_out, proactive_opt_out_at, created_at, created_by,
  updated_at)
VALUES ($1, $2, $3, $4, $5, 'Acme', $6, $7, $8, 'Gestora', $9, $10, $11, $12, $13, $14, $15, $16,
  $10, 'teste', $10)
RETURNING id
"""


async def add_user(conn: asyncpg.Connection, **over: Any) -> int:
    args: dict[str, Any] = {
        "name": "Ana",
        "phone_e164": "+5511987654321",
        "phone_key": "551187654321",
        "bsuid": None,
        "profile": "vendedor",
        "ufs": ["SP"],
        "channels": ["varejo"],
        "brands": ["jungle"],
        "status": "pending_optin",
        "now": NOW,
        "accepted_at": None,
        "accepted_notice_version": None,
        "revoked_at": None,
        "revoked_by": None,
        "proactive_opt_out": False,
        "proactive_opt_out_at": None,
    }
    args.update(over)
    return int(await conn.fetchval(USER_SQL, *args.values()))


async def add_trace(conn: asyncpg.Connection, wa_id: str = "wamid.1", **over: Any) -> int:
    cols: dict[str, Any] = {
        "wa_message_id": wa_id,
        "received_at": NOW,
        "msg_type": "text",
        "access_decision": "allowed",
        "decision": "allowed",
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join(f"${i}" for i in range(1, len(cols) + 1))
    sql = f"INSERT INTO chat.message_trace ({names}) VALUES ({marks}) RETURNING id"
    return int(await conn.fetchval(sql, *cols.values()))


async def test_migrations_001_003_registradas(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch("SELECT version, checksum FROM ops.schema_migrations")
    versions = {r["version"] for r in rows}
    assert {"001_schemas_ops_base", "002_access", "003_chat"} <= versions
    assert all(len(r["checksum"]) == 64 for r in rows)


def test_migrate_check_sai_zero_apos_aplicar(migrated_database: str) -> None:
    env = {**os.environ, "DATABASE_URL": migrated_database}
    result = subprocess.run(
        [sys.executable, "-m", "app.db.migrate", "--check"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout


async def test_usuario_valido_e_phone_key_unico(db_conn: asyncpg.Connection) -> None:
    await add_user(db_conn)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_user(db_conn, phone_e164="+551187654321")


async def test_bsuid_unico(db_conn: asyncpg.Connection) -> None:
    await add_user(db_conn, bsuid="BS1")
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_user(db_conn, phone_key="551199999999", bsuid="BS1")


@pytest.mark.parametrize(
    "over",
    [
        {"profile": "estagiario"},
        {"status": "inexistente"},
        {"ufs": []},
        {"channels": []},
        {"brands": []},
        {"status": "active"},  # active_needs_acceptance: sem aceite
        {"status": "active", "accepted_at": NOW},  # sem versao do aviso
        {"status": "revoked"},  # revoked_needs_author
        {"status": "revoked", "revoked_at": NOW},  # sem autor
        {"proactive_opt_out": True},  # opt_out_consistent
        {"proactive_opt_out_at": NOW},
    ],
)
async def test_users_checks_rejeitam(db_conn: asyncpg.Connection, over: dict[str, Any]) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await add_user(db_conn, **over)


async def test_users_checks_aceitam_casos_validos(db_conn: asyncpg.Connection) -> None:
    await add_user(
        db_conn,
        phone_key="a",
        status="active",
        accepted_at=NOW,
        accepted_notice_version="v1",
    )
    await add_user(db_conn, phone_key="b", status="revoked", revoked_at=NOW, revoked_by="cli:caio")
    await add_user(db_conn, phone_key="c", proactive_opt_out=True, proactive_opt_out_at=NOW)


async def test_motivo_de_revogacao_fora_da_lista(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(
            "UPDATE access.users SET revoked_reason = 'porque_sim' WHERE id = $1", uid
        )


async def test_um_aviso_aberto_por_usuario(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    sql = (
        "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, issued_by)"
        " VALUES ($1, $2, 'v1', $3, 'cli')"
    )
    await db_conn.execute(sql, uid, "open", NOW)
    await db_conn.execute(sql, uid, "superseded", NOW)
    await db_conn.execute(sql, uid, "accepted", NOW)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_conn.execute(sql, uid, "open", NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(sql, uid, "outro", NOW)


ATTEMPT_SQL = """
INSERT INTO access.access_attempts (phone_key, bsuid, reason, attempt_count, first_attempt_at,
  last_attempt_at, first_msg_type)
VALUES ($1, $2, $3, $4, $5, $5, 'text') RETURNING id
"""


async def test_access_attempts_checks(db_conn: asyncpg.Connection) -> None:
    await db_conn.fetchval(ATTEMPT_SQL, "551100000001", None, "unknown", 1, NOW)
    await db_conn.fetchval(ATTEMPT_SQL, None, "BS9", "unknown", 1, NOW)
    with pytest.raises(asyncpg.CheckViolationError):  # has_identifier
        await db_conn.fetchval(ATTEMPT_SQL, None, None, "unknown", 1, NOW)
    with pytest.raises(asyncpg.CheckViolationError):  # attempt_count >= 1
        await db_conn.fetchval(ATTEMPT_SQL, "551100000002", None, "unknown", 0, NOW)
    with pytest.raises(asyncpg.CheckViolationError):  # reason
        await db_conn.fetchval(ATTEMPT_SQL, "551100000003", None, "x", 1, NOW)
    with pytest.raises(asyncpg.UniqueViolationError):  # phone_key unico
        await db_conn.fetchval(ATTEMPT_SQL, "551100000001", None, "unknown", 1, NOW)


async def test_unknown_first_messages_cascade(db_conn: asyncpg.Connection) -> None:
    attempt = await db_conn.fetchval(ATTEMPT_SQL, "551100000001", None, "unknown", 1, NOW)
    await db_conn.execute(
        "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at)"
        " VALUES ($1, 'oi', $2)",
        attempt,
        NOW,
    )
    await db_conn.execute("DELETE FROM access.access_attempts WHERE id = $1", attempt)
    assert await db_conn.fetchval("SELECT count(*) FROM access.unknown_first_messages") == 0


async def test_wa_message_id_unico(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await add_trace(db_conn, "wamid.A", user_id=uid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_trace(db_conn, "wamid.A", user_id=uid)


async def test_message_trace_has_sender(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await add_trace(db_conn, "wamid.B")
    await add_trace(db_conn, "wamid.C", access_decision="error", decision="error")
    attempt = await db_conn.fetchval(ATTEMPT_SQL, "551100000001", None, "unknown", 1, NOW)
    await add_trace(db_conn, "wamid.D", attempt_id=attempt)


async def test_message_trace_defaults(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    tid = await add_trace(db_conn, "wamid.E", user_id=uid)
    row = await db_conn.fetchrow(
        "SELECT counted_in_limit, reply_kind FROM chat.message_trace WHERE id = $1", tid
    )
    assert row is not None
    assert row["counted_in_limit"] is False
    assert row["reply_kind"] == "none"


OUTBOUND_SQL = """
INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, status,
  next_attempt_at, created_at, wa_message_id)
VALUES ($1, '+5511987654321', $2, 'active', $3, $4, $4, $5)
"""


async def test_outbound_messages_checks_e_unique(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await db_conn.execute(OUTBOUND_SQL, uid, "media_ack", "pending", NOW, "wamid.O1")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_conn.execute(OUTBOUND_SQL, uid, "media_ack", "sent", NOW, "wamid.O1")
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(OUTBOUND_SQL, uid, "kind_invalido", "pending", NOW, None)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(OUTBOUND_SQL, uid, "media_ack", "status_invalido", NOW, None)
    row = await db_conn.fetchrow("SELECT attempts FROM chat.outbound_messages")
    assert row is not None
    assert row["attempts"] == 0


async def test_audit_log_recusa_update_e_delete(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute(
        "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id)"
        " VALUES ($1, 'cli', 'caio', 'register', 'user', '1')",
        NOW,
    )
    with pytest.raises(asyncpg.RaiseError):
        await db_conn.execute("UPDATE ops.audit_log SET actor = 'outro'")
    with pytest.raises(asyncpg.RaiseError):
        await db_conn.execute("DELETE FROM ops.audit_log")
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 1


async def test_audit_log_actor_type_check(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(
            "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id)"
            " VALUES ($1, 'robo', 'x', 'a', 't', '1')",
            NOW,
        )


async def test_system_state_chave_unica(db_conn: asyncpg.Connection) -> None:
    sql = (
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by)"
        " VALUES ('paused', 'false'::jsonb, $1, 'cli')"
    )
    await db_conn.execute(sql, NOW)
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_conn.execute(sql, NOW)
