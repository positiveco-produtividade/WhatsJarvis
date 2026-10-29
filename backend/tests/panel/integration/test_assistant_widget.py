"""Widget flutuante do assistente (PN-T57; PN-50 desligado, PN-51 cabecalho do agente)."""
# ruff: noqa: F811
from __future__ import annotations

import re

import pytest

from app.panel.assistant.agents import AGENTS, COORDINATOR
from app.panel.assistant.disabled import DISABLED_REASON
from app.paths import STATIC_DIR
from tests.panel.integration.test_access import Env, env, login  # noqa: F401

pytestmark = pytest.mark.db

JS = (STATIC_DIR / "js" / "assistente.js").read_text(encoding="utf-8")
DISABLED_TEXT = "O assistente será ativado quando a chave do Gemini for configurada"


def test_widget_aparece_nas_telas_com_scripts_locais(env: Env) -> None:
    with env.client() as c:
        login(c)
        html = c.get("/dashboard/faq").text
    assert 'id="assistente"' in html and 'role="dialog"' in html
    assert f'data-disabled-text="{DISABLED_TEXT}"' in html
    srcs = re.findall(r'<script src="([^"]+)" defer>', html)
    assert [s.split("?")[0] for s in srcs] == [
        "/static/vendor/marked.min.js",
        "/static/vendor/purify.min.js",
        "/static/js/assistente.js",
    ]
    assert html.index("alpine-csp.min.js") < html.index("assistente.js")
    assert "x-html" not in html


def test_nove_chips_e_todos_desabilitaveis(env: Env) -> None:
    with env.client() as c:
        login(c)
        html = c.get("/dashboard/faq").text
    chips = re.findall(r"<button[^>]*data-chip[^>]*>", html)
    assert len(chips) == 9
    assert all(':disabled="chipsOff"' in chip for chip in chips)
    assert 'x-model="draft"' in html and ':disabled="inputOff"' in html


def test_status_desligado_traz_a_mensagem_do_widget(env: Env) -> None:
    with env.client() as c:
        login(c)
        body = c.get("/api/assistant/status").json()
    assert body["enabled"] is False and body["reason"] == DISABLED_REASON == DISABLED_TEXT
    assert "reason" in JS and "body.enabled === true" in JS
    assert DISABLED_TEXT in JS


def test_status_lista_rostos_que_existem_em_static(env: Env) -> None:
    with env.client() as c:
        login(c)
        agents = c.get("/api/assistant/status").json()["agents"]
    assert len(agents) == 1 + len(AGENTS)
    for agent in agents:
        assert (STATIC_DIR / "img" / "bots" / agent["face_image"]).is_file()
        assert (STATIC_DIR / "img" / "bots" / agent["bot_image"]).is_file()
    assert agents[0]["face_image"] == COORDINATOR.face_image


def test_cabecalho_segue_o_agente_da_ultima_resposta() -> None:
    assert "res.body.agent" in JS and "lastAgent" in JS
    assert "agent.face_image" in JS and "agent.label" in JS
    assert "COORDINATOR_SUB" in JS


def test_respostas_so_entram_sanitizadas() -> None:
    assert "DOMPurify.sanitize(window.marked.parse" in JS or (
        "DOMPurify.sanitize(" in JS and "marked.parse(" in JS
    )
    assert JS.count("innerHTML") == 1
    assert "textContent = msg.text" in JS
    assert re.search(r"innerHTML = html;", JS)
    assert "eval(" not in JS and "x-html" not in JS


def test_envio_usa_csrf_e_json() -> None:
    assert "'X-CSRF-Token': csrfToken()" in JS
    assert "/api/assistant/chat" in JS and "JSON.stringify" in JS
    assert "Alpine.data('assistente'" in JS
