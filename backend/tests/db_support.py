"""Fixtures de banco reutilizaveis (dono: PF-04). Outras tarefas so as importam.

integration/conftest.py e security/conftest.py fazem `from tests.db_support import *`.
Os testes `db` pulam sozinhos sem TEST_DATABASE_URL (tests/conftest.py).
Seguranca: recusa rodar se o banco da URL nao terminar em `_test`, porque cada
teste faz TRUNCATE.

Papeis (porta-fechada.2, PA-13, D3): `database_url` e o DSN do dono (migra, TRUNCATE, testes
de migration e de gatilho). `migrated_database` devolve o mesmo DSN com o papel trocado na
conexao para `jarvis_app` (parametro de partida `role`), entao `db_pool`, os `Settings` dos
testes e o `create_app` rodam o app com as permissoes de producao.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import asyncpg
import pytest

from app.db import migrate
from app.db.connection import create_pool

__all__ = [
    "MIGRATIONS_DIR",
    "APP_ROLE",
    "app_role_dsn",
    "owner_dsn",
    "assert_test_database",
    "database_url",
    "migrated_database",
    "db_pool",
    "db_conn",
    "owner_conn",
    "owner_execute",
    "assistant_dsn",
    "assistant_pool",
]

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
_PRESERVED = {("ops", "schema_migrations")}
APP_ROLE = "jarvis_app"
_ROLE_MISSING = (
    "papel jarvis_app ausente ou sem GRANT para o papel de teste: rode "
    "backend/scripts/dev_db_setup.sql como postgres (backend/DEV_SETUP.md, porta-fechada.2)"
)


def owner_dsn(dsn: str) -> str:
    """Tira a troca de papel do DSN (volta ao dono)."""
    parts = urlparse(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "role"]
    return urlunparse(parts._replace(query=urlencode(query)))


def app_role_dsn(dsn: str, role: str = APP_ROLE) -> str:
    """O mesmo DSN, com a conexao assumindo `role` (o asyncpg manda como server_settings)."""
    parts = urlparse(dsn)
    query = [(k, v) for k, v in parse_qsl(parts.query) if k != "role"] + [("role", role)]
    return urlunparse(parts._replace(query=urlencode(query)))


def assert_test_database(dsn: str) -> None:
    """Levanta RuntimeError se o banco do DSN nao terminar em `_test`."""
    name = urlparse(dsn).path.lstrip("/")
    if not name.endswith("_test"):
        raise RuntimeError(
            "TEST_DATABASE_URL deve apontar para um banco que termina em '_test' "
            "(os testes fazem TRUNCATE); nunca o banco de desenvolvimento nem o de producao"
        )


async def _truncate_all(dsn: str) -> None:
    conn = await asyncpg.connect(owner_dsn(dsn))  # o app (jarvis_app) nao trunca (PA-7)
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
        member = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1) "
            "AND pg_has_role(session_user, $1, 'SET')",
            APP_ROLE,
        )
        if not member:
            raise RuntimeError(_ROLE_MISSING)
        await migrate.apply_pending(conn, MIGRATIONS_DIR)  # reaplica as permissoes (402)
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def database_url() -> str:
    dsn = os.environ.get("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("defina TEST_DATABASE_URL; ver backend/DEV_SETUP.md")
    assert_test_database(dsn)
    return owner_dsn(dsn)  # o conftest raiz pos o papel jarvis_app na variavel


@pytest.fixture(scope="session")
def migrated_database(database_url: str) -> str:
    """Aplica as migrations reais uma vez por sessao (como dono); devolve o DSN do app,
    que conecta como `jarvis_app`."""
    asyncio.run(_apply_real_migrations(database_url))
    return app_role_dsn(database_url)


@pytest.fixture
async def db_pool(migrated_database: str) -> AsyncIterator[asyncpg.Pool]:
    """Pool novo por teste, com as tabelas limpas antes de comecar."""
    await _truncate_all(owner_dsn(migrated_database))
    pool = await create_pool(migrated_database, min_size=1, max_size=5)
    try:
        yield pool
    finally:
        await pool.close()


async def owner_execute(sql: str, *args: object) -> None:
    """Roda `sql` como dono do banco de teste (TRUNCATE, setval, DDL de teste)."""
    dsn = os.environ["TEST_DATABASE_URL"]
    assert_test_database(dsn)
    conn = await asyncpg.connect(owner_dsn(dsn))
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


def assistant_dsn(dsn: str) -> str:
    """DSN como jarvis_assistant (papel das ferramentas do assistente em producao)."""
    return app_role_dsn(owner_dsn(dsn), "jarvis_assistant")


@pytest.fixture
async def assistant_pool(migrated_database: str) -> AsyncIterator[asyncpg.Pool]:
    """Pool das ferramentas do assistente, como em producao (ASSISTANT_DATABASE_URL)."""
    pool = await create_pool(assistant_dsn(migrated_database), min_size=1, max_size=3)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def owner_conn(migrated_database: str) -> AsyncIterator[asyncpg.Connection]:
    """Conexao como dono, para o que o app nao pode (migration de novo, gatilho, SET ROLE)."""
    conn = await asyncpg.connect(owner_dsn(migrated_database))
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def db_conn(db_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Connection]:
    async with db_pool.acquire() as conn:
        yield conn
