"""Casca da aplicacao e GET /health (spec 4.3.1 e 5): banco, migrations, cabecalhos, erros."""
from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from tests.db_support import MIGRATIONS_DIR

BASE = "http://localhost"


def make_settings(dsn: str = "postgresql://u:p@localhost/db") -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=dsn,  # type: ignore[arg-type]
        whatsapp_app_secret="app-secret-xyz",  # type: ignore[arg-type]
        whatsapp_verify_token="verify-token-xyz",  # type: ignore[arg-type]
        whatsapp_access_token="access-token-xyz",  # type: ignore[arg-type]
        whatsapp_phone_number_id="100000000000001",
    )


class _BrokenAcquire:
    async def __aenter__(self) -> Any:
        raise ConnectionRefusedError("postgresql://jarvis:senha-secreta@db/jarvis")

    async def __aexit__(self, *exc: object) -> None:
        return None


class BrokenPool:
    """Pool cujo banco esta fora do ar."""

    def acquire(self) -> _BrokenAcquire:
        return _BrokenAcquire()


@pytest.fixture
def app() -> FastAPI:
    return main.create_app(make_settings())


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    # Sem `with`: a lifespan (que abre o pool) nao roda nestes testes sem banco.
    yield TestClient(app, base_url=BASE, raise_server_exceptions=False)


def test_health_503_quando_pool_nao_existe(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded"}


def test_health_503_quando_banco_indisponivel(
    app: FastAPI, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    app.state.pool = BrokenPool()
    with caplog.at_level("WARNING"):
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded"}
    assert "senha-secreta" not in caplog.text  # so o tipo do erro vai para o log
    assert [r.error_type for r in caplog.records if hasattr(r, "error_type")] == [
        "ConnectionRefusedError"
    ]


def test_cabecalhos_de_seguranca_e_request_id(client: TestClient) -> None:
    first = client.get("/health")
    second = client.get("/health")
    assert first.headers["x-content-type-options"] == "nosniff"
    assert "max-age=" in first.headers["strict-transport-security"]
    assert first.headers["cache-control"] == "no-store"
    assert len(first.headers["x-request-id"]) == 32
    assert first.headers["x-request-id"] != second.headers["x-request-id"]


def test_request_id_do_cliente_nao_e_aceito(client: TestClient) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "forjado"})
    assert resp.headers["x-request-id"] != "forjado"


def test_docs_redoc_e_openapi_desligados(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_host_nao_confiavel_recusado_com_cabecalhos(app: FastAPI) -> None:
    resp = TestClient(app, base_url="http://evil.example.com").get("/health")
    assert resp.status_code == 400
    assert resp.headers["x-content-type-options"] == "nosniff"


def test_host_do_dominio_da_empresa_aceito(app: FastAPI) -> None:
    resp = TestClient(app, base_url="http://jarvis.positiveco.com.br").get("/health")
    assert resp.status_code == 503  # chegou ao handler (sem pool), nao foi barrado


def test_tunel_de_dev_so_fora_de_producao() -> None:
    """Endereco do tunel do VS Code (Meta -> webhook local) so com PRODUCTION=false."""
    tunnel = "http://abc123-8010.brs.devtunnels.ms"
    prod = main.create_app(make_settings())
    assert TestClient(prod, base_url=tunnel).get("/health").status_code == 400
    dev = main.create_app(make_settings().model_copy(update={"production": False}))
    assert TestClient(dev, base_url=tunnel).get("/health").status_code == 503


def test_erro_404_sem_detalhe(client: TestClient) -> None:
    resp = client.get("/nao-existe")
    assert resp.status_code == 404
    assert resp.json() == {"error": "http_error"}


def test_erro_interno_sem_traceback_nem_detalhe(app: FastAPI) -> None:
    async def boom() -> None:
        raise RuntimeError("detalhe-interno Traceback")

    app.add_api_route("/boom", boom, methods=["GET"])
    resp = TestClient(app, base_url=BASE, raise_server_exceptions=False).get("/boom")
    assert resp.status_code == 500
    assert resp.json() == {"error": "internal_error"}
    assert "detalhe-interno" not in resp.text
    assert resp.headers["cache-control"] == "no-store"
    assert "x-request-id" in resp.headers


def test_validacao_422_sem_eco_da_entrada(app: FastAPI) -> None:
    async def needs_int(n: int) -> dict[str, int]:
        return {"n": n}

    app.add_api_route("/n", needs_int, methods=["GET"])
    resp = TestClient(app, base_url=BASE).get("/n", params={"n": "valor-secreto"})
    assert resp.status_code == 422
    assert resp.json() == {"error": "invalid_request"}


def test_settings_secrets_registrados_para_mascara() -> None:
    from app.core.log_redaction import redact

    main.create_app(make_settings())
    assert "app-secret-xyz" not in redact("falha app-secret-xyz no log")
    assert "access-token-xyz" not in redact("token=access-token-xyz")


@pytest.mark.db
def test_health_200_com_banco_e_migrations_em_dia(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(main, "setup_logging", lambda level=0: None)
    app = main.create_app(make_settings(migrated_database))
    with TestClient(app, base_url=BASE) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
        pool = app.state.pool
    assert pool.is_closing()  # a lifespan fechou o pool


@pytest.mark.db
def test_health_503_com_migration_pendente(
    migrated_database: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    monkeypatch.setattr(main, "setup_logging", lambda level=0: None)
    shutil.copytree(MIGRATIONS_DIR, tmp_path, dirs_exist_ok=True)
    (tmp_path / "999_pendente.sql").write_text("SELECT 1;", encoding="utf-8")

    async def with_extra_dir(conn: Any) -> bool:
        return await real(conn, tmp_path)

    real = main.migrate.is_up_to_date
    monkeypatch.setattr(main.migrate, "is_up_to_date", with_extra_dir)
    app = main.create_app(make_settings(migrated_database))
    with TestClient(app, base_url=BASE) as client:
        resp = client.get("/health")
    assert resp.status_code == 503
    assert resp.json() == {"status": "degraded"}
