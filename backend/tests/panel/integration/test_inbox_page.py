"""Pagina Inbox, lista (PN-T21; PN-10, PN-9, PN-43) contra Postgres real e o app montado."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.clock import FakeClock
from app.db.connection import create_pool
from app.panel.auth.iam import IamClient
from app.panel.routes import mount_panel
from tests.db_support import _truncate_all
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B, PanelWorld
from tests.panel.fakes import IAM_BASE_URL, FakeIam

pytestmark = pytest.mark.db

ADMIN = "admin@example.test"
HOST = "jarvis.positiveco.com.br"
ORIGIN = f"https://{HOST}"
NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
FRAGMENT = {"HX-Request": "true"}


def make_settings(dsn: str) -> Settings:
    values: dict[str, Any] = {
        "database_url": dsn,
        "whatsapp_app_secret": "app-secret-xyz",
        "whatsapp_verify_token": "verify-token-xyz",
        "whatsapp_access_token": "access-token-xyz",
        "whatsapp_phone_number_id": "100000000000001",
        "panel_admin_emails": ADMIN,
        "panel_csrf_secret": "csrf-secret-de-teste",
        "panel_public_host": HOST,
    }
    return Settings(_env_file=None, **values)  # type: ignore[call-arg]


def build_app(dsn: str, iam: FakeIam, clock: FakeClock) -> FastAPI:
    settings = make_settings(dsn)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.pool = await create_pool(dsn, min_size=1, max_size=4)
        try:
            yield
        finally:
            await app.state.pool.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.panel_iam = IamClient(iam.client(), clock, base_url=IAM_BASE_URL, cache_ttl_seconds=0)
    mount_panel(app)
    return app


async def _seed(dsn: str) -> PanelWorld:
    await _truncate_all(dsn)
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


async def _add_trace(dsn: str, user_id: int, body: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, body) "
            "VALUES (gen_random_uuid()::text, $1, 'text', $2, 'allowed', 'allowed', $3)",
            NOW,
            user_id,
            body,
        )
    finally:
        await conn.close()


@pytest.fixture
def dsn(migrated_database: str) -> str:
    return migrated_database


@pytest.fixture
def world(dsn: str) -> PanelWorld:
    return asyncio.run(_seed(dsn))


@pytest.fixture
def client(dsn: str, world: PanelWorld) -> Iterator[TestClient]:
    app = build_app(dsn, FakeIam(email=ADMIN), FakeClock(NOW))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        c.cookies.set("iam_session", "iam-valido", domain=HOST)
        yield c


def row_names(html: str) -> list[str]:
    return re.findall(r'class="text-sm font-medium text-ink-800 truncate">([^<]*)<', html)


# ---- acesso ------------------------------------------------------------------------------


def test_sem_sessao_redireciona_ao_login(dsn: str, world: PanelWorld) -> None:
    app = build_app(dsn, FakeIam(email=ADMIN), FakeClock(NOW))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        r = c.get("/dashboard/inbox")
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]


def test_nao_admin_recebe_403_sem_dados(dsn: str, world: PanelWorld) -> None:
    app = build_app(dsn, FakeIam(email="intruso@example.test"), FakeClock(NOW))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        c.cookies.set("iam_session", "iam-valido", domain=HOST)
        r = c.get("/dashboard/inbox")
    assert r.status_code == 403
    assert "Vendedor" not in r.text


# ---- PN-10: lista ------------------------------------------------------------------------


def test_pagina_completa_com_abas_busca_e_lista(client: TestClient, world: PanelWorld) -> None:
    r = client.get("/dashboard/inbox")
    assert r.status_code == 200
    assert "no-store" in r.headers["cache-control"]
    html = r.text
    assert "<html" in html and 'id="inbox-lista"' in html and 'id="chat-pane"' in html
    assert sorted(row_names(html)) == ["Vendedor Duas Marcas", "Vendedor Uma Marca"]
    for needle in ("Conversas", "Mídias", "Buscar por nome ou telefone", "Só aguardando (2)"):
        assert needle in html
    assert 'aria-current="page"' in html and "📥 Inbox" in html
    assert "inbox.js" in html and 'x-data="inboxList"' in html


def test_linha_mostra_classificacao_e_tempo_de_espera(
    client: TestClient, world: PanelWorld
) -> None:
    html = client.get("/dashboard/inbox").text
    assert "⏳ 3 h" in html  # J-0003, aguardando da Uma Marca, criado 3 h antes
    assert "Produto" in html and "Preço" in html  # chips de classificacao
    assert '<input type="checkbox" name="user_ids"' in html  # so quem aguarda ganha caixa


def test_telefone_nao_aparece_na_lista(client: TestClient, world: PanelWorld) -> None:
    html = client.get("/dashboard/inbox").text
    assert "55119000" not in html and "+55 11" not in html


def test_linha_aponta_para_o_chat(
    client: TestClient, world: PanelWorld
) -> None:
    uid = world.sellers.one_brand
    html = client.get("/dashboard/inbox").text
    assert f'hx-get="/dashboard/inbox/{uid}"' in html and 'hx-target="#chat-pane"' in html
    assert f"u={uid}" in html
    assert 'hx-post="/dashboard/inbox/concluir-lote"' in html
    assert client.get(f"/dashboard/inbox/{uid}").status_code == 200


def test_aba_midias_lista_so_quem_tem_ticket_de_midia(
    client: TestClient, world: PanelWorld
) -> None:
    html = client.get("/dashboard/inbox?aba=midias").text
    assert row_names(html) == ["Vendedor So Midia"]
    assert "Só aguardando (1)" in html


def test_aba_invalida_vira_conversas(client: TestClient, world: PanelWorld) -> None:
    assert len(row_names(client.get("/dashboard/inbox?aba=xyz").text)) == 2


def test_busca_por_nome_sem_acento_e_caixa(client: TestClient, world: PanelWorld) -> None:
    html = client.get("/dashboard/inbox?q=DUAS marcas").text
    assert row_names(html) == ["Vendedor Duas Marcas"]
    assert 'value="DUAS marcas"' in html


def test_so_aguardando_ativo_mostra_link_para_todas(
    client: TestClient, world: PanelWorld
) -> None:
    html = client.get("/dashboard/inbox?so_aguardando=1").text
    assert "← todas" in html and 'name="so_aguardando" value="1"' in html
    assert sorted(row_names(html)) == ["Vendedor Duas Marcas", "Vendedor Uma Marca"]


def test_marca_em_foco_escopa_a_lista(client: TestClient, world: PanelWorld) -> None:
    client.cookies.set("jarvis_brand", BRAND_B)
    assert row_names(client.get("/dashboard/inbox").text) == ["Vendedor Duas Marcas"]
    client.cookies.set("jarvis_brand", "jungle")
    assert row_names(client.get("/dashboard/inbox").text) == []


def test_ultima_mensagem_e_escapada(client: TestClient, world: PanelWorld, dsn: str) -> None:
    asyncio.run(_add_trace(dsn, world.sellers.one_brand, "<script>alert(1)</script> oi"))
    html = client.get("/dashboard/inbox").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


# ---- HTMX: fragmento x pagina ------------------------------------------------------------


def test_htmx_devolve_so_a_lista(client: TestClient, world: PanelWorld) -> None:
    r = client.get("/dashboard/inbox?aba=midias", headers=FRAGMENT)
    assert r.status_code == 200
    assert "<html" not in r.text and 'id="chat-pane"' not in r.text
    assert row_names(r.text) == ["Vendedor So Midia"]


def test_restauracao_de_historico_do_htmx_recebe_a_pagina_inteira(
    client: TestClient, world: PanelWorld
) -> None:
    r = client.get("/dashboard/inbox", headers={**FRAGMENT, "HX-History-Restore-Request": "true"})
    assert "<html" in r.text


def test_u_abre_o_chat_ao_carregar(client: TestClient, world: PanelWorld) -> None:
    uid = world.sellers.two_brands
    html = client.get(f"/dashboard/inbox?u={uid}").text
    assert f'hx-get="/dashboard/inbox/{uid}" hx-trigger="load"' in html
    assert 'aria-current="true"' in html  # linha selecionada
    plain = client.get("/dashboard/inbox").text
    assert 'hx-trigger="load"' not in plain


def test_parametros_lixo_nao_quebram(client: TestClient, world: PanelWorld) -> None:
    r = client.get("/dashboard/inbox?page=abc&u=zz&so_aguardando=talvez&q=" + "x" * 500)
    assert r.status_code == 200
    assert client.get("/dashboard/inbox?page=-4&u=0").status_code == 200


# ---- PN-43: vazio ------------------------------------------------------------------------


def test_lista_vazia_mostra_nada_aguardando_aqui(
    client: TestClient, world: PanelWorld, dsn: str
) -> None:
    html = client.get("/dashboard/inbox?q=nao-existe-ninguem").text
    assert "Nada aguardando aqui." in html
    assert "0 conversas" in html and "Página" not in html


def test_banco_sem_conversas_mostra_estado_vazio(dsn: str) -> None:
    asyncio.run(_truncate_all(dsn))
    app = build_app(dsn, FakeIam(email=ADMIN), FakeClock(NOW))
    with TestClient(app, base_url=ORIGIN, follow_redirects=False) as c:
        c.cookies.set("iam_session", "iam-valido", domain=HOST)
        r = c.get("/dashboard/inbox")
        m = c.get("/dashboard/inbox?aba=midias&so_aguardando=1")
    assert r.status_code == 200 and "Nada aguardando aqui." in r.text
    assert m.status_code == 200 and "Nada aguardando aqui." in m.text


def test_paginacao_aparece_alem_de_uma_pagina(
    client: TestClient, world: PanelWorld, dsn: str
) -> None:
    async def many() -> None:
        conn = await asyncpg.connect(dsn)
        try:
            for i in range(60):
                await factories.insert_user(
                    conn, name=f"Extra {i:02d}", seq=100 + i, brands=[BRAND_A], now=NOW
                )
        finally:
            await conn.close()

    asyncio.run(many())
    html = client.get("/dashboard/inbox").text
    assert "Página 1 de 2" in html
    page2 = client.get("/dashboard/inbox?page=2", headers=FRAGMENT).text
    assert "Página 2 de 2" in page2 and "aba=conversas" in page2
