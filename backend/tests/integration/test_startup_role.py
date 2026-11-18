"""Subida recusada em producao com o papel dono (porta-fechada.2, PA-12, D2)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from urllib.parse import urlparse

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app import main
from app.db import roles
from tests.integration.test_health import BASE, make_settings

pytestmark = pytest.mark.db


@pytest.fixture
async def owner(database_url: str, migrated_database: str) -> AsyncIterator[asyncpg.Connection]:
    conn = await asyncpg.connect(database_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def quiet_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "setup_logging", lambda level=0: None)


async def test_jarvis_app_nao_tem_problema(db_conn: asyncpg.Connection) -> None:
    assert await roles.role_problems(db_conn) == ("jarvis_app", [])
    await roles.assert_app_role(db_conn)


async def test_dono_tem_os_motivos(owner: asyncpg.Connection) -> None:
    role, problems = await roles.role_problems(owner)
    assert role == await owner.fetchval("SELECT current_user::text")
    assert "dono do banco" in problems
    assert "dono de tabelas ou funcoes" in problems
    assert "pode criar objetos nos schemas do Jarvis" in problems
    assert "superusuario" not in problems  # a suite recusa superusuario


def test_producao_com_dono_nao_sobe(database_url: str, quiet_logs: None) -> None:
    cfg = make_settings(database_url)
    assert cfg.production
    with pytest.raises(RuntimeError) as exc:
        with TestClient(main.create_app(cfg), base_url=BASE):
            pass
    message = str(exc.value)
    parts = urlparse(database_url)
    assert repr(parts.username) in message and "dono do banco" in message
    assert parts.password and parts.password not in message
    assert database_url not in message


def test_producao_com_jarvis_app_sobe(migrated_database: str, quiet_logs: None) -> None:
    with TestClient(main.create_app(make_settings(migrated_database)), base_url=BASE) as client:
        assert client.get("/health").status_code == 200


def test_fora_de_producao_o_dono_sobe(database_url: str, quiet_logs: None) -> None:
    cfg = make_settings(database_url).model_copy(update={"production": False})
    with TestClient(main.create_app(cfg), base_url=BASE) as client:
        assert client.get("/health").status_code == 200
