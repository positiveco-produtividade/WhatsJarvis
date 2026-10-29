"""Pagina Relatorios e PDF mensal (PN-42, PN-43, PN-4; task PN-T50) contra Postgres real."""
from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from urllib.parse import quote

import asyncpg
import pytest

from app.panel.api.relatorios import month_options, pdf_filename
from tests.panel import factories
from tests.panel.factories import BRAND_A
from tests.panel.integration.test_access import NOW, Env, env, login  # noqa: F401

pytestmark = pytest.mark.db

URL = "/dashboard/relatorios"
PDF_URL = URL + "/mensal.pdf"
QUESTION = "Argumento para preço do Possible no atacarejo"


async def _seed(dsn: str, *, with_data: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        world = await factories.seed_world(conn, NOW)
        await conn.execute("DELETE FROM chat.tickets")
        if not with_data:
            return
        user = world.sellers.one_brand
        for n in (1, 2):
            await factories.insert_ticket(
                conn,
                number=700 + n,
                user_id=user,
                brand=BRAND_A,
                status="awaiting",
                classification="ARGUMENTO",
                message=QUESTION,
                created_at=NOW - timedelta(days=1, minutes=n),
            )
    finally:
        await conn.close()


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        assert c.get(URL).status_code == 302
        assert c.get(PDF_URL).status_code == 302


def test_pagina_com_dados_e_criar_na_faq_pelo_ticket(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    html = r.text
    assert "<title>Relatórios" in html and 'aria-current="page"' in html
    assert "Perguntas mais frequentes" in html and "Objeções mais citadas" in html
    assert "Argumento para preço do Possible no atacarejo" in html
    assert "2x" in html
    links = re.findall(r'hx-get="(/dashboard/faq/nova[^"]*)"', html)
    assert links
    for link in links:
        assert re.fullmatch(r"/dashboard/faq/nova\?ticket=J-\d{4,}", link)
        assert quote("preço") not in link and "atacarejo" not in link
    assert "mensal.pdf" in html and "Baixar relatório do mês (PDF)" in html


def test_pagina_sem_dados_mostra_estados_vazios(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=False))
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"periodo": "xyz"})
    assert r.status_code == 200
    assert "Nenhuma pergunta neste período." in r.text
    assert "Nenhuma objeção neste período." in r.text
    assert "Criar na FAQ" not in r.text
    assert "<option value=\"30\" selected>" in r.text


def test_pdf_devolve_application_pdf_com_nome_seguro(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=True))
    with env.client() as c:
        login(c)
        r = c.get(PDF_URL, params={"mes": "2026-10"})
        bad = c.get(PDF_URL, params={"mes": '../x"; evil'})
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'attachment; filename="relatorio-2026-10.pdf"'
    assert r.content.startswith(b"%PDF")
    assert bad.status_code == 200
    assert bad.headers["content-disposition"] == 'attachment; filename="relatorio-2026-10.pdf"'


def test_pdf_de_mes_sem_dados_nao_da_erro(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, with_data=False))
    with env.client() as c:
        login(c)
        r = c.get(PDF_URL, params={"mes": "2020-01"})
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert "relatorio-2020-01.pdf" in r.headers["content-disposition"]


def test_opcoes_de_mes_viram_o_ano() -> None:
    options = month_options(NOW.replace(month=2))
    assert options[0][0] == "2026-02" and options[1][0] == "2026-01"
    assert options[2][0] == "2025-12" and len(options) == 12
    assert pdf_filename("2026-10") == "relatorio-2026-10.pdf"
