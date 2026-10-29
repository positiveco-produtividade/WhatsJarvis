"""Runner de migrations contra Postgres real.

Usa um diretorio temporario e uma tabela de controle propria (public.mig_runner_test),
para nao interferir nas migrations reais aplicadas pela sessao.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest

from app.db import migrate
from app.db.migrate import MigrationError
from tests.db_support import assert_test_database

TABLE = "public.mig_runner_test"
OBJECTS = ("mig_runner_a", "mig_runner_b")


def _write(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8", newline="")


async def _cleanup(conn: asyncpg.Connection) -> None:
    await conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    for name in OBJECTS:
        await conn.execute(f"DROP TABLE IF EXISTS public.{name}")


@pytest.fixture
async def conn(database_url: str) -> AsyncIterator[asyncpg.Connection]:
    connection = await asyncpg.connect(database_url)
    await _cleanup(connection)
    try:
        yield connection
    finally:
        await _cleanup(connection)
        await connection.close()


@pytest.fixture
def mig_dir(tmp_path: Path) -> Path:
    _write(tmp_path, "001_a.sql", "CREATE TABLE public.mig_runner_a (id int);")
    _write(tmp_path, "002_b.sql", "CREATE TABLE public.mig_runner_b (id int);")
    return tmp_path


@pytest.mark.db
async def test_aplica_em_ordem_e_registra_checksum(
    conn: asyncpg.Connection, mig_dir: Path
) -> None:
    assert await migrate.apply_pending(conn, mig_dir, TABLE) == ["001_a", "002_b"]
    rows = await conn.fetch(f"SELECT version, checksum FROM {TABLE} ORDER BY version")
    assert [r["version"] for r in rows] == ["001_a", "002_b"]
    assert all(len(r["checksum"]) == 64 for r in rows)
    assert await conn.fetchval("SELECT to_regclass('public.mig_runner_b') IS NOT NULL")


@pytest.mark.db
async def test_idempotente(conn: asyncpg.Connection, mig_dir: Path) -> None:
    await migrate.apply_pending(conn, mig_dir, TABLE)
    assert await migrate.apply_pending(conn, mig_dir, TABLE) == []
    assert await conn.fetchval(f"SELECT count(*) FROM {TABLE}") == 2


@pytest.mark.db
async def test_recusa_checksum_alterado(conn: asyncpg.Connection, mig_dir: Path) -> None:
    await migrate.apply_pending(conn, mig_dir, TABLE)
    _write(mig_dir, "001_a.sql", "CREATE TABLE public.mig_runner_a (id int, extra int);")
    with pytest.raises(MigrationError, match="checksum"):
        await migrate.apply_pending(conn, mig_dir, TABLE)
    assert not await migrate.is_up_to_date(conn, mig_dir, TABLE)


@pytest.mark.db
async def test_falha_reverte_so_o_arquivo_com_erro(
    database_url: str, conn: asyncpg.Connection, mig_dir: Path
) -> None:
    _write(mig_dir, "002_b.sql", "CREATE TABLE public.mig_runner_b (id int); SELECT boom();")
    with pytest.raises(asyncpg.PostgresError):
        await migrate.apply_pending(conn, mig_dir, TABLE)
    assert await migrate.pending_versions(conn, mig_dir, TABLE) == ["002_b"]
    assert not await conn.fetchval("SELECT to_regclass('public.mig_runner_b') IS NOT NULL")
    # o lock consultivo foi liberado: outra conexao consegue pega-lo
    other = await asyncpg.connect(database_url)
    try:
        assert await other.fetchval("SELECT pg_try_advisory_lock($1)", migrate._LOCK_KEY)
    finally:
        await other.close()


@pytest.mark.db
async def test_pending_e_is_up_to_date(conn: asyncpg.Connection, mig_dir: Path) -> None:
    assert await migrate.is_up_to_date(conn, mig_dir, TABLE) is False  # tabela nem existe
    assert await migrate.pending_versions(conn, mig_dir, TABLE) == ["001_a", "002_b"]
    await migrate.apply_pending(conn, mig_dir, TABLE)
    assert await migrate.pending_versions(conn, mig_dir, TABLE) == []
    assert await migrate.is_up_to_date(conn, mig_dir, TABLE) is True


@pytest.mark.db
async def test_check_codigo_de_saida_quando_falta(
    database_url: str, conn: asyncpg.Connection, mig_dir: Path
) -> None:
    assert await migrate.pending_versions(conn, mig_dir, TABLE) == ["001_a", "002_b"]
    assert await migrate._run("check", database_url, mig_dir, TABLE) == migrate.EXIT_PENDING
    await migrate._run("apply", database_url, mig_dir, TABLE)
    assert await migrate._run("check", database_url, mig_dir, TABLE) == migrate.EXIT_OK


def test_recusa_banco_que_nao_termina_em_test() -> None:
    with pytest.raises(RuntimeError, match="_test"):
        assert_test_database("postgresql://jarvis:x@localhost:5432/jarvis")
    assert_test_database("postgresql://jarvis_test:x@localhost:5432/jarvis_test")
