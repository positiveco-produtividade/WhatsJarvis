"""Gate de admin, CSRF, escopo de marca e rotas de sistema (PN-T13; PN-1..PN-5)."""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import asyncpg
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.clock import FakeClock
from app.db.connection import create_pool
from app.panel.auth.iam import IamClient
from app.panel.auth.sessions import COOKIE_NAME
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    require_csrf,
)
from app.panel.routes import mount_panel
from app.panel.services.scope import Scope
from tests.db_support import _truncate_all
from tests.panel.fakes import IAM_BASE_URL, FakeIam

pytestmark = pytest.mark.db

ADMIN = "admin@example.test"
HOST = "jarvis.positiveco.com.br"
ORIGIN = f"https://{HOST}"
CSRF_SECRET = "csrf-secret-de-teste"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)

probe = APIRouter(route_class=PanelRoute)
plain = APIRouter()


@probe.get("/dashboard/_t/token")
async def _route_token(
    request: Request, admin: Annotated[Any, Depends(current_admin)]
) -> dict[str, str]:
    return {"token": csrf_token_for(request, admin), "email": admin.email}


@probe.post("/dashboard/_t/post", dependencies=[Depends(require_csrf)])
async def _post() -> dict[str, bool]:
    return {"ok": True}


@probe.get("/dashboard/_t/brand")
async def _brand(scope: Annotated[Scope, Depends(brand_scope)]) -> dict[str, str]:
    return {"brand": scope.cookie_value}


@probe.get("/api/_t/json")
async def _json(_: Annotated[Any, Depends(current_admin)]) -> dict[str, bool]:
    return {"ok": True}


@plain.get("/dashboard/_t/plain")
async def _plain(_: Annotated[Any, Depends(current_admin)]) -> dict[str, bool]:
    return {"ok": True}


def make_settings(dsn: str, **over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": dsn,
        "whatsapp_app_secret": "app-secret-xyz",
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": "access-token-xyz",
        "whatsapp_phone_number_id": "100000000000001",
        "panel_admin_emails": f"{ADMIN}, outro@example.test",
        "panel_csrf_secret": CSRF_SECRET,
        "panel_public_host": HOST,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def build_app(settings: Settings, iam: FakeIam, clock: FakeClock) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pool = await create_pool(settings.database_url.get_secret_value(), min_size=1, max_size=4)
        app.state.pool = pool
        try:
            yield
        finally:
            await pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.panel_iam = IamClient(iam.client(), clock, base_url=IAM_BASE_URL, cache_ttl_seconds=0)
    app.include_router(probe)
    app.include_router(plain)
    mount_panel(app)
    return app


async def _sql(dsn: str, sql: str, *args: Any, many: bool = False) -> Any:
    conn = await asyncpg.connect(dsn)
    try:
        if many:
            return await conn.fetch(sql, *args)
        return await conn.fetchval(sql, *args)
    finally:
        await conn.close()


def sql(dsn: str, query: str, *args: Any) -> Any:
    return asyncio.run(_sql(dsn, query, *args))


class Env:
    def __init__(self, dsn: str, iam: FakeIam, clock: FakeClock) -> None:
        self.dsn = dsn
        self.iam = iam
        self.clock = clock

    def client(self, **settings_over: Any) -> TestClient:
        app = build_app(make_settings(self.dsn, **settings_over), self.iam, self.clock)
        return TestClient(app, base_url=ORIGIN, follow_redirects=False)


@pytest.fixture
def env(migrated_database: str) -> Iterator[Env]:
    asyncio.run(_truncate_all(migrated_database))
    iam = FakeIam(email=ADMIN)
    yield Env(migrated_database, iam, FakeClock(NOW))


def login(client: TestClient, iam_cookie: str = "iam-valido") -> None:
    client.cookies.set("iam_session", iam_cookie, domain="jarvis.positiveco.com.br")


def post_headers(token: str, origin: str | None = ORIGIN) -> dict[str, str]:
    headers = {"X-CSRF-Token": token}
    if origin:
        headers["Origin"] = origin
    return headers


# ---- PN-1: login pelo IAM ----------------------------------------------------------------


def test_sem_sessao_html_redireciona_ao_iam_com_next(env: Env) -> None:
    with env.client() as c:
        r = c.get("/dashboard")
    assert r.status_code == 302
    url = urlparse(r.headers["location"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://accounts.positiveco.com.br/login"
    assert parse_qs(url.query)["next"] == [f"https://{HOST}/dashboard"]


def test_next_nunca_aponta_para_host_de_fora(env: Env) -> None:
    with env.client() as c:
        r = c.get("/dashboard", headers={"Host": "evil.example"})
    assert parse_qs(urlparse(r.headers["location"]).query)["next"] == [f"https://{HOST}/dashboard"]


def test_sem_sessao_json_da_401_e_htmx_pede_redirect(env: Env) -> None:
    with env.client() as c:
        r = c.get("/api/_t/json")
        assert r.status_code == 401 and r.json() == {"error": "unauthorized"}
        h = c.get("/dashboard/_t/token", headers={"HX-Request": "true"})
    assert h.status_code == 401
    assert h.headers["HX-Redirect"].startswith("https://accounts.positiveco.com.br/login?next=")


def test_iam_valido_cria_sessao_e_cookie_opaco(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get("/dashboard")
        assert r.status_code == 302 and r.headers["location"] == "/dashboard/tickets"
        cookie = r.headers["set-cookie"].lower()
        assert f"{COOKIE_NAME}=" in cookie
        assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie
        calls = env.iam.calls
        c.cookies.delete("iam_session", domain=HOST)
        again = c.get("/dashboard/_t/token")  # so com o cookie do painel
    assert again.status_code == 200 and again.json()["email"] == ADMIN
    assert env.iam.calls == calls
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions") == 1


def test_raiz_tambem_e_entrada(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get("/")
    assert r.status_code == 302 and r.headers["location"] == "/dashboard/tickets"


def test_iam_401_manda_para_o_login_sem_criar_sessao(env: Env) -> None:
    env.iam.mode = "unauthorized"
    with env.client() as c:
        login(c)
        r = c.get("/dashboard")
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions") == 0


def test_email_do_iam_vale_sem_diferenca_de_caixa(env: Env) -> None:
    env.iam.email = "ADMIN@Example.Test"
    with env.client() as c:
        login(c)
        assert c.get("/dashboard").status_code == 302
        assert c.get("/dashboard/_t/token").status_code == 200


# ---- PN-2: 403 sem dados ---------------------------------------------------------------


def test_nao_admin_recebe_403_sem_dados_e_sem_sessao(env: Env) -> None:
    env.iam.email = "intruso@example.test"
    with env.client() as c:
        login(c)
        r = c.get("/dashboard/_t/token")
    assert r.status_code == 403
    assert "Acesso negado" in r.text and "intruso" not in r.text and "csrf" not in r.text.lower()
    assert COOKIE_NAME not in r.headers.get("set-cookie", "")
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions") == 0


def test_forbidden_auditado_no_maximo_uma_vez_por_minuto_por_email(env: Env) -> None:
    env.iam.email = "intruso@example.test"
    count = "SELECT count(*) FROM ops.audit_log WHERE action = 'panel.forbidden'"
    with env.client() as c:
        login(c)
        for _ in range(3):
            assert c.get("/dashboard").status_code == 403
        assert sql(env.dsn, count) == 1
        env.clock.advance(seconds=61)
        assert c.get("/dashboard").status_code == 403
        env.iam.email = "outro-intruso@example.test"
        assert c.get("/dashboard").status_code == 403
    assert sql(env.dsn, count) == 3
    row = sql(env.dsn, "SELECT actor_type FROM ops.audit_log WHERE action = 'panel.forbidden'")
    assert row == "panel"


def test_admin_removido_da_lista_perde_o_acesso_da_sessao_viva(env: Env) -> None:
    with env.client() as c:
        login(c)
        assert c.get("/dashboard/_t/token").status_code == 200
    with env.client(panel_admin_emails="so-outro@example.test") as c2:
        c2.cookies.update({k: v for k, v in c.cookies.items()})
        assert c2.get("/dashboard/_t/token").status_code == 403


def test_rota_comum_sem_route_class_tambem_recusa_com_status_certo(env: Env) -> None:
    env.iam.email = "intruso@example.test"
    with env.client() as c:
        login(c)
        assert c.get("/dashboard/_t/plain").status_code == 403
    with env.client() as c2:
        r = c2.get("/dashboard/_t/plain")
    assert r.status_code == 302 and "accounts.positiveco.com.br" in r.headers["location"]


# ---- PN-3: IAM fora do ar -----------------------------------------------------------------


@pytest.mark.parametrize("mode", ["timeout", "server_error"])
def test_iam_fora_sem_sessao_mostra_pagina_503(env: Env, mode: Any) -> None:
    env.iam.mode = mode
    with env.client() as c:
        login(c)
        r = c.get("/dashboard")
    assert r.status_code == 503
    assert "Login corporativo indisponível" in r.text
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions") == 0


def test_iam_fora_nao_derruba_sessao_viva(env: Env) -> None:
    with env.client() as c:
        login(c)
        assert c.get("/dashboard").status_code == 302
        env.iam.mode = "timeout"
        env.clock.advance(minutes=10)  # passa da revalidacao de 5 min
        assert c.get("/dashboard/_t/token").status_code == 200


def test_iam_401_na_revalidacao_revoga_a_sessao(env: Env) -> None:
    with env.client() as c:
        login(c)
        assert c.get("/dashboard").status_code == 302
        env.iam.mode = "unauthorized"
        env.clock.advance(minutes=6)
        r = c.get("/dashboard/_t/token")
    assert r.status_code == 302
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions WHERE revoked_at IS NOT NULL") == 1


# ---- DEV_AUTH_EMAIL ---------------------------------------------------------------------


def test_dev_auth_entra_sem_iam_fora_de_producao(env: Env) -> None:
    with env.client(production=False, dev_auth_email=ADMIN) as c:
        r = c.get("/dashboard")
        assert r.status_code == 302 and r.headers["location"] == "/dashboard/tickets"
        assert c.get("/dashboard/_t/token").json()["email"] == ADMIN
    assert env.iam.calls == 0


def test_dev_auth_tambem_passa_pelo_filtro_de_admin(env: Env) -> None:
    with env.client(production=False, dev_auth_email="dev-fora@example.test") as c:
        assert c.get("/dashboard").status_code == 403


def test_dev_auth_e_recusado_em_producao() -> None:
    with pytest.raises(ValueError, match="DEV_AUTH_EMAIL"):
        make_settings("postgresql://u:p@localhost/db", production=True, dev_auth_email=ADMIN)


def test_producao_sem_cookie_do_iam_nao_entra_mesmo_com_dev_email_vazio(env: Env) -> None:
    with env.client(production=True) as c:
        assert c.get("/dashboard").status_code == 302
    assert env.iam.calls == 0


# ---- PN-4: CSRF -------------------------------------------------------------------------


def csrf_of(c: TestClient) -> str:
    login(c)
    return str(c.get("/dashboard/_t/token").json()["token"])


def test_post_sem_token_e_403(env: Env) -> None:
    with env.client() as c:
        csrf_of(c)
        r = c.post("/dashboard/_t/post", headers={"Origin": ORIGIN})
    assert r.status_code == 403 and "Acesso negado" in r.text


def test_post_com_token_e_origin_passa_por_cabecalho(env: Env) -> None:
    with env.client() as c:
        token = csrf_of(c)
        r = c.post("/dashboard/_t/post", headers=post_headers(token))
    assert r.status_code == 200 and r.json() == {"ok": True}


def test_post_com_campo_csrf_de_formulario_passa(env: Env) -> None:
    with env.client() as c:
        token = csrf_of(c)
        r = c.post("/dashboard/_t/post", data={"csrf": token}, headers={"Origin": ORIGIN})
        bad = c.post("/dashboard/_t/post", data={"csrf": token + "x"}, headers={"Origin": ORIGIN})
    assert r.status_code == 200 and bad.status_code == 403


def test_origin_ou_referer_de_fora_e_ausente_falham(env: Env) -> None:
    with env.client() as c:
        token = csrf_of(c)
        evil = c.post("/dashboard/_t/post", headers=post_headers(token, "https://evil.example"))
        none = c.post("/dashboard/_t/post", headers=post_headers(token, None))
        ref = c.post(
            "/dashboard/_t/post",
            headers={**post_headers(token, None), "Referer": f"{ORIGIN}/dashboard/faq"},
        )
    assert evil.status_code == 403 and none.status_code == 403
    assert ref.status_code == 200


def test_token_de_outra_sessao_nao_vale(env: Env) -> None:
    with env.client() as a:
        token_a = csrf_of(a)
    with env.client() as b:
        csrf_of(b)
        r = b.post("/dashboard/_t/post", headers=post_headers(token_a))
    assert r.status_code == 403


def test_post_sem_sessao_nao_chega_ao_csrf(env: Env) -> None:
    with env.client() as c:
        r = c.post("/dashboard/_t/post", headers={"Origin": ORIGIN, "X-CSRF-Token": "x"})
    assert r.status_code == 302


# ---- sair ------------------------------------------------------------------------------


def test_sair_exige_csrf_revoga_e_leva_ao_login_do_iam(env: Env) -> None:
    with env.client() as c:
        token = csrf_of(c)
        assert c.post("/dashboard/sair", headers={"Origin": ORIGIN}).status_code == 403
        r = c.post("/dashboard/sair", data={"csrf": token}, headers={"Origin": ORIGIN})
        assert r.status_code == 303
        assert r.headers["location"] == "https://accounts.positiveco.com.br/login"
        assert COOKIE_NAME in r.headers["set-cookie"]
        c.cookies.delete("iam_session", domain=HOST)
        after = c.get("/dashboard/_t/token")
    assert after.status_code == 302
    assert sql(env.dsn, "SELECT count(*) FROM ops.panel_sessions WHERE revoked_at IS NOT NULL") == 1


# ---- PN-5: marca ------------------------------------------------------------------------


def test_marca_grava_cookie_e_volta_pela_referer_do_painel(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(
            "/dashboard/marca?b=zaya", headers={"Referer": f"{ORIGIN}/dashboard/faq?page=2"}
        )
        assert r.status_code == 302 and r.headers["location"] == "/dashboard/faq?page=2"
        cookie = r.headers["set-cookie"].lower()
        assert "jarvis_brand=zaya" in cookie and "httponly" in cookie and "secure" in cookie
        assert "samesite=lax" in cookie and "max-age=31536000" in cookie
        assert c.get("/dashboard/_t/brand").json() == {"brand": "zaya"}


@pytest.mark.parametrize(
    "referer",
    [None, "https://evil.example/x", "https://evil.example@" + HOST + "/x", "javascript:1"],
)
def test_marca_ignora_referer_de_fora(env: Env, referer: str | None) -> None:
    headers = {"Referer": referer} if referer else {}
    with env.client() as c:
        login(c)
        r = c.get("/dashboard/marca?b=jungle", headers=headers)
    assert r.status_code == 302
    assert r.headers["location"] in ("/dashboard/tickets", "/x")  # userinfo no host e o do painel
    assert "evil" not in r.headers["location"]


def test_marca_invalida_vira_todas_e_cookie_invalido_tambem(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get("/dashboard/marca?b=inventada")
        assert "jarvis_brand=todas" in r.headers["set-cookie"].lower()
        c.cookies.set("jarvis_brand", "'; DROP TABLE x;--", domain=HOST)
        assert c.get("/dashboard/_t/brand").json() == {"brand": "todas"}


def test_marca_exige_admin(env: Env) -> None:
    with env.client() as c:
        r = c.get("/dashboard/marca?b=zaya")
    assert r.status_code == 302 and "accounts.positiveco.com.br" in r.headers["location"]
    assert "jarvis_brand" not in r.headers.get("set-cookie", "")


# ---- estado -----------------------------------------------------------------------------


def _seed_state(dsn: str) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
                "VALUES ('paused', 'true'::jsonb, $1, 'teste')", NOW,
            )
            for brand, ack in (("jungle", None), (None, None), ("zaya", None), ("jungle", NOW)):
                await conn.execute(
                    "INSERT INTO ops.alerts (severity, kind, message, payload, created_at, "
                    "acknowledged_at) VALUES ('warn', 'x', 'm', $1::jsonb, $2, $3)",
                    json.dumps({"brand": brand} if brand else {}), NOW, ack,
                )
        finally:
            await conn.close()

    asyncio.run(run())


def test_estado_mostra_pausa_e_alertas_nao_vistos(env: Env) -> None:
    _seed_state(env.dsn)
    with env.client() as c:
        login(c)
        r = c.get("/dashboard/estado")
    assert r.status_code == 200
    assert "Jarvis pausado" in r.text and "3 alertas não vistos" in r.text
    assert 'hx-trigger="every 30s"' in r.text and "<html" not in r.text


def test_estado_respeita_a_marca_em_foco(env: Env) -> None:
    _seed_state(env.dsn)
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "zaya", domain=HOST)
        r = c.get("/dashboard/estado")
    assert "2 alertas não vistos" in r.text  # zaya + sem marca


def test_estado_sem_nada_nao_mostra_faixas(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get("/dashboard/estado")
    assert "pausado" not in r.text and "não visto" not in r.text


def test_respostas_do_painel_levam_cabecalhos_de_seguranca(env: Env) -> None:
    with env.client() as c:
        r = c.get("/dashboard")
    assert r.headers["cache-control"] == "no-store, private"
    assert "script-src 'self'" in r.headers["content-security-policy"]
