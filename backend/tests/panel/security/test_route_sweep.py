"""Varredura viva de rotas do painel (PN-T14; PN-2, PN-4).

Enumera `app.routes` do app real (`create_app`, painel ligado) e confere TODA rota fora da
lista de publicas: sem sessao (302 / 401 JSON / 401+HX-Redirect), nao-admin (403 sem dado) e
admin sem CSRF em todo metodo nao seguro (403). Rotas novas entram sozinhas.
"""
from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.clock import FakeClock
from app.panel.auth.iam import IamClient
from tests.fakes import FakeChannel
from tests.panel.fakes import IAM_BASE_URL, FakeIam

pytestmark = [pytest.mark.security, pytest.mark.db]

ADMIN = "admin@example.test"
INTRUDER = "intruso@example.test"
HOST = "jarvis.positiveco.com.br"
ORIGIN = f"https://{HOST}"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})

# Unicas rotas publicas por desenho (qualquer outra sem gate e um buraco):
#  - /health: sonda de saude do servidor (sem dado);
#  - /webhook/whatsapp: webhook da Meta, autenticado por assinatura HMAC (PF), nao por sessao.
# `/static` e um Mount (nao um APIRoute) e serve so arquivos publicos do front; fica fora da
# varredura por nao ser rota de aplicacao.
PUBLIC_PATHS = frozenset({"/health"})
PUBLIC_PREFIXES = ("/webhook",)


def is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)


def make_settings(dsn: str) -> Settings:
    base: dict[str, Any] = {
        "database_url": dsn,
        "whatsapp_app_secret": "app-secret-xyz",
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": "access-token-xyz",
        "whatsapp_phone_number_id": "100000000000001",
        "panel_enabled": True,
        "panel_admin_emails": ADMIN,
        "panel_csrf_secret": "csrf-secret-de-teste",
        "panel_public_host": HOST,
    }
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def build_app(dsn: str, iam: FakeIam) -> FastAPI:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    app = main.create_app(make_settings(dsn), channel=FakeChannel(), http_client=http)
    app.state.panel_iam = IamClient(
        iam.client(), FakeClock(), base_url=IAM_BASE_URL, cache_ttl_seconds=0
    )
    return app


def _sample(param: Any) -> str:
    kind = getattr(param, "type_", str)
    if kind is uuid.UUID:
        return str(uuid.UUID(int=1))
    return "1" if kind is int else "x1"


def concrete_path(route: APIRoute) -> str:
    """Troca `{param}` por um valor do tipo certo (a autorizacao roda antes da validacao)."""
    values = {p.name: _sample(p) for p in route.dependant.path_params}
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: values.get(m.group(1), "1"), route.path)


def panel_targets(app: FastAPI) -> list[tuple[str, str]]:
    """(metodo, caminho concreto) de toda rota de aplicacao que nao e publica."""
    out: list[tuple[str, str]] = []
    for route in app.routes:
        if isinstance(route, APIRoute) and not is_public(route.path):
            for method in sorted(route.methods - IGNORED_METHODS):
                out.append((method, concrete_path(route)))
    return sorted(set(out))


@pytest.fixture
def iam() -> FakeIam:
    return FakeIam(email=ADMIN)


@pytest.fixture
def app(migrated_database: str, iam: FakeIam) -> Iterator[FastAPI]:
    from tests.db_support import _truncate_all

    asyncio.run(_truncate_all(migrated_database))
    yield build_app(migrated_database, iam)


def client_for(app: FastAPI, *, iam_cookie: bool) -> TestClient:
    c = TestClient(
        app, base_url=ORIGIN, follow_redirects=False, raise_server_exceptions=False
    )
    if iam_cookie:
        c.cookies.set("iam_session", "iam-valido", domain=HOST)
    return c


def test_varredura_enxerga_rotas_do_painel(app: FastAPI) -> None:
    targets = panel_targets(app)
    paths = {p for _, p in targets}
    assert "/dashboard" in paths and len(targets) >= 10  # o painel esta montado
    assert any(m != "GET" for m, _ in targets)


def test_rotas_publicas_sao_so_as_da_lista(app: FastAPI) -> None:
    public = {r.path for r in app.routes if isinstance(r, APIRoute) and is_public(r.path)}
    assert "/health" in public
    assert all(p == "/health" or p.startswith("/webhook") for p in public)


def test_sem_sessao_toda_rota_recusa(app: FastAPI) -> None:
    bad: list[str] = []
    with client_for(app, iam_cookie=False) as c:
        for method, path in panel_targets(app):
            r = c.request(method, path, headers={"Origin": ORIGIN})
            expected = 401 if path.startswith("/api/") else 302
            if r.status_code != expected:
                bad.append(f"{method} {path} -> {r.status_code} (esperado {expected})")
            elif expected == 302 and "accounts.positiveco.com.br/login" not in r.headers.get(
                "location", ""
            ):
                bad.append(f"{method} {path} -> 302 sem ir ao login do IAM")
            htmx = c.request(method, path, headers={"Origin": ORIGIN, "HX-Request": "true"})
            if htmx.status_code != 401 or ("hx-redirect" not in htmx.headers) == (
                not path.startswith("/api/")
            ):
                bad.append(f"{method} {path} HTMX -> {htmx.status_code}")
    assert not bad, "rotas sem gate de sessao:\n" + "\n".join(bad)


def test_nao_admin_recebe_403_em_toda_rota(app: FastAPI, iam: FakeIam) -> None:
    iam.email = INTRUDER
    bad: list[str] = []
    with client_for(app, iam_cookie=True) as c:
        for method, path in panel_targets(app):
            r = c.request(method, path, headers={"Origin": ORIGIN})
            if r.status_code != 403:
                bad.append(f"{method} {path} -> {r.status_code}")
            elif "set-cookie" in r.headers and "jarvis_panel" in r.headers["set-cookie"]:
                bad.append(f"{method} {path} -> 403 mas criou sessao")
    assert not bad, "rotas acessiveis a nao-admin:\n" + "\n".join(bad)


def test_admin_sem_csrf_recebe_403_em_todo_metodo_nao_seguro(app: FastAPI) -> None:
    unsafe = [(m, p) for m, p in panel_targets(app) if m not in SAFE_METHODS]
    assert unsafe
    bad: list[str] = []
    with client_for(app, iam_cookie=True) as c:
        for method, path in unsafe:
            semtoken = c.request(method, path, headers={"Origin": ORIGIN})
            errado = c.request(
                method, path, headers={"Origin": ORIGIN, "X-CSRF-Token": "token-falso"}
            )
            fora = c.request(method, path, headers={"Origin": "https://evil.example"})
            for label, r in (("sem token", semtoken), ("token falso", errado), ("origin", fora)):
                if r.status_code != 403:
                    bad.append(f"{method} {path} [{label}] -> {r.status_code}")
    assert not bad, "rotas nao-GET sem protecao de CSRF:\n" + "\n".join(bad)
