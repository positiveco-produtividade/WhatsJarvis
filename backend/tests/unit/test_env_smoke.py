import os
import sys

import asyncpg
import pytest


def test_pytest_roda_no_venv() -> None:
    assert sys.version_info >= (3, 12)


@pytest.mark.db
async def test_conexao_sem_superusuario() -> None:
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        assert await conn.fetchval("SELECT 1") == 1
        assert await conn.fetchval("SHOW server_version_num") is not None
        is_super = await conn.fetchval(
            "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"
        )
        assert is_super is False, "a aplicacao nunca deve usar superusuario"
    finally:
        await conn.close()
