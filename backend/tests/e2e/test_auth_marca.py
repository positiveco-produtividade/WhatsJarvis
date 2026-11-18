"""E2E: login pelo IAM e marca em foco (PN-T65; PN-1, PN-5).

O seletor de marca saiu do menu (pedido do Caio, 2026-10-27, ver `_nav.html`); a escolha
continua sendo `GET /dashboard/marca?b=<slug>`, que grava o cookie `jarvis_brand` e volta pela
`Referer`. O teste de PN-5 usa essa rota como um link e confere que o filtro persiste ao
navegar pelo menu e ao recarregar.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (E2E opcional)")

from playwright.sync_api import BrowserContext, Page, Request, expect  # noqa: E402
from playwright.sync_api import Error as PlaywrightError  # noqa: E402

from app.panel.auth.sessions import COOKIE_NAME  # noqa: E402
from tests.e2e.conftest import IAM_HOST, LiveServer, login  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.db]

MENU = (
    "Tickets",
    "Inbox",
    "Escalados",
    "FAQ",
    "Pessoas autorizadas",
    "Métricas",
    "Alertas",
    "Relatórios",
    "Logs",
    "Perguntas",
)
BRAND_COOKIE = "jarvis_brand"


def _cookie(context: BrowserContext, name: str) -> dict[str, object] | None:
    return next((dict(c) for c in context.cookies() if c["name"] == name), None)


# ---- PN-1 -------------------------------------------------------------------------------


def test_sem_sessao_vai_ao_login_do_iam(page: Page, live_server: LiveServer) -> None:
    # O IAM nao existe no teste (o Chromium so resolve localhost): o navegador segue o 302 do
    # painel ate o login do IAM e la para. O pedido registrado prova o destino.
    requests: list[Request] = []
    page.on("request", lambda r: requests.append(r))
    with pytest.raises(PlaywrightError, match="ERR_NAME_NOT_RESOLVED"):
        page.goto("/dashboard/escalados")
    iam = [r for r in requests if urlparse(r.url).hostname == IAM_HOST]
    assert len(iam) == 1 and iam[0].is_navigation_request()
    url = urlparse(iam[0].url)
    assert url.scheme == "https" and url.path == "/login"
    assert parse_qs(url.query)["next"] == ["https://localhost/dashboard/escalados"]
    origin = iam[0].redirected_from
    assert origin is not None and origin.url == live_server.url("/dashboard/escalados")
    response = origin.response()
    assert response is not None and response.status == 302
    assert _cookie(page.context, COOKIE_NAME) is None


def test_com_iam_ve_o_menu_e_sessao_httponly(
    context: BrowserContext, page: Page, live_server: LiveServer
) -> None:
    login(context)
    page.goto("/dashboard")
    expect(page).to_have_url(re.compile(r"/dashboard/tickets$"))
    menu = page.locator("aside nav")
    for label in MENU:
        expect(menu.get_by_role("link", name=label)).to_be_visible()
    expect(menu.get_by_role("link", name="Tickets")).to_have_attribute("aria-current", "page")

    session = _cookie(context, COOKIE_NAME)
    assert session is not None
    assert session["httpOnly"] is True and session["secure"] is True
    assert session["sameSite"] == "Lax"
    assert COOKIE_NAME not in page.evaluate("document.cookie")

    # Depois do login a sessao do painel basta: sem o cookie do IAM, segue dentro.
    calls = live_server.iam.calls
    context.clear_cookies(name="iam_session")
    menu.get_by_role("link", name="Alertas").click()
    expect(page).to_have_url(re.compile(r"/dashboard/alerts$"))
    expect(page.locator("main h1")).to_have_text("Alertas do sistema")
    assert live_server.iam.calls == calls


# ---- PN-5 -------------------------------------------------------------------------------


def _brand_cells(page: Page, label: str) -> int:
    return page.locator("table tbody").get_by_text(label, exact=True).count()


def test_marca_escolhida_persiste_entre_telas_e_ao_recarregar(
    admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    page.goto("/dashboard/tickets")
    expect(page.locator("table tbody tr").first).to_be_visible()
    assert _brand_cells(page, "Plant Power") > 0
    assert _brand_cells(page, "A Tal da Castanha") > 0

    # Escolhe Plant Power estando em Escalados: volta para Escalados, ja filtrado.
    page.goto(
        "/dashboard/marca?b=plant_power", referer=live_server.url("/dashboard/escalados")
    )
    expect(page).to_have_url(re.compile(r"/dashboard/escalados$"))
    cookie = _cookie(page.context, BRAND_COOKIE)
    assert cookie is not None and cookie["value"] == "plant_power"
    assert cookie["httpOnly"] is True
    board = page.locator("#escalados-board")
    expect(board.locator('[data-ticket="J-0005"]')).to_be_visible()  # escalado de Plant Power
    expect(board.locator('[data-ticket="J-0007"]')).to_have_count(0)  # escalado de A Tal

    # Outra tela pelo menu: a marca continua em foco.
    page.locator("aside nav").get_by_role("link", name="Tickets").click()
    expect(page).to_have_url(re.compile(r"/dashboard/tickets$"))
    expect(page.locator("table tbody tr").first).to_be_visible()
    assert _brand_cells(page, "Plant Power") > 0
    assert _brand_cells(page, "A Tal da Castanha") == 0

    page.reload()
    expect(page.locator("table tbody tr").first).to_be_visible()
    assert _brand_cells(page, "A Tal da Castanha") == 0
    assert _cookie(page.context, BRAND_COOKIE)["value"] == "plant_power"  # type: ignore[index]

    # Todas as marcas de novo.
    page.goto("/dashboard/marca?b=todas", referer=live_server.url("/dashboard/tickets"))
    expect(page).to_have_url(re.compile(r"/dashboard/tickets$"))
    expect(page.locator("table tbody tr").first).to_be_visible()
    assert _brand_cells(page, "A Tal da Castanha") > 0
