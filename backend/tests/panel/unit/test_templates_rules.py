"""PN-T07: regras de template (spec 4.12). Varre TODOS os templates e JS do painel.

Como varre `frontend/templates/**/*.html` e `frontend/static/js/**/*.js`, as telas das
tarefas seguintes entram sozinhas. Tambem renderiza cada template com listas vazias
(PN-43) e confere macros, menu e base contra `app.panel.values`.
"""

from __future__ import annotations

import re
from pathlib import Path

import jinja2
import pytest

from app.panel import values
from app.panel.templating import build_templates
from app.paths import STATIC_DIR, TEMPLATES_DIR

JS_DIR = STATIC_DIR / "js"
TEMPLATES = sorted(TEMPLATES_DIR.rglob("*.html"))
SCRIPTS = sorted(JS_DIR.rglob("*.js"))
SOURCES = TEMPLATES + SCRIPTS


def _rel(path: Path) -> str:
    return path.relative_to(TEMPLATES_DIR.parent).as_posix()


def _ids(paths: list[Path]) -> list[str]:
    return [_rel(p) for p in paths]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _strip_jinja_comments(text: str) -> str:
    """Comentarios `{# #}` nao vao ao navegador; as regras olham o que e servido."""
    return re.sub(r"\{#.*?#\}", "", text, flags=re.S)


def test_sources_exist() -> None:
    names = {p.name for p in TEMPLATES}
    for required in (
        "base.html",
        "_nav.html",
        "_macros.html",
        "_banner_estado.html",
        "404.html",
        "500.html",
    ):
        assert required in names
    assert (JS_DIR / "base.js").is_file()


# --- regras de varredura (todas as fontes) ---------------------------------------------


@pytest.mark.parametrize("path", SOURCES, ids=_ids(SOURCES))
def test_no_native_dialogs_or_eval(path: Path) -> None:
    text = _text(path)
    assert not re.search(r"\b(confirm|alert|prompt)\s*\(", text), "janela nativa do navegador"
    assert "hx-confirm" not in text, "hx-confirm abre confirm() nativo"
    assert not re.search(r"\beval\s*\(|new\s+Function\s*\(|document\.write\s*\(", text)


@pytest.mark.parametrize("path", SOURCES, ids=_ids(SOURCES))
def test_no_beto(path: Path) -> None:
    assert not re.search(r"\bbeto\b", _text(path), re.I)


@pytest.mark.parametrize("path", SOURCES, ids=_ids(SOURCES))
def test_no_external_urls(path: Path) -> None:
    text = _text(path)
    # xmlns de SVG e o unico http(s):// aceito (nao carrega nada).
    urls = [u for u in re.findall(r"https?://[^\s\"'<>)]+", text) if "www.w3.org" not in u]
    assert not urls, f"URL externa (CDN proibido): {urls}"
    assert not re.search(r"""(?:src|href)\s*=\s*["']//""", text), "URL sem protocolo"
    assert not re.search(r"@import\s+url\(\s*['\"]?\s*(?:https?:)?//", text)


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_safe_filter(path: Path) -> None:
    text = _strip_jinja_comments(_text(path))
    assert not re.search(r"\|\s*safe\b", text), "filtro safe e proibido (autoescape)"
    assert not re.search(r"\bMarkup\s*\(", text)
    assert "{% autoescape false" not in text.replace("{%-", "{%")


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_inline_event_handlers(path: Path) -> None:
    text = _strip_jinja_comments(_text(path))
    assert not re.search(r"<[^<>]*\son[a-z]+\s*=", text, re.I), "handler on* inline (CSP)"
    assert not re.search(r"""(?:href|src|action)\s*=\s*["']\s*javascript:""", text, re.I)


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_inline_script_bodies(path: Path) -> None:
    text = _strip_jinja_comments(_text(path))
    for match in re.finditer(r"<script\b([^>]*)>(.*?)</script>", text, re.S | re.I):
        attrs, body = match.group(1), match.group(2)
        is_json = re.search(r"""type\s*=\s*["']application/json["']""", attrs)
        if is_json:
            continue
        assert "src=" in attrs, "script inline (CSP script-src 'self')"
        assert not body.strip(), "script com src nao pode ter corpo"
    assert not re.search(r"<script\b[^>]*/>", text)


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_no_reply_to_seller(path: Path) -> None:
    text = _strip_jinja_comments(_text(path))
    assert not re.search(r"responder\s+ao\s+vendedor", text, re.I)
    if re.match(r"_?(inbox|ticket_drawer)", path.name):
        assert "<textarea" not in text.lower(), "Inbox/drawer nao tem campo de resposta (PN-13/15)"
        assert not re.search(
            r"""name\s*=\s*["'](reply|resposta|responder|message)["']""", text, re.I
        )


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_modals_have_role_dialog(path: Path) -> None:
    text = _strip_jinja_comments(_text(path))
    for tag in re.findall(r"<[a-z][^<>]*>", text, re.I):
        if "aria-modal" in tag or "data-modal" in tag:
            assert 'role="dialog"' in tag, f"modal sem role=dialog: {tag[:80]}"


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_alpine_is_csp_safe(path: Path) -> None:
    """x-data so com nome de componente registrado em static/js (build CSP, sem eval)."""
    registered = {
        m for p in SCRIPTS for m in re.findall(r"Alpine\.data\(\s*['\"](\w+)['\"]", _text(p))
    }
    text = _strip_jinja_comments(_text(path))
    for name in re.findall(r"""\bx-data\s*=\s*["']([^"']*)["']""", text):
        assert re.fullmatch(r"\w+", name), f"x-data com expressao inline: {name!r}"
        assert name in registered, f"componente {name!r} nao registrado em static/js"
    assert not re.search(r"\bx-data\b(?!\s*=)", text), "x-data sem nome de componente"
    assert not re.search(r"\bx-html\b", text), "x-html injeta HTML sem autoescape"


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_alpine_expressions_are_plain_names(path: Path) -> None:
    """O Alpine CSP so avalia NOMES simples: `item.message`, `!open`, `a && b` ficam
    vazios em silencio (o aviso de sucesso saia em branco). x-for aceita `item in lista`."""
    text = _strip_jinja_comments(_text(path))
    directive = r"""(?:x-(?:text|show|if|model|bind:[\w-]+|on:[\w.:-]+)|:[\w-]+|@[\w.:-]+)"""
    for attr, value in re.findall(rf"""\s({directive})\s*=\s*"([^"]*)\"""", text):
        assert re.fullmatch(r"\w+", value.strip()), f"{attr}={value!r}: use um nome simples"
    for value in re.findall(r"""\bx-for\s*=\s*"([^"]*)\"""", text):
        assert re.fullmatch(r"\w+ in \w+", value.strip()), f"x-for={value!r}"


# --- renderizacao com listas vazias (PN-43) --------------------------------------------


def _env() -> jinja2.Environment:
    env = build_templates().env
    # Contexto vazio: nomes ausentes viram vazio (inclusive em cadeia), como listas vazias.
    env.undefined = jinja2.ChainableUndefined
    return env


EMPTY_CONTEXT: dict[str, object] = {
    "items": [],
    "rows": [],
    "tickets": [],
    "conversations": [],
    "people": [],
    "alerts": [],
    "csrf_token": "",
    "brand": "todas",
    "estado": {"paused": False, "unseen_alerts": 0},
}


@pytest.mark.parametrize("path", TEMPLATES, ids=_ids(TEMPLATES))
def test_templates_render_with_empty_lists(path: Path) -> None:
    html = _env().get_template(path.relative_to(TEMPLATES_DIR).as_posix()).render(EMPTY_CONTEXT)
    assert "Traceback" not in html


# --- base, menu e macros ---------------------------------------------------------------


def test_base_loads_only_local_vendor_in_order() -> None:
    html = _env().get_template("base.html").render({"csrf_token": "tok&<1>"})
    srcs = re.findall(r'<script defer src="([^"]+)"', html)
    paths = [s.split("?")[0] for s in srcs]
    assert paths == [
        "/static/vendor/htmx.min.js",
        "/static/js/base.js",
        "/static/vendor/alpine-csp.min.js",
    ]
    assert "base.js" in srcs[1] and srcs[0].startswith("/static/vendor/")
    assert '<link rel="stylesheet" href="/static/css/tailwind.css' in html


def test_base_csrf_header_is_escaped_json() -> None:
    html = _env().get_template("base.html").render({"csrf_token": "tok&<1>"})
    match = re.search(r"hx-headers='([^']*)'", html)
    assert match is not None
    assert "<" not in match.group(1)
    assert "\\u0026" in match.group(1) or "&amp;" in match.group(1)
    assert "X-CSRF-Token" in match.group(1)


def test_base_has_modal_drawer_toast_and_no_eval() -> None:
    html = _env().get_template("base.html").render({})
    assert html.count('role="dialog"') >= 2
    for needle in ('id="modal-body"', 'id="drawer-body"', 'aria-live="polite"', 'id="toasts"'):
        assert needle in html
    assert '"allowEval": false' in html


def test_base_shows_pause_banner_and_unseen_alerts() -> None:
    ctx = {"estado": {"paused": True, "unseen_alerts": 3}, "admin_name": "Caio", "active": "faq"}
    html = _env().get_template("base.html").render(ctx)
    assert "Jarvis pausado" in html
    assert "3 alertas não vistos" in html
    assert "<b>Caio</b>" in html
    assert 'href="/dashboard/faq" aria-current="page"' in html
    quiet = (
        _env()
        .get_template("_banner_estado.html")
        .render({"estado": {"paused": False, "unseen_alerts": 1}})
    )
    assert "pausado" not in quiet and "1 alerta não visto" in quiet


def test_extending_template_fills_content_block() -> None:
    html = _env().get_template("404.html").render({})
    assert "Página não encontrada" in html and 'id="conteudo"' in html


def test_nav_has_full_prototype_menu() -> None:
    html = _env().get_template("_nav.html").render({"active": "inbox", "brand": "zaya"})
    for label in (
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
    ):
        assert label in html
    for group in ("Operação", "Análise", "Admin"):
        assert group in html
    assert html.count('aria-current="page"') == 1


def test_nav_has_no_brand_selector() -> None:
    html = _env().get_template("_nav.html").render({})
    assert "/dashboard/marca" not in html
    assert values.ALL_BRANDS_LABEL not in html


def _macro(call: str) -> str:
    source = '{% from "_macros.html" import ' + call.split("(")[0] + " %}{{ " + call + " }}"
    return _env().from_string(source).render().strip()


def test_macros_match_values_labels() -> None:
    for status in values.TICKET_STATUSES:
        assert values.ticket_status_label(status) in _macro(f"status_chip('{status}')")
    for classification in values.CLASSIFICATIONS:
        assert values.classification_label(classification) in _macro(
            f"class_chip('{classification}')"
        )
    for brand in values.BRANDS:
        assert values.brand_label(brand) in _macro(f"brand_chip('{brand}')")
        assert f'title="{values.agent_name(brand)}"' in _macro(f"agent_face('{brand}')")
    assert 'title="Jarvis"' in _macro("agent_face(none)")
    assert "rosto-positive.png" in _macro("agent_face(none)")


def test_macros_handle_empty_values() -> None:
    assert "—" in _macro("status_chip(none)")
    assert "—" in _macro("brand_chip(none)")
    assert _macro("pct(0, 0)") == "—"
    assert _macro("pct(1, 4)") == "25%"
    assert "—" in _macro("phone_cell(none)")
    assert _macro("pagination(1, 1, 3, '/x', '#t')") == ""
    assert "Nenhum" in _macro("empty_row(3, 'Nenhum ticket encontrado.')")


def test_macros_escape_user_text_and_show_errors() -> None:
    assert "&lt;b&gt;" in _macro("empty_box('<b>x</b>')")
    html = _macro("error_list(['Pergunta vazia', 'Resposta obrigatória'])")
    assert 'role="alert"' in html and "2 problemas" in html
    assert _macro("error_list([])") == ""
    page = _macro("pagination(2, 3, 60, '/dashboard/tickets', '#lista', 'q=a')")
    assert "Página 2 de 3" in page and "page=1" in page and "page=3" in page
    assert 'value="t&lt;"' in _macro("csrf_field('t<')")


def test_macro_modal_heads_close_via_data_attribute() -> None:
    assert "data-overlay-close" in _macro("modal_head('Adicionar pergunta')")
    assert "data-overlay-close" in _macro("drawer_head('Ticket', 'J-0142')")
