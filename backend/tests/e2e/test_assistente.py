"""E2E: assistente flutuante desligado (PN-T66; PN-50).

Sem chave do Gemini o widget aparece em todas as telas, abre, mostra o time de agentes (Jarvis
sozinho em cima; Duda, Leo, Nico, Rita e Tati embaixo), os chips e o aviso de desligado, com
chips e campo de pergunta desativados. O modo ligado (provedor falso) segue coberto pelos testes
de integracao (`panel/integration/test_assistant_api.py`).
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (E2E opcional)")

from playwright.sync_api import Page, expect  # noqa: E402

from app.panel.assistant.disabled import DISABLED_REASON  # noqa: E402
from tests.e2e.conftest import LiveServer, no_native_dialogs  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.db]

TEAM = (
    "Duda · Jungle",
    "Léo · Plant Power",
    "Nico · Possible",
    "Rita · A Tal da Castanha",
    "Tati · Zaya",
)
CHIPS = (
    "Ver escalados",
    "Quem não aceitou",
    "Métricas 7d",
    "Pedidos de acesso",
    "Jarvis na semana",
    "Semana x anterior",
    "O que falta na FAQ",
    "Onde o Jarvis erra",
    "Saúde do sistema",
)


@pytest.mark.parametrize("path", ["/dashboard/tickets", "/dashboard/faq"])
def test_widget_desligado_mostra_o_time_e_o_aviso(
    admin_page: Page, live_server: LiveServer, path: str
) -> None:
    page = admin_page
    dialogs = no_native_dialogs(page)
    page.goto(path)
    widget = page.locator("#assistente")
    panel = widget.get_by_role("dialog", name="Assistente Jarvis")
    expect(panel).to_be_hidden()

    with page.expect_response(lambda r: r.url.endswith("/api/assistant/status")) as status:
        widget.get_by_role("button", name="Abrir assistente").click()
    body = status.value.json()
    assert body["enabled"] is False and body["reason"] == DISABLED_REASON
    names = [a["name"] for a in body["agents"]]
    assert names[0] == "Jarvis" and len(names) == 6

    expect(panel).to_be_visible()
    expect(panel.get_by_text(DISABLED_REASON, exact=True)).to_be_visible()
    expect(panel.get_by_text("Olá! Como podemos ajudar?")).to_be_visible()

    # Jarvis sozinho e maior na primeira linha; os cinco agentes de marca embaixo.
    jarvis = panel.locator('img[title="Jarvis"][src*="bot-positive"]')
    expect(jarvis).to_be_visible()
    jarvis_box = jarvis.bounding_box()
    assert jarvis_box is not None
    for title in TEAM:
        agent = panel.locator(f'img[title="{title}"]')
        expect(agent).to_be_visible()
        box = agent.bounding_box()
        assert box is not None
        assert box["y"] > jarvis_box["y"] and box["height"] < jarvis_box["height"]

    for label in CHIPS:
        expect(panel.get_by_role("button", name=label, exact=True)).to_be_disabled()
    expect(panel.get_by_label("Pergunta para o assistente")).to_be_disabled()
    expect(panel.get_by_role("button", name="Enviar pergunta")).to_be_disabled()
    expect(panel.get_by_role("button", name="Expandir ou recolher o assistente")).to_be_visible()
    expect(page.get_by_text("Beto")).to_have_count(0)

    panel.get_by_role("button", name="Fechar o assistente").click()
    expect(panel).to_be_hidden()
    expect(widget.get_by_role("button", name="Abrir assistente")).to_be_visible()
    assert dialogs == []
