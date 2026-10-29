"""Pagina Tickets (PN-7, PN-8, PN-9, PN-43; task PN-T18) contra Postgres real."""
from __future__ import annotations

import asyncio
import re

import asyncpg
import pytest

from tests.panel import factories
from tests.panel.integration.test_access import HOST, NOW, Env, env, login  # noqa: F401

pytestmark = pytest.mark.db

URL = "/dashboard/tickets"
HX = {"HX-Request": "true"}


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:  # noqa: F811
    return asyncio.run(_seed(env.dsn))


def _rows(html: str) -> list[str]:
    return re.findall(r'<tr data-href="[^"]*"', html)


def _numbers(html: str) -> list[str]:
    return re.findall(r'class="hover:underline">(J-\d+)</a>', html)


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        r = c.get(URL)
    assert r.status_code == 302
    assert "accounts.positiveco.com.br/login" in r.headers["location"]


def test_pagina_completa_com_kpis_e_tabela(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "<title>Tickets" in html and 'aria-current="page"' in html
    for label in (
        "Total (30d)",
        "Aguardando resposta",
        "Escalados no período",
        "Sem classificação",
        "Respostas do bot",
        "há mais de 48h",
        "mídia sem texto",
    ):
        assert label in html
    for header in ("Ticket", "Data", "Tipo", "Status", "Mensagem", "Marca"):
        assert f">{header}</th>" in html
    assert len(_rows(html)) == 12  # 12 tickets semeados, uma pagina
    assert "Nenhum ticket encontrado." not in html
    assert 'id="tickets-resultado"' in html


def test_kpis_conferem_com_o_cenario(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL).text
    assert re.search(r">12</div>\s*<div class=\"text-xs text-ink-500 mt-1\">Total \(30d\)", html)
    assert re.search(
        r">4</div>\s*<div class=\"text-xs text-ink-500 mt-1\">Aguardando resposta", html
    )
    assert "+ 1 mídia sem texto" in html
    assert re.search(r">2</div>\s*<div class=\"text-xs text-ink-500 mt-1\">Escalados", html)
    assert "25% dos tickets" in html


def test_kpi_aguardando_leva_ao_filtro_de_status(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL).text
        assert 'href="/dashboard/tickets?status=awaiting&amp;days=30"' in html
        filtered = c.get(URL, params={"status": "awaiting"}).text
    assert len(_rows(filtered)) == 4


def test_filtros_combinados_e_busca_sem_acento(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        by_class = c.get(URL, params={"classification": "PRECO"}).text
        both = c.get(URL, params={"classification": "PRECO", "status": "awaiting"}).text
        by_number = c.get(URL, params={"q": "j-0001"}).text
        by_seller = c.get(URL, params={"q": "VENDEDOR so MIDIA"}).text
    assert len(_rows(by_class)) == 2 and len(_rows(both)) == 2
    assert _numbers(by_number) == ["J-0001"]
    assert len(_rows(by_seller)) == 2


def test_estado_vazio_e_filtros_refletidos(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"q": "nada-disso-existe", "classification": "MATERIAL"})
    html = r.text
    assert "Nenhum ticket encontrado." in html and not _rows(html)
    assert 'value="nada-disso-existe"' in html
    assert re.search(r'<option value="MATERIAL" selected>', html)


def test_busca_e_escapada(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"q": '"><script>x</script>'}).text
    assert "<script>x</script>" not in html
    assert "Nenhum ticket encontrado." in html


def test_periodo_e_valores_invalidos_viram_padrao(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"days": "7", "status": "em_atendimento", "page": "abc"})
        weird = c.get(URL, params={"days": "9999", "classification": "XYZ"})
    assert "Total (7d)" in r.text and len(_rows(r.text)) == 12
    assert weird.status_code == 200 and "Total (30d)" in weird.text


def test_marca_em_foco_filtra_e_midia_sem_marca_aparece(
    env: Env, seeded: factories.PanelWorld  # noqa: F811
) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "a_tal_da_castanha", domain=HOST)
        html = c.get(URL).text
    # 4 da marca B + 2 de midia sem marca (PN-6)
    assert len(_rows(html)) == 6
    assert "Total (30d)" in html


def test_fragmento_htmx_so_traz_o_resultado(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"status": "awaiting"}, headers=HX)
    assert r.status_code == 200
    assert "<html" not in r.text and "<form" not in r.text
    assert "Aguardando resposta" in r.text and len(_rows(r.text)) == 4
    assert r.headers["vary"] == "HX-Request"


def test_linha_abre_o_inbox_na_conversa(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    first = seeded.tickets[0]
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"q": first.ticket_number}).text
    href = f"/dashboard/inbox?u={first.user_id}"
    assert f'<tr data-href="{href}"' in html
    assert f'<a href="{href}" class="hover:underline">{first.ticket_number}</a>' in html
    assert "js/tickets.js" in html


def test_paginacao_de_25_em_25(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    async def add_more() -> None:
        conn = await asyncpg.connect(env.dsn)
        try:
            for i in range(20):
                await factories.insert_ticket(
                    conn,
                    number=100 + i,
                    user_id=seeded.sellers.one_brand,
                    status="answered_bot",
                    created_at=NOW.replace(hour=1, minute=i),
                    brand=factories.BRAND_A,
                )
        finally:
            await conn.close()

    asyncio.run(add_more())
    with env.client() as c:
        login(c)
        p1 = c.get(URL).text
        p2 = c.get(URL, params={"page": "2"}, headers=HX).text
    assert len(_rows(p1)) == 25 and "Página 1 de 2" in p1 and "32 itens total" in p1
    assert 'hx-get="/dashboard/tickets?&amp;page=2"' in p1 or "page=2" in p1
    assert len(_rows(p2)) == 7 and "Página 2 de 2" in p2


def test_banco_sem_tickets_nao_quebra(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert "Nenhum ticket encontrado." in r.text
    assert re.search(r"text-ink-500 mt-1\">Sem classificação", r.text)
    assert "—" in r.text  # % sem classificacao sem tickets: traco, nao divisao por zero
