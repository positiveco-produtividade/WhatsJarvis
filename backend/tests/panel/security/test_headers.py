"""Cabecalhos e cache do painel (PN-T06; spec 4.4.4)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.panel.headers import PANEL_CSP, cache_control_for, is_panel_path

pytestmark = pytest.mark.security

BASE = "http://localhost"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": "postgresql://u:p@localhost/db",
        "whatsapp_app_secret": "app-secret-xyz",
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": "access-token-xyz",
        "whatsapp_phone_number_id": "100000000000001",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


@pytest.fixture
def client() -> TestClient:
    app = main.create_app(make_settings(panel_enabled=True))

    async def page() -> HTMLResponse:
        return HTMLResponse("<p>oi</p>")

    async def data() -> JSONResponse:
        return JSONResponse({"ok": True})

    async def boom() -> None:
        raise RuntimeError("x")

    app.add_api_route("/dashboard/teste", page, methods=["GET"])
    app.add_api_route("/api/teste", data, methods=["GET"])
    app.add_api_route("/dashboard/boom", boom, methods=["GET"])
    return TestClient(app, base_url=BASE, raise_server_exceptions=False)


def test_csp_so_permite_scripts_proprios() -> None:
    script = next(d for d in PANEL_CSP.split(";") if d.strip().startswith("script-src"))
    assert script.strip() == "script-src 'self'"
    assert "http" not in PANEL_CSP  # sem CDN
    assert "unsafe-eval" not in PANEL_CSP
    assert "frame-ancestors 'none'" in PANEL_CSP


@pytest.mark.parametrize("path", ["/dashboard/teste", "/api/teste", "/dashboard/nao-existe"])
def test_html_json_e_404_do_painel_sao_no_store_private(client: TestClient, path: str) -> None:
    resp = client.get(path)
    assert resp.headers["cache-control"] == "no-store, private"
    assert resp.headers["content-security-policy"] == PANEL_CSP
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in resp.headers["permissions-policy"]
    assert len(resp.headers["x-request-id"]) == 32  # cabecalhos de PF preservados
    assert "max-age=" in resp.headers["strict-transport-security"]


def test_erro_500_do_painel_tambem_leva_os_cabecalhos(client: TestClient) -> None:
    resp = client.get("/dashboard/boom")
    assert resp.status_code == 500
    assert resp.headers["cache-control"] == "no-store, private"
    assert resp.headers["content-security-policy"] == PANEL_CSP


def test_estatico_versionado_cacheia_e_sem_versao_revalida(client: TestClient) -> None:
    versioned = client.get("/static/vendor/htmx.min.js?v=123")
    assert versioned.status_code == 200
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert versioned.headers["content-security-policy"] == PANEL_CSP
    plain = client.get("/static/vendor/htmx.min.js")
    assert plain.headers["cache-control"] == "no-cache"


def test_health_e_webhook_nao_recebem_cabecalhos_do_painel(client: TestClient) -> None:
    health = client.get("/health")
    assert health.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in health.headers
    hook = client.get("/webhook/whatsapp")
    assert "content-security-policy" not in hook.headers
    assert hook.headers["cache-control"] == "no-store"
    other = client.get("/rota-de-pf-qualquer")
    assert other.headers["cache-control"] == "no-store"
    assert "content-security-policy" not in other.headers


def test_painel_desligado_nao_adiciona_cabecalhos() -> None:
    off = TestClient(main.create_app(make_settings(panel_enabled=False)), base_url=BASE)
    assert "content-security-policy" not in off.get("/health").headers
    assert "content-security-policy" not in off.get("/nada").headers


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", True),
        ("/dashboard", True),
        ("/dashboard/tickets", True),
        ("/api/assistant/chat", True),
        ("/static/x.js", True),
        ("/health", False),
        ("/webhook/whatsapp", False),
        ("/boom", False),
    ],
)
def test_is_panel_path(path: str, expected: bool) -> None:
    assert is_panel_path(path) is expected


def test_cache_control_for() -> None:
    assert cache_control_for("/dashboard/x", b"v=1") == "no-store, private"
    assert cache_control_for("/static/a.js", b"v=1") == "public, max-age=31536000, immutable"
    assert cache_control_for("/static/a.js", b"") == "no-cache"
