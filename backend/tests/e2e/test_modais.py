"""E2E: pausa e marcas pedem confirmacao no modal da pagina (PN-T66; PN-45, PN-47, PN-48).

Toda janela nativa do navegador (alert/confirm/prompt) e registrada por `page.on("dialog")` e o
teste exige que nenhuma apareca. Confirmar duas vezes (duas abas com o modal aberto) nao alterna:
o POST leva o estado desejado e o segundo so avisa "Ja estava assim".
"""
from __future__ import annotations

import re

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright nao instalado (E2E opcional)")

from playwright.sync_api import BrowserContext, Locator, Page, expect  # noqa: E402

from tests.e2e.conftest import LiveServer, no_native_dialogs  # noqa: E402

pytestmark = [pytest.mark.e2e, pytest.mark.db]

ALREADY = "Já estava assim: nada foi alterado."


def _flag(live_server: LiveServer, key: str) -> str | None:
    return live_server.sql(  # type: ignore[no-any-return]
        "SELECT value::text FROM ops.system_state WHERE key = $1", key
    )


def _count(live_server: LiveServer, query: str, *args: object) -> int:
    return int(live_server.sql(query, *args))


def _audits(live_server: LiveServer, key: str) -> int:
    return _count(
        live_server,
        "SELECT count(*) FROM ops.audit_log WHERE action = 'flag.set' AND target_id = $1",
        key,
    )


def _alerts(live_server: LiveServer, kind: str) -> int:
    return _count(live_server, "SELECT count(*) FROM ops.alerts WHERE kind = $1", kind)


def _open_alerts(page: Page) -> None:
    page.goto("/dashboard/alerts")
    expect(page.locator("#control-pausa button")).to_be_visible()


def _modal(page: Page) -> Locator:
    modal = page.locator("#modal")
    expect(modal).to_be_visible()
    return modal


def _toast(page: Page, text: str) -> None:
    expect(page.locator("#toasts").get_by_text(text, exact=True).last).to_be_visible()


def _brand_row(page: Page, label: str) -> Locator:
    return page.locator("#control-marcas li").filter(has_text=label)


def test_pausar_e_retomar_no_modal_sem_janela_nativa(
    context: BrowserContext, admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    dialogs = no_native_dialogs(page)
    _open_alerts(page)
    pause_button = page.locator("#control-pausa button")
    expect(pause_button).to_have_text("Pausar Jarvis")

    # Cancelar nao muda nada.
    pause_button.click()
    modal = _modal(page)
    expect(modal.get_by_role("heading", name="Pausar o Jarvis?")).to_be_visible()
    expect(modal.get_by_text("Agora: Jarvis em operação")).to_be_visible()
    modal.get_by_role("button", name="Cancelar").click()
    expect(page.locator("#modal")).to_be_hidden()
    assert _flag(live_server, "paused") is None

    # Duas abas com o mesmo modal aberto (PN-48): a segunda confirmacao nao alterna.
    other = context.new_page()
    other_dialogs = no_native_dialogs(other)
    _open_alerts(other)
    other.locator("#control-pausa button").click()
    expect(_modal(other).get_by_text("Agora: Jarvis em operação")).to_be_visible()

    pause_button.click()
    _modal(page).locator("[data-control-confirm]").click()
    expect(page.locator("#modal")).to_be_hidden()
    _toast(page, "Jarvis pausado.")
    expect(page.locator("#control-pausa button")).to_have_text("▶ Retomar operação")
    expect(page.get_by_role("alert").filter(has_text="Jarvis pausado.")).to_be_visible()
    assert _flag(live_server, "paused") == "true"

    other.locator("#modal [data-control-confirm]").click()  # modal antigo, "Pausar"
    _toast(other, ALREADY)
    expect(other.locator("#control-pausa button")).to_have_text("▶ Retomar operação")
    assert _flag(live_server, "paused") == "true"
    assert _audits(live_server, "paused") == 1 and _alerts(live_server, "pause") == 1

    # Reaberto, o modal mostra o estado atual; retomar volta a operar.
    page.locator("#control-pausa button").click()
    modal = _modal(page)
    expect(modal.get_by_role("heading", name="Retomar operação?")).to_be_visible()
    expect(modal.get_by_text("Agora: Jarvis pausado")).to_be_visible()
    modal.locator("[data-control-confirm]").click()
    _toast(page, "Operação retomada.")
    expect(page.locator("#control-pausa button")).to_have_text("Pausar Jarvis")
    assert _flag(live_server, "paused") == "false"
    assert _audits(live_server, "paused") == 2 and _alerts(live_server, "resume") == 1

    assert dialogs == [] and other_dialogs == []


def test_desligar_e_ligar_marca_no_modal_sem_janela_nativa(
    context: BrowserContext, admin_page: Page, live_server: LiveServer
) -> None:
    page = admin_page
    key = "brand_enabled:plant_power"
    dialogs = no_native_dialogs(page)
    _open_alerts(page)
    summary = page.locator("#control-marcas")
    expect(summary).to_contain_text(re.compile(r"Respondendo em\s+5 de 5"))
    row = _brand_row(page, "Plant Power")
    expect(row.get_by_text("Léo")).to_be_visible()  # agente da marca
    expect(row.get_by_text("Respondendo", exact=True)).to_be_visible()

    # Cancelar no X do modal nao muda nada.
    row.get_by_role("button", name="Desligar").click()
    modal = _modal(page)
    expect(modal.get_by_role("heading", name="Desligar o Jarvis na Plant Power?")).to_be_visible()
    modal.get_by_role("button", name="Fechar").click()
    expect(page.locator("#modal")).to_be_hidden()
    assert _flag(live_server, key) is None

    other = context.new_page()
    other_dialogs = no_native_dialogs(other)
    _open_alerts(other)
    _brand_row(other, "Plant Power").get_by_role("button", name="Desligar").click()
    expect(_modal(other).get_by_text(re.compile(r"Agora: Respondendo"))).to_be_visible()

    row.get_by_role("button", name="Desligar").click()
    _modal(page).locator("[data-control-confirm]").click()
    expect(page.locator("#modal")).to_be_hidden()
    _toast(page, "Jarvis desligado na Plant Power.")
    row = _brand_row(page, "Plant Power")
    expect(row.get_by_text("Desligado — a equipe atende")).to_be_visible()
    expect(page.locator("#control-marcas")).to_contain_text(re.compile(r"Respondendo em\s+4 de 5"))
    assert _flag(live_server, key) == "false"

    other.locator("#modal [data-control-confirm]").click()  # modal antigo, "Desligar"
    _toast(other, ALREADY)
    assert _flag(live_server, key) == "false"
    assert _audits(live_server, key) == 1 and _alerts(live_server, "brand_off") == 1

    row.get_by_role("button", name="Ligar").click()
    modal = _modal(page)
    expect(modal.get_by_role("heading", name="Ligar o Jarvis na Plant Power?")).to_be_visible()
    modal.locator("[data-control-confirm]").click()
    _toast(page, "Jarvis ligado na Plant Power.")
    expect(page.locator("#control-marcas")).to_contain_text(re.compile(r"Respondendo em\s+5 de 5"))
    assert _flag(live_server, key) == "true"
    assert _alerts(live_server, "brand_on") == 1

    assert dialogs == [] and other_dialogs == []
