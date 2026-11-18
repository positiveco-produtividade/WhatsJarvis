"""Migration 402: `ops.apply_app_grants()` (PA-T01, spec porta-fechada.2 7.1).

Conecta como dono (`database_url`): so o dono aplica permissoes. A varredura completa do
catalogo e as tentativas reais como `jarvis_app` ficam em tests/security/test_app_role.py.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import asyncpg
import pytest

pytestmark = pytest.mark.db

APP = "jarvis_app"
SCHEMAS = ("access", "chat", "ops", "content")
_ACLS = (
    "SELECT n.nspname, c.relname, c.relacl::text FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = ANY($1::text[]) AND c.relkind IN ('r', 'v', 'S') ORDER BY 1, 2"
)


@pytest.fixture
async def owner(database_url: str, migrated_database: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(database_url)
    try:
        yield conn
    finally:
        await conn.close()


async def _role_exists(conn: asyncpg.Connection, role: str) -> bool:
    sql = "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)"
    return bool(await conn.fetchval(sql, role))


async def test_funcao_existe_e_nao_e_publica(owner: asyncpg.Connection) -> None:
    acl = await owner.fetchval(
        "SELECT proacl::text FROM pg_proc WHERE oid = 'ops.apply_app_grants()'::regprocedure"
    )
    # Sem EXECUTE para PUBLIC: na ACL nao ha entrada "=X/..." (grantee vazio = PUBLIC).
    assert acl is not None
    assert not any(entry.startswith("=") for entry in acl.strip("{}").split(","))


async def test_sem_o_papel_so_avisa(owner: asyncpg.Connection) -> None:
    """PA-11: copia da funcao com um papel que nao existe, em pg_temp: NOTICE e nada quebra."""
    source = await owner.fetchval(
        "SELECT pg_get_functiondef('ops.apply_app_grants()'::regprocedure)"
    )
    ghost = "jarvis_app_inexistente_pa11"
    assert not await _role_exists(owner, ghost)
    fake = source.replace("ops.apply_app_grants()", "pg_temp.apply_ghost_grants()").replace(
        "'jarvis_app'", f"'{ghost}'"
    )
    notices: list[str] = []
    owner.add_log_listener(lambda _c, msg: notices.append(str(msg)))
    await owner.execute(fake)
    await owner.execute("SELECT pg_temp.apply_ghost_grants()")
    assert any("nao existe" in n for n in notices)


async def test_privilegios_do_app(owner: asyncpg.Connection) -> None:
    """PA-4..PA-6 em tabelas representativas (varredura completa na T06)."""
    if not await _role_exists(owner, APP):
        pytest.skip("papel jarvis_app ausente: rode backend/scripts/dev_db_setup.sql")

    async def can(obj: str, priv: str) -> bool:
        return bool(await owner.fetchval("SELECT has_table_privilege($1, $2, $3)", APP, obj, priv))

    for table in ("access.users", "chat.tickets", "chat.outbound_messages", "content.faq_items",
                  "content.faq_suggestions", "ops.system_state", "ops.v_tickets"):  # fmt: skip
        for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await can(table, priv), (table, priv)
        assert not await can(table, "TRUNCATE"), table
    assert await can("ops.audit_log", "INSERT")
    assert await can("ops.audit_log", "SELECT")
    assert not await can("ops.audit_log", "UPDATE")
    assert not await can("ops.audit_log", "DELETE")
    assert await can("ops.schema_migrations", "SELECT")
    assert not await can("ops.schema_migrations", "INSERT")
    schema_priv = "SELECT has_schema_privilege($1, $2, $3)"
    for schema in SCHEMAS:
        assert await owner.fetchval(schema_priv, APP, schema, "USAGE")
        assert not await owner.fetchval(schema_priv, APP, schema, "CREATE")
    assert not await owner.fetchval("SELECT has_schema_privilege($1, 'assistant', 'USAGE')", APP)
    assert await owner.fetchval(
        "SELECT has_sequence_privilege($1, 'ops.ticket_seq', 'USAGE')", APP
    )


async def test_reaplicar_nao_muda_nada(owner: asyncpg.Connection) -> None:
    """PA-9: duas chamadas seguidas, mesmas ACLs e sem erro."""
    await owner.execute("SELECT ops.apply_app_grants()")
    before = [tuple(r) for r in await owner.fetch(_ACLS, list(SCHEMAS) + ["assistant"])]
    await owner.execute("SELECT ops.apply_app_grants()")
    after = [tuple(r) for r in await owner.fetch(_ACLS, list(SCHEMAS) + ["assistant"])]
    assert before == after
