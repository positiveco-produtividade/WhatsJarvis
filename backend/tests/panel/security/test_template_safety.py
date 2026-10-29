"""XSS no momento da renderizacao (PN-T63): payloads em nomes, tickets e FAQ saem escapados.

Injeta `<script>`, aspas e manipuladores de evento nos dados que o painel exibe (nomes, empresa,
tickets, FAQ, mensagens do Inbox, perguntas ao assistente, auditoria, alertas, pedidos de acesso)
e renderiza TODAS as telas e fragmentos. Em nenhuma resposta pode existir elemento novo (script
com conteudo, img/svg/iframe), atributo `on*` ou o payload cru; onde o dado aparece, ele vem
escapado. Tambem cobre telas que devolvem o que o admin digitou (formularios e previa de CSV).
"""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import re
from html.parser import HTMLParser
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.panel.assistant import log as assistant_log
from app.services import audit
from tests.panel import factories
from tests.panel.factories import BRAND_A, insert_user
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
    post_headers,
)
from tests.panel.integration.test_people_read import add_attempt, set_display, set_status

pytestmark = [pytest.mark.security, pytest.mark.db]

HX = {"HX-Request": "true"}
SCRIPT = "<script>alert(1)</script>"
IMG = '"><img src=x onerror=alert(2)>'
HANDLER = "' onmouseover='alert(3)' x='"
PAYLOAD = f"{SCRIPT}{IMG}{HANDLER}"
ESCAPED_SCRIPT = "&lt;script&gt;alert(1)&lt;/script&gt;"
RAW_FORBIDDEN = ("<script>alert", "<img src=x", "onerror=alert(2)>", "<iframe")
DANGEROUS_TAGS = frozenset({"iframe", "object", "embed"})


class _Audit(HTMLParser):
    """Coleta o que um XSS bem-sucedido deixaria: script com corpo, tags e atributos `on*`."""

    def __init__(self) -> None:
        super().__init__()
        self.problems: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DANGEROUS_TAGS:
            self.problems.append(f"tag <{tag}>")
        if tag == "img" and any(k == "src" and v == "x" for k, v in attrs):
            self.problems.append("<img src=x>")
        for name, _ in attrs:
            if name.startswith("on"):
                self.problems.append(f"atributo {name} em <{tag}>")
        # `<script type="application/json">` e bloco de dados (nao executa); o resto, sem corpo.
        self._in_script = tag == "script" and ("type", "application/json") not in attrs

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_script and data.strip():
            self.problems.append(f"script inline: {data.strip()[:40]}")


def audit_html(html: str) -> list[str]:
    parser = _Audit()
    parser.feed(html)
    problems = list(parser.problems)
    problems += [f"payload cru: {raw}" for raw in RAW_FORBIDDEN if raw in html]
    return problems


class Seeded:
    def __init__(self, user_id: int, other_id: int, faq_id: int | None) -> None:
        self.user_id = user_id
        self.other_id = other_id
        self.faq_id = faq_id


async def _seed(dsn: str) -> Seeded:
    conn = await asyncpg.connect(dsn)
    try:
        world = await factories.seed_world(conn, NOW)
        uid = await insert_user(conn, name=PAYLOAD, seq=50, brands=[BRAND_A], now=NOW)
        other = await insert_user(
            conn, name=f"Revogada {PAYLOAD}", seq=51, brands=[BRAND_A], now=NOW
        )
        await set_status(conn, other, "revoked", NOW)
        await conn.execute(
            "UPDATE access.users SET company = $2, manager_name = $2 WHERE id = $1", uid, PAYLOAD
        )
        await factories.insert_ticket(
            conn, number=900, user_id=uid, status="awaiting", created_at=NOW, brand=BRAND_A,
            message=PAYLOAD, escalated=True, urgency="urgente",
        )
        await factories.insert_ticket(
            conn, number=901, user_id=uid, status="answered_human", created_at=NOW, brand=BRAND_A,
            message=PAYLOAD,
        )
        await conn.execute(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, body) "
            "VALUES (gen_random_uuid()::text, $1, 'text', $2, 'allowed', 'allowed', $3)",
            NOW,
            uid,
            PAYLOAD,
        )
        faq_id = await _seed_faq(conn, world)
        await set_display(conn, True, NOW)
        await add_attempt(conn, 1, NOW, count=4, body=PAYLOAD)
        await conn.execute(
            "INSERT INTO ops.alerts (severity, kind, message, payload, created_at) "
            "VALUES ('high', 'api_failure', $1, '{}'::jsonb, $2)",
            PAYLOAD,
            NOW,
        )
        await assistant_log.record_answer(
            conn, asked_by=PAYLOAD, agent="jarvis", question=PAYLOAD, answer=PAYLOAD,
            tools_used=[PAYLOAD], tokens_in=1, tokens_out=1, latency_ms=1,
        )
        await audit.write(conn, "panel", PAYLOAD, "bulk.op", "user", PAYLOAD, PAYLOAD, PAYLOAD)
        return Seeded(uid, other, faq_id)
    finally:
        await conn.close()


async def _seed_faq(conn: asyncpg.Connection, world: factories.PanelWorld) -> int | None:
    if not world.faq.available:
        return None
    item = world.faq.ids[0]
    await conn.execute(
        "UPDATE content.faq_items SET question = $2, answer = $2, category = 'PRODUTO', "
        "variations = ARRAY[$2]::text[] WHERE id = $1",
        item,
        PAYLOAD,
    )
    return item


def _pages(s: Seeded) -> list[str]:
    urls = [
        "/dashboard/tickets",
        "/dashboard/tickets/J-0900",
        "/dashboard/tickets/J-0901",
        "/dashboard/escalados",
        "/dashboard/inbox",
        f"/dashboard/inbox?u={s.user_id}",
        f"/dashboard/inbox/{s.user_id}",
        "/dashboard/faq",
        "/dashboard/faq?q=%3Cscript%3E",
        "/dashboard/faq/nova",
        "/dashboard/pessoas",
        "/dashboard/pessoas?aba=pedindo",
        "/dashboard/pessoas?aba=retiradas",
        "/dashboard/pessoas?q=%3Cscript%3Ealert(1)%3C/script%3E",
        f"/dashboard/pessoas/{s.user_id}",
        f"/dashboard/pessoas/{s.user_id}/revogar",
        f"/dashboard/pessoas/{s.user_id}/excluir",
        f"/dashboard/pessoas/{s.other_id}",
        "/dashboard/pessoas/nova",
        "/dashboard/pessoas/nova?pedido=1",
        "/dashboard/pessoas/importar",
        "/dashboard/alerts",
        "/dashboard/metrics",
        "/dashboard/relatorios",
        "/dashboard/logs",
        "/dashboard/logs?aba=auditoria",
        "/dashboard/logs/auditoria",
        "/dashboard/logs/auditoria?acao=bulk.op",
        "/dashboard/logs/problemas",
        "/dashboard/logs/servidor",
        "/dashboard/logs/retencao",
        "/dashboard/assistente-perguntas",
        "/dashboard/estado",
        "/dashboard/control/pausa",
        "/dashboard/control/painel",
        "/dashboard/marca",
    ]
    if s.faq_id is not None:
        urls.append(f"/dashboard/faq/{s.faq_id}/editar")
    return urls


def _fetch(c: TestClient, url: str, headers: dict[str, str]) -> tuple[int, str]:
    r = c.get(url, headers=headers)
    return r.status_code, r.text


def test_nenhuma_tela_executa_payload_injetado(env: Env) -> None:
    seeded = asyncio.run(_seed(env.dsn))
    bad: list[str] = []
    shown = 0
    with env.client() as c:
        login(c)
        for url in _pages(seeded):
            for label, headers in (("pagina", {}), ("fragmento", HX)):
                status, html = _fetch(c, url, headers)
                if status >= 500:
                    bad.append(f"{url} [{label}] -> {status}")
                    continue
                if "/pessoas?aba=pedindo" in url and ESCAPED_SCRIPT in html:
                    shown += 1
                bad += [f"{url} [{label}] {p}" for p in audit_html(html)]
    assert not bad, "payload nao escapado:\n" + "\n".join(bad)
    assert shown, "o texto injetado nao apareceu na aba Pedindo acesso (teste vazio)"


@pytest.mark.parametrize(
    ("url", "needle"),
    [
        ("/dashboard/tickets", ESCAPED_SCRIPT),
        ("/dashboard/tickets/J-0900", ESCAPED_SCRIPT),
        ("/dashboard/escalados", ESCAPED_SCRIPT),
        ("/dashboard/pessoas", ESCAPED_SCRIPT),
        ("/dashboard/pessoas?aba=pedindo", ESCAPED_SCRIPT),
        ("/dashboard/pessoas?aba=retiradas", ESCAPED_SCRIPT),
        ("/dashboard/assistente-perguntas", ESCAPED_SCRIPT),
        ("/dashboard/logs/auditoria", ESCAPED_SCRIPT),
    ],
)
def test_dado_injetado_aparece_escapado(env: Env, url: str, needle: str) -> None:
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        html = c.get(url, headers=HX).text
    assert needle in html, f"{url}: o dado deveria aparecer (escapado)"
    assert not audit_html(html)


def test_inbox_chat_e_ficha_escapam(env: Env) -> None:
    seeded = asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        chat = c.get(f"/dashboard/inbox/{seeded.user_id}", headers=HX).text
        ficha = c.get(f"/dashboard/pessoas/{seeded.user_id}", headers=HX).text
    assert ESCAPED_SCRIPT in chat and ESCAPED_SCRIPT in ficha
    assert not audit_html(chat) and not audit_html(ficha)


def test_faq_escapa_no_formulario_de_edicao(env: Env) -> None:
    seeded = asyncio.run(_seed(env.dsn))
    if seeded.faq_id is None:
        pytest.skip("content.faq_items inexistente")
    with env.client() as c:
        login(c)
        form = c.get(f"/dashboard/faq/{seeded.faq_id}/editar", headers=HX).text
        lista = c.get("/dashboard/faq", headers=HX).text
    assert ESCAPED_SCRIPT in form and ESCAPED_SCRIPT in lista
    assert not audit_html(form) and not audit_html(lista)


# ---- telas que devolvem o que o admin digitou --------------------------------------------


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def test_formulario_da_faq_com_erro_devolve_valores_escapados(env: Env) -> None:
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        r = c.post(
            "/dashboard/faq/nova",
            data={"brand": "jungle", "category": "PRODUTO", "question": PAYLOAD, "answer": ""},
            headers={**post_headers(_token(c)), **HX},
        )
    assert r.status_code < 500
    assert not audit_html(r.text)


def test_formulario_de_pessoa_com_erro_devolve_valores_escapados(env: Env) -> None:
    asyncio.run(_seed(env.dsn))
    data: dict[str, Any] = {
        "nome": PAYLOAD,
        "telefone": "123",
        "perfil": "vendedor",
        "empresa": PAYLOAD,
        "gestor": PAYLOAD,
        "ufs": ["CE"],
        "canais": ["farma"],
        "marcas": ["jungle"],
    }
    with env.client() as c:
        login(c)
        r = c.post(
            "/dashboard/pessoas/nova", data=data, headers={**post_headers(_token(c)), **HX}
        )
    assert r.status_code < 500
    assert ESCAPED_SCRIPT in r.text, "o formulario deveria devolver o valor digitado, escapado"
    assert not audit_html(r.text)


def test_previa_da_importacao_de_pessoas_escapa_linhas_do_arquivo(env: Env) -> None:
    header = "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor"
    quoted = PAYLOAD.replace(chr(34), chr(34) * 2)
    row = f'"{quoted}",+5585991231201,vendedor,Loja,CE,farma,jungle,G'
    csv = ("\n".join([header, row]) + "\n").encode("utf-8")
    base = "/dashboard/pessoas/importar"
    with env.client() as c:
        login(c)
        up = c.post(
            base,
            files={"file": ("pessoas.csv", csv, "text/csv")},
            data={"x": "1"},
            headers=post_headers(_token(c)),
        )
        assert up.status_code == 303, up.status_code
        html = c.get(up.headers["location"]).text
    assert not audit_html(html)
    assert re.search(r"&lt;script&gt;|&#60;script&#62;", html), "previa nao mostrou a linha"


def test_previa_da_importacao_de_faq_escapa_linhas_do_arquivo(env: Env) -> None:
    quoted = PAYLOAD.replace('"', '""')
    csv = f'pergunta;resposta;categoria;bot responde\n"{quoted}";"{quoted}";Produto;sim\n'
    base = "/dashboard/faq/importar"
    with env.client() as c:
        login(c)
        up = c.post(
            base,
            data={"brand": "jungle"},
            files={"file": ("faq.csv", csv.encode("utf-8"), "text/csv")},
            headers=post_headers(_token(c)),
        )
        if up.status_code != 303:
            pytest.skip(f"upload da FAQ nao redirecionou ({up.status_code})")
        html = c.get(up.headers["location"]).text
    assert not audit_html(html)
