"""Fixtures de banco reutilizaveis (dono: PF-04). Outras tarefas so as importam.

integration/conftest.py e security/conftest.py fazem `from tests.db_support import *`.
Os testes `db` pulam sozinhos sem TEST_DATABASE_URL (tests/conftest.py).
Seguranca: recusa rodar se o banco da URL nao terminar em `_test`, porque cada
teste faz TRUNCATE.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from app.db import migrate
from app.db.connection import create_pool

__all__ = [
    "MIGRATIONS_DIR",
    "assert_test_database",
    "database_url",
    "migrated_database",
    "db_pool",
    "db_conn",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_PRESERVED = {("ops", "schema_migrations")}


def assert_test_database(dsn: str) -> None:
    """Levanta RuntimeError se o banco do DSN nao terminar em `_test`."""
    name = urlparse(dsn).path.lstrip("/")
    if not name.endswith("_test"):
        raise RuntimeError(
            "TEST_DATABASE_URL deve apontar para um banco que termina em '_test' "
            "(os testes fazem TRUNCATE); nunca o banco de desenvolvimento nem o de producao"
        )


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT schemaname, tablename FROM pg_tables "
            "WHERE schemaname IN ('access', 'chat', 'ops', 'content')"
        )
        names = [
            f'"{r["schemaname"]}"."{r["tablename"]}"'
            for r in rows
            if (r["schemaname"], r["tablename"]) not in _PRESERVED
        ]
        if names:
            await conn.execute(f"TRUNCATE {', '.join(names)} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()


async def _apply_real_migrations(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await migrate.apply_pending(conn, MIGRATIONS_DIR)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("defina TEST_DATABASE_URL; ver backend/DEV_SETUP.md")
    assert_test_database(dsn)
    return dsn


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    """Aplica as migrations reais uma vez por sessao; devolve o DSN."""
    asyncio.run(_apply_real_migrations(database_url))
    return database_url


@pytest.fixture
async def db_pool(migrated_database: str) -> AsyncIterator[asyncpg.Pool]:
    """Pool novo por teste, com as tabelas limpas antes de comecar."""
    await _truncate_all(migrated_database)
    pool = await create_pool(migrated_database, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def db_conn(db_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    async with db_pool.acquire() as conn:
        yield conn
