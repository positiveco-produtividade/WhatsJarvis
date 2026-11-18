"""O papel do app (`jarvis_app`) so le e grava dados (porta-fechada.2, PA-T06).

Duas provas: (1) varredura do catalogo, objeto por objeto dos schemas do Jarvis, com os
privilegios esperados (PA-4..PA-8); (2) tentativas reais, conectado como `jarvis_app`
(`db_conn`), de tudo que mudaria estrutura, auditoria ou permissoes (PA-5, PA-7, PA-8).
Mais: tabela nova ganha permissao sozinha quando o runner roda (PA-10).
"""

from __future__ import annotations

import asyncpg
import pytest

from app.db import migrate

pytestmark = pytest.mark.db

APP = "jarvis_app"
SCHEMAS = ("access", "chat", "ops", "content")
DML = ("SELECT", "INSERT", "UPDATE", "DELETE")
NEVER = ("TRUNCATE", "REFERENCES", "TRIGGER")
EXPECTED_DML = {
    "ops.audit_log": {"SELECT", "INSERT"},  # PA-5
    "ops.schema_migrations": {"SELECT"},  # PA-6
}


_TABLE_PRIV = "SELECT has_table_privilege($1, $2, $3)"


async def _relations(conn: asyncpg.Connection, kinds: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT format('%I.%I', n.nspname, c.relname) AS name FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = ANY($1::text[]) AND c.relkind = ANY($2::char[]) ORDER BY 1",
        list(SCHEMAS),
        list(kinds),
    )
    return [r["name"] for r in rows]


async def test_toda_tabela_e_visao_com_os_privilegios_certos(
    owner_conn: asyncpg.Connection,
) -> None:
    """PA-4..PA-6: DML em tudo, exceto auditoria e controle de migrations; nunca TRUNCATE."""
    relations = await _relations(owner_conn, "rvpm")
    assert "access.users" in relations and "ops.v_tickets" in relations
    wrong: list[tuple[str, str, bool]] = []
    for rel in relations:
        expected = EXPECTED_DML.get(rel, set(DML))
        for priv in DML + NEVER:
            has = await owner_conn.fetchval(_TABLE_PRIV, APP, rel, priv)
            if has != (priv in expected):
                wrong.append((rel, priv, has))
    assert wrong == []


async def test_toda_sequencia_usa_mas_nao_reinicia(owner_conn: asyncpg.Connection) -> None:
    sequences = await _relations(owner_conn, "S")
    assert "ops.ticket_seq" in sequences
    for seq in sequences:
        check = "SELECT has_sequence_privilege($1, $2, $3)"
        assert await owner_conn.fetchval(check, APP, seq, "USAGE"), seq
        assert await owner_conn.fetchval(check, APP, seq, "SELECT"), seq
        assert not await owner_conn.fetchval(check, APP, seq, "UPDATE"), seq  # setval


async def test_schemas_e_funcoes(owner_conn: asyncpg.Connection) -> None:
    """PA-7, PA-8: usa os schemas do Jarvis, nao cria em nenhum; nada no assistente."""
    priv = "SELECT has_schema_privilege($1, $2, $3)"
    for schema in SCHEMAS:
        assert await owner_conn.fetchval(priv, APP, schema, "USAGE"), schema
        assert not await owner_conn.fetchval(priv, APP, schema, "CREATE"), schema
    assert not await owner_conn.fetchval(priv, APP, "public", "CREATE")
    assert not await owner_conn.fetchval(priv, APP, "assistant", "USAGE")
    for fn in ("ops.apply_app_grants()", "assistant.apply_grants()"):
        assert not await owner_conn.fetchval(
            "SELECT has_function_privilege($1, $2, 'EXECUTE')", APP, fn
        ), fn
    role = await owner_conn.fetchrow(
        "SELECT rolsuper, rolcreatedb, rolcreaterole, rolbypassrls FROM pg_roles "
        "WHERE rolname = $1",
        APP,
    )
    assert role is not None and not any(role.values())


async def test_conexao_dos_testes_e_o_jarvis_app(db_conn: asyncpg.Connection) -> None:
    assert await db_conn.fetchval("SELECT current_user::text") == APP


FORBIDDEN = [
    *(f"CREATE TABLE {s}.pa_nao_pode (id int)" for s in (*SCHEMAS, "public")),
    "CREATE FUNCTION ops.pa_nao_pode() RETURNS int LANGUAGE sql AS 'SELECT 1'",
    "CREATE VIEW chat.pa_nao_pode AS SELECT 1",
    "ALTER TABLE access.users ADD COLUMN pa_nao_pode int",
    "DROP TABLE chat.tickets",
    "TRUNCATE ops.alerts",
    "DROP TRIGGER audit_log_no_update_delete ON ops.audit_log",
    "ALTER TABLE ops.audit_log DISABLE TRIGGER ALL",
    "GRANT SELECT ON assistant.people TO PUBLIC",
    "ALTER TABLE access.users OWNER TO jarvis_app",
    "UPDATE ops.audit_log SET actor = 'outro'",
    "DELETE FROM ops.audit_log",
    "INSERT INTO ops.schema_migrations (version, checksum, applied_at) VALUES ('x', 'x', now())",
    "DELETE FROM ops.schema_migrations",
    "SELECT * FROM assistant.people LIMIT 1",
    "SELECT ops.apply_app_grants()",
    "SELECT setval('ops.ticket_seq', 1)",
]


@pytest.mark.parametrize("statement", FORBIDDEN)
async def test_jarvis_app_recusado(db_conn: asyncpg.Connection, statement: str) -> None:
    """PA-5, PA-7, PA-8: recusa por permissao (nao pelo gatilho nem por erro de sintaxe)."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with db_conn.transaction():
            await db_conn.execute(statement)


async def test_grant_sem_opcao_nao_concede_nada(
    db_conn: asyncpg.Connection, owner_conn: asyncpg.Connection
) -> None:
    """GRANT onde o app ja tem algum privilegio so gera aviso no Postgres: nada muda."""
    await db_conn.execute("GRANT TRUNCATE, UPDATE ON ops.audit_log TO PUBLIC")
    for priv in ("TRUNCATE", "UPDATE"):
        assert not await owner_conn.fetchval(_TABLE_PRIV, "public", "ops.audit_log", priv)
        assert not await owner_conn.fetchval(_TABLE_PRIV, APP, "ops.audit_log", priv)


async def test_auditoria_insere_e_le(db_conn: asyncpg.Connection) -> None:
    await db_conn.execute(
        "INSERT INTO ops.audit_log (at, actor_type, actor, action, target_type, target_id) "
        "VALUES (now(), 'cli', 'teste', 'register', 'user', '1')"
    )
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 1


async def test_tabela_nova_ganha_permissao_quando_o_runner_roda(
    owner_conn: asyncpg.Connection, db_conn: asyncpg.Connection
) -> None:
    """PA-10 (D1): migration futura nao precisa de GRANT manual."""
    await owner_conn.execute("CREATE TABLE ops.pa10_nova (id bigserial PRIMARY KEY, x int)")
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db_conn.execute("INSERT INTO ops.pa10_nova (x) VALUES (1)")
        assert "ops.apply_app_grants()" in await migrate.refresh_grants(owner_conn)
        await db_conn.execute("INSERT INTO ops.pa10_nova (x) VALUES (1)")
        assert await db_conn.fetchval("SELECT count(*) FROM ops.pa10_nova") == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await db_conn.execute("TRUNCATE ops.pa10_nova")
    finally:
        await owner_conn.execute("DROP TABLE IF EXISTS ops.pa10_nova")
