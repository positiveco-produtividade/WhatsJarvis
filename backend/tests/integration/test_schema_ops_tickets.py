"""Schema das migrations 004-005 contra Postgres real: alertas, entregas, contadores e tickets."""

from datetime import UTC, date, datetime
from typing import Any

import asyncpg
import pytest

from tests.integration.test_schema_access_chat import add_trace, add_user

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

pytestmark = pytest.mark.db

TICKET_SQL = """
INSERT INTO chat.tickets (ticket_number, user_id, trace_id, kind, brand, classification, message,
  escalated, status, urgency, created_at, updated_at)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $11)
RETURNING id
"""


async def add_ticket(conn: asyncpg.Connection, user_id: int, **over: Any) -> int:
    args: dict[str, Any] = {
        "ticket_number": "J-0001",
        "user_id": user_id,
        "trace_id": None,
        "kind": "media",
        "brand": None,
        "classification": "MIDIA",
        "message": "[áudio]",
        "escalated": True,
        "status": "awaiting",
        "urgency": "urgente",
        "now": NOW,
    }
    args.update(over)
    return int(await conn.fetchval(TICKET_SQL, *args.values()))


async def add_delivery(conn: asyncpg.Connection, **over: Any) -> None:
    args: dict[str, Any] = {
        "source_type": "alert",
        "source_id": 1,
        "channel": "email",
        "status": "pending",
    }
    args.update(over)
    await conn.execute(
        "INSERT INTO ops.deliveries (source_type, source_id, channel, status, next_attempt_at, "
        "created_at) VALUES ($1, $2, $3, $4, $5, $5)",
        *args.values(),
        NOW,
    )


async def test_migrations_004_005_registradas(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch("SELECT version FROM ops.schema_migrations")
    versions = {r["version"] for r in rows}
    assert {"004_ops_alerts_deliveries", "005_chat_tickets"} <= versions


async def test_alerta_valido_e_severidade_invalida(db_conn: asyncpg.Connection) -> None:
    sql = "INSERT INTO ops.alerts (severity, kind, message, created_at) VALUES ($1, 'k', 'm', $2)"
    await db_conn.execute(sql, "warn", NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await db_conn.execute(sql, "gravissimo", NOW)
    payload = await db_conn.fetchval("SELECT payload::text FROM ops.alerts")
    assert payload == "{}"


async def test_sequencia_gera_j_0001(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute("SELECT setval('ops.ticket_seq', 1, false)")
    n = await db_conn.fetchval("SELECT nextval('ops.ticket_seq')")
    assert f"J-{n:04d}" == "J-0001"


async def test_entrega_unica_por_origem_e_canal(db_conn: asyncpg.Connection) -> None:
    await add_delivery(db_conn)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_delivery(db_conn)
    await add_delivery(db_conn, channel="group")  # outro canal, mesma origem


async def test_entrega_de_ticket_por_email_e_recusada(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await add_delivery(db_conn, source_type="ticket", channel="email")
    await add_delivery(db_conn, source_type="ticket", channel="group", status="panel_only")
    await add_delivery(db_conn, source_type="alert", channel="email", status="skipped")


@pytest.mark.parametrize(
    "over",
    [{"channel": "sms"}, {"status": "enviado"}, {"source_type": "outro"}],
)
async def test_entrega_checks_de_dominio(db_conn: asyncpg.Connection, over: dict[str, Any]) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await add_delivery(db_conn, **over)


async def test_contador_de_seguranca_chave_dupla(db_conn: asyncpg.Connection) -> None:
    sql = "INSERT INTO ops.security_counters (day, kind, count) VALUES ($1, $2, 1)"
    await db_conn.execute(sql, date(2026, 10, 23), "invalid_signature")
    await db_conn.execute(sql, date(2026, 10, 23), "oversize_payload")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db_conn.execute(sql, date(2026, 10, 23), "invalid_signature")


async def test_ticket_de_midia_gravavel(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    trace = await add_trace(db_conn, user_id=uid)
    await add_ticket(db_conn, uid, trace_id=trace)
    row = await db_conn.fetchrow("SELECT * FROM ops.v_tickets")
    assert row is not None
    assert (row["kind"], row["classification"], row["escalated"]) == ("media", "MIDIA", True)
    assert (row["status"], row["urgency"], row["brand"]) == ("awaiting", "urgente", None)
    assert row["trace_id"] == trace
    assert row["meta"] == "{}"


async def test_view_expoe_as_colunas_da_tabela(db_conn: asyncpg.Connection) -> None:
    cols_sql = (
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = $1 AND table_name = $2 ORDER BY ordinal_position"
    )
    table = [r["column_name"] for r in await db_conn.fetch(cols_sql, "chat", "tickets")]
    view = [r["column_name"] for r in await db_conn.fetch(cols_sql, "ops", "v_tickets")]
    assert view == table
    assert {"ticket_number", "urgency", "escalated", "classification", "meta"} <= set(view)


async def test_ticket_escalado_sem_urgencia_falha(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await add_ticket(db_conn, uid, urgency=None)
    await add_ticket(db_conn, uid, escalated=False, urgency=None, status="answered_bot")


@pytest.mark.parametrize(
    "over",
    [
        {"kind": "outro"},
        {"brand": "marca_x"},
        {"classification": "FOO"},
        {"status": "aberto"},
        {"urgency": "critica"},
    ],
)
async def test_ticket_checks_de_dominio(db_conn: asyncpg.Connection, over: dict[str, Any]) -> None:
    uid = await add_user(db_conn)
    with pytest.raises(asyncpg.CheckViolationError):
        await add_ticket(db_conn, uid, **over)


async def test_ticket_number_unico_e_usuario_obrigatorio(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await add_ticket(db_conn, uid)
    with pytest.raises(asyncpg.UniqueViolationError):
        await add_ticket(db_conn, uid)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await add_ticket(db_conn, 999999, ticket_number="J-0002")


async def test_classificacao_padrao_nao_identificado(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await db_conn.execute(
        "INSERT INTO chat.tickets (ticket_number, user_id, kind, status, created_at, updated_at) "
        "VALUES ('J-0009', $1, 'question', 'awaiting', $2, $2)",
        uid,
        NOW,
    )
    got = await db_conn.fetchval("SELECT classification FROM chat.tickets")
    assert got == "NAO_IDENTIFICADO"
