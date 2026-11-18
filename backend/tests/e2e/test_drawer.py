"""E2E: drawer lateral do ticket (PN-T65; PN-14, PN-15).

PN-15 dizia "nao existe 'Responder ao vendedor'"; a story retorno-humano.1 (RH-6) trocou a regra:
ticket aguardando agora mostra a caixa "Responder ao vendedor" no drawer. O teste confere o
comportamento atual.
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (E2E opcional)")

from playwright.sync_api import Locator, Page, expect  # noqa: E402

from tests.e2e.conftest import LiveServer  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.db]

AWAITING = "J-0005"  # Plant Power, aguardando, escalado (vendedor de duas marcas)
ANSWERED_BOT = "J-0006"  # Plant Power, respondido pelo bot (mesmo vendedor)


def _open_drawer(page: Page) -> Locator:
    drawer = page.locator("#drawer")
    expect(drawer).to_be_visible()
    return drawer.locator("#drawer-body")


def _assert_awaiting_drawer(body: Locator) -> None:
    expect(body.locator(f'[data-ticket="{AWAITING}"]')).to_be_visible()
    expect(body.locator(".font-mono.text-2xl")).to_have_text(AWAITING)
    expect(body.get_by_text("Aguardando atendimento", exact=True)).to_be_visible()
    expect(body.get_by_text(f"Pergunta de teste {AWAITING}", exact=True).first).to_be_visible()
    expect(body.get_by_text("Vendedor Duas Marcas", exact=True)).to_be_visible()
    # retorno-humano.1 (RH-6): ticket aguardando tem a caixa de resposta ao vendedor.
    box = body.locator("[data-reply-box]")
    expect(box.get_by_role("heading", name="Responder ao vendedor")).to_be_visible()
    expect(box.locator("textarea#drawer-reply")).to_be_editable()
    expect(box.get_by_role("button", name="Enviar e concluir")).to_be_visible()
    expect(body.get_by_role("button", name="Concluir", exact=True)).to_be_visible()


def test_tickets_recentes_do_inbox_abrem_o_drawer(
    admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    seller = live_server.world.sellers.two_brands
    page.goto(f"/dashboard/inbox?u={seller}")
    recent = page.locator("#chat-pane").get_by_role("button").filter(has_text=AWAITING)
    expect(recent).to_be_visible()
    expect(page.locator("#drawer")).to_be_hidden()

    recent.click()
    body = _open_drawer(page)
    _assert_awaiting_drawer(body)

    # Fecha no X e abre outro ticket da mesma conversa: sem caixa de resposta.
    body.get_by_role("button", name="Fechar detalhe do ticket").click()
    expect(page.locator("#drawer")).to_be_hidden()
    page.locator("#chat-pane").get_by_role("button").filter(has_text=ANSWERED_BOT).click()
    body = _open_drawer(page)
    expect(body.locator(f'[data-ticket="{ANSWERED_BOT}"]')).to_be_visible()
    expect(body.get_by_text("Respondido pelo bot", exact=True)).to_be_visible()
    expect(body.get_by_text("Enviado automaticamente pelo Jarvis")).to_be_visible()
    expect(body.locator("[data-reply-box]")).to_have_count(0)


# Bug do app achado por estes E2E (nao corrigido aqui; so testes): no htmx 1.9 o `hx-select`
# e herdado. Em Escalados (#escalados-board) e em Alertas (#alerts-area) a secao tem
# `hx-select="#escalados-board"` / `"#alerts-area"`, entao o botao que abre o ticket herda esse
# seletor, a resposta do drawer (que nao tem o id) vira vazia e o drawer nao abre. A correcao
# provavel e `hx-select="unset"` nesses botoes (como `#control-painel` ja faz em alerts.html).
# `strict=True`: quando o app for corrigido, o xfail vira falha e lembra de tirar a marca.
HX_SELECT_BUG = pytest.mark.xfail(
    reason="hx-select herdado esvazia o drawer em Escalados/Alertas (bug do app, ver comentario)",
    strict=True,
)


@HX_SELECT_BUG
def test_cartao_de_escalados_abre_o_drawer_e_esc_fecha(
    admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    page.goto("/dashboard/escalados")
    page.get_by_role("button", name=f"Detalhar o ticket {AWAITING}").click()
    body = _open_drawer(page)
    _assert_awaiting_drawer(body)
    expect(body.locator("#drawer-urgency")).to_have_value("muito_urgente")

    page.keyboard.press("Escape")
    expect(page.locator("#drawer")).to_be_hidden()
    expect(page).to_have_url(live_server.url("/dashboard/escalados"))


@HX_SELECT_BUG
def test_alerta_ligado_a_ticket_abre_o_drawer(admin_page: Page, live_server: LiveServer) -> None:
    live_server.sql(
        "INSERT INTO ops.alerts (severity, kind, message, payload, ticket_number, created_at) "
        "VALUES ('warn', 'api_failure', 'Falha de teste', '{}'::jsonb, $1, $2)",
        AWAITING,
        live_server.now,
    )
    page = admin_page
    page.goto("/dashboard/alerts")
    page.get_by_role("button", name=f"Abrir {AWAITING}").click()
    _assert_awaiting_drawer(_open_drawer(page))
