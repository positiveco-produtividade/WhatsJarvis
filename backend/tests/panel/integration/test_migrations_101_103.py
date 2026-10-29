"""Migrations 101 (FAQ) e 103 (ops do painel): esquema, CHECKs, unico parcial, gatilho (PN-T08)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from tests.panel import factories

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

_FAQ_SQL = (
    "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
    "bot_responde, active, origin, created_at, created_by, updated_at, updated_by) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, 'teste', $9, 'teste') RETURNING id"
)


@pytest.fixture
async def conn(db_conn: asyncpg.Connection) -> asyncpg.Connection:
    await factories.truncate_content(db_conn)  # db_support nao limpa o schema content
    return db_conn


async def _faq(
    c: asyncpg.Connection,
    *,
    brand: str = "jungle",
    category: str = "PRODUTO",
    question: str = "Q?",
    key: str = "q",
    answer: str | None = "R",
    bot: bool = True,
    active: bool = True,
    origin: str = "manual",
) -> int:
    return int(
        await c.fetchval(_FAQ_SQL, brand, category, question, key, answer, bot, active, origin, NOW)
    )


async def _revision(c: asyncpg.Connection) -> int | None:
    raw = await c.fetchval("SELECT value::text FROM ops.system_state WHERE key = 'faq_revision'")
    return None if raw is None else int(json.loads(raw))


async def _rejects(c: asyncpg.Connection, check: str, **kw: object) -> None:
    with pytest.raises(asyncpg.CheckViolationError) as exc:
        await _faq(c, **kw)  # type: ignore[arg-type]
    assert check in str(exc.value)


async def test_tabelas_e_indices_existem(conn: asyncpg.Connection) -> None:
    tables = {
        (r["schemaname"], r["tablename"])
        for r in await conn.fetch(
            "SELECT schemaname, tablename FROM pg_tables WHERE schemaname IN ('content','ops')"
        )
    }
    for t in [
        ("content", "faq_items"),
        ("content", "faq_item_versions"),
        ("ops", "panel_sessions"),
        ("ops", "import_batches"),
        ("ops", "assistant_questions"),
    ]:
        assert t in tables
    idx = {r["indexname"] for r in await conn.fetch("SELECT indexname FROM pg_indexes")}
    assert {
        "faq_active_question_uq",
        "faq_bot_read_idx",
        "faq_list_idx",
        "panel_sessions_exp_idx",
        "assistant_q_idx",
        "audit_at_idx",
        "audit_action_idx",
        "audit_target_idx",
        "alerts_unack_idx",
    } <= idx


async def test_checks_da_faq(conn: asyncpg.Connection) -> None:
    await _rejects(conn, "brand", brand="outra")
    await _rejects(conn, "category", category="MIDIA")
    await _rejects(conn, "category", category="produto")
    await _rejects(conn, "origin", origin="planilha")
    await _rejects(conn, "question", question="")
    await _rejects(conn, "question", question="x" * 501)
    await _rejects(conn, "answer_required", answer=None, bot=True)


async def test_valores_aceitos_da_faq(conn: asyncpg.Connection) -> None:
    for i, cat in enumerate(["PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA"]):
        await _faq(conn, category=cat, key=f"k{i}")
    brands = ["todas", "jungle", "plant_power", "possible", "a_tal_da_castanha", "zaya"]
    for i, brand in enumerate(brands):
        await _faq(conn, brand=brand, key=f"b{i}")
    await _faq(conn, question="x" * 500, key="max")
    await _faq(conn, answer=None, bot=False, key="sem-resposta")
    row = await conn.fetchrow("SELECT version, active, variations FROM content.faq_items LIMIT 1")
    assert row is not None
    assert (row["version"], row["active"], list(row["variations"])) == (1, True, [])


async def test_unico_parcial_so_vale_entre_ativas(conn: asyncpg.Connection) -> None:
    first = await _faq(conn, key="mesma")
    with pytest.raises(asyncpg.UniqueViolationError) as exc:
        await _faq(conn, key="mesma")
    assert "faq_active_question_uq" in str(exc.value)
    await _faq(conn, brand="zaya", key="mesma")  # outra marca pode
    await _faq(conn, key="mesma", active=False)  # inativa pode
    await conn.execute("UPDATE content.faq_items SET active = false WHERE id = $1", first)
    await _faq(conn, key="mesma")  # liberada ao desativar a anterior
    with pytest.raises(asyncpg.UniqueViolationError):  # reativar colidiria com a ativa
        await conn.execute("UPDATE content.faq_items SET active = true WHERE id = $1", first)


async def test_versoes_chave_check_e_fk(conn: asyncpg.Connection) -> None:
    item = await _faq(conn)
    sql = "INSERT INTO content.faq_item_versions VALUES ($1, $2, '{}'::jsonb, $3, $4, 'caio')"
    await conn.execute(sql, item, 1, "create", NOW)
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(sql, item, 1, "update", NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, item, 2, "apagar", NOW)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await conn.execute(sql, item + 999, 1, "create", NOW)
    for i, kind in enumerate(["update", "deactivate", "reactivate", "import", "seed"], start=2):
        await conn.execute(sql, item, i, kind, NOW)


async def test_gatilho_incrementa_faq_revision(conn: asyncpg.Connection) -> None:
    assert await _revision(conn) is None
    item = await _faq(conn)
    assert await _revision(conn) == 1
    await conn.execute("UPDATE content.faq_items SET answer = 'novo' WHERE id = $1", item)
    assert await _revision(conn) == 2
    await conn.execute("UPDATE content.faq_items SET active = false WHERE id = $1", item)
    assert await _revision(conn) == 3
    by = await conn.fetchval("SELECT updated_by FROM ops.system_state WHERE key = 'faq_revision'")
    assert by == "faq_trigger"


async def test_gatilho_em_lote_conta_uma_revisao_e_retoma_valor_existente(
    conn: asyncpg.Connection,
) -> None:
    await conn.execute(
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
        "VALUES ('faq_revision', '41'::jsonb, $1, 'x')",
        NOW,
    )
    await conn.execute(
        "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
        "bot_responde, origin, created_at, created_by, updated_at, updated_by) "
        "SELECT 'jungle', 'PRECO', 'P'||g, 'p'||g, 'R', true, 'import', $1, 't', $1, 't' "
        "FROM generate_series(1, 5) g",
        NOW,
    )
    assert await _revision(conn) == 42


async def test_falha_nao_incrementa_revisao(conn: asyncpg.Connection) -> None:
    await _faq(conn, key="a")
    with pytest.raises(asyncpg.UniqueViolationError):
        await _faq(conn, key="a")
    assert await _revision(conn) == 1


async def test_panel_sessions(conn: asyncpg.Connection) -> None:
    sql = (
        "INSERT INTO ops.panel_sessions (id_hash, email, name, csrf_secret, created_at, "
        "last_seen_at, last_iam_check_at, expires_at) VALUES ($1,'a@b.c','A','s',$2,$2,$2,$2)"
    )
    await conn.execute(sql, "h1", NOW)
    with pytest.raises(asyncpg.UniqueViolationError):
        await conn.execute(sql, "h1", NOW)
    revoked = await conn.fetchval("SELECT revoked_at FROM ops.panel_sessions WHERE id_hash='h1'")
    assert revoked is None


async def test_import_batches_checks(conn: asyncpg.Connection) -> None:
    sql = (
        "INSERT INTO ops.import_batches (id, kind, actor, filename, sha256, size_bytes, rows, "
        "summary, status, created_at, expires_at) "
        "VALUES ($1, $2, 'caio', 'f.csv', 'abc', 10, '[]'::jsonb, '{}'::jsonb, $3, $4, $4)"
    )
    await conn.execute(sql, uuid.uuid4(), "faq", "preview", NOW)
    for status in ("committed", "cancelled", "expired"):
        await conn.execute(sql, uuid.uuid4(), "people", status, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, uuid.uuid4(), "outro", "preview", NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.execute(sql, uuid.uuid4(), "faq", "pendente", NOW)


async def test_assistant_questions(conn: asyncpg.Connection) -> None:
    sql = (
        "INSERT INTO ops.assistant_questions (asked_at, asked_by, agent, question, status) "
        "VALUES ($1, 'caio', 'ops', 'oi?', $2) RETURNING tools_used"
    )
    assert list(await conn.fetchval(sql, NOW, "ok")) == []
    await conn.fetchval(sql, NOW, "error")
    with pytest.raises(asyncpg.CheckViolationError):
        await conn.fetchval(sql, NOW, "timeout")


async def test_definicao_dos_indices_de_alertas_e_auditoria(conn: asyncpg.Connection) -> None:
    defs = {
        r["indexname"]: r["indexdef"]
        for r in await conn.fetch(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'ops'"
        )
    }
    assert "WHERE (acknowledged_at IS NULL)" in defs["alerts_unack_idx"]
    assert "(action, at DESC)" in defs["audit_action_idx"]
    assert "(target_type, target_id)" in defs["audit_target_idx"]


async def test_migrations_registradas(conn: asyncpg.Connection) -> None:
    versions = {r["version"] for r in await conn.fetch("SELECT version FROM ops.schema_migrations")}
    assert {"101_content_faq", "103_ops_panel"} <= versions
