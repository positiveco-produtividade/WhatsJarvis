"""Migrations 102 (tickets: busca e indices), 104 (busca de pessoas) e 107 (trigram opcional)."""
from __future__ import annotations

import asyncpg
import pytest

from tests.db_support import MIGRATIONS_DIR
from tests.integration.test_schema_access_chat import add_user
from tests.integration.test_schema_ops_tickets import add_ticket

pytestmark = pytest.mark.db

PF_TICKET_COLUMNS = [
    "id", "ticket_number", "user_id", "trace_id", "kind", "brand", "classification",
    "confidence", "message", "summary", "escalation_reason", "escalated", "status", "urgency",
    "answered_by", "answered_at", "responsible", "meta", "created_at", "updated_at",
]


async def _view_columns(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='ops' AND table_name='v_tickets' ORDER BY ordinal_position"
    )
    return [r["column_name"] for r in rows]


async def test_migrations_registradas(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch("SELECT version FROM ops.schema_migrations")
    versions = {r["version"] for r in rows}
    assert {"102_tickets_panel", "104_access_search", "107_trgm_optional"} <= versions


async def test_visao_mantem_colunas_de_pf_e_so_ganha_search_key(
    db_conn: asyncpg.Connection,
) -> None:
    cols = await _view_columns(db_conn)
    assert cols[: len(PF_TICKET_COLUMNS)] == PF_TICKET_COLUMNS
    assert cols[len(PF_TICKET_COLUMNS):] == ["search_key"]


async def test_indices_de_tickets(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch("SELECT indexname FROM pg_indexes WHERE tablename='tickets'")
    names = {r["indexname"] for r in rows}
    assert {"tickets_brand_idx", "tickets_status_idx", "tickets_created_idx",
            "tickets_user_idx", "tickets_awaiting_idx"} <= names


async def test_busca_de_ticket_sem_acento_e_sem_caixa(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await add_ticket(db_conn, uid, ticket_number="J-0001", message="Preço da Castanha é AÇÃO?")
    await add_ticket(db_conn, uid, ticket_number="J-0002", message=None)
    for term in ("preco", "castanha e acao", "j-0001"):
        found = await db_conn.fetch(
            "SELECT ticket_number FROM ops.v_tickets WHERE search_key LIKE '%' || $1 || '%'", term
        )
        assert [r["ticket_number"] for r in found] == ["J-0001"], term
    nulo = await db_conn.fetchval(
        "SELECT search_key FROM ops.v_tickets WHERE ticket_number='J-0002'"
    )
    assert nulo == "j-0002 "


async def test_search_key_de_ticket_e_gerada(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    await add_ticket(db_conn, uid)
    with pytest.raises(asyncpg.GeneratedAlwaysError):
        await db_conn.execute("UPDATE chat.tickets SET search_key = 'x'")


async def test_busca_de_pessoas_sem_acento(db_conn: asyncpg.Connection) -> None:
    await add_user(db_conn, name="JOSÉ da Conceição", phone_key="551100000001")
    await add_user(db_conn, name="Ana", phone_key="551100000002")
    key = await db_conn.fetchval(
        "SELECT search_key FROM access.users WHERE phone_key='551100000001'"
    )
    assert key == "jose da conceicao acme"
    found = await db_conn.fetch(
        "SELECT phone_key FROM access.users WHERE search_key LIKE '%' || $1 || '%'", "conceicao"
    )
    assert [r["phone_key"] for r in found] == ["551100000001"]


async def test_107_reaplicavel_e_coerente_com_a_extensao(db_conn: asyncpg.Connection) -> None:
    sql = (MIGRATIONS_DIR / "107_trgm_optional.sql").read_text(encoding="utf-8")
    await db_conn.execute(sql)  # idempotente: nao falha com ou sem pg_trgm
    await db_conn.execute(sql)
    has_ext = await db_conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='pg_trgm')"
    )
    idx = await db_conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE indexname IN "
        "('tickets_search_trgm_idx','users_search_trgm_idx')"
    )
    if not has_ext:
        assert idx == 0
    else:
        assert idx in (0, 2)  # 0 so se a classe de operadores nao estiver no search_path
