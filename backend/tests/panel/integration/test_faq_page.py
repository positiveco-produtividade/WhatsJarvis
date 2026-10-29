"""Pagina FAQ: lista e filtros (PN-22, PN-43, PN-5; task PN-T26) contra Postgres real."""
from __future__ import annotations

import asyncio
import re

import asyncpg
import pytest

from app.panel.services.faq_validation import question_key
from tests.panel import factories
from tests.panel.integration.test_access import HOST, NOW, Env, env, login  # noqa: F401

pytestmark = pytest.mark.db

URL = "/dashboard/faq"
HX = {"HX-Request": "true"}
ROW = '<tr class="border-t border-ink-100 hover:bg-ink-50 transition">'


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


async def _sql(dsn: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()


async def _add(dsn: str, question: str, *, brand: str = "todas", category: str = "PRODUTO") -> None:
    await _sql(
        dsn,
        "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
        "variations, bot_responde, active, origin, version, created_at, created_by, "
        "updated_at, updated_by) VALUES ($1, $2, $3, $4, 'resp', '{}', true, true, 'manual', 1, "
        "$5, 't', $5, 't')",
        brand,
        category,
        question,
        question_key(question),
        NOW,
    )


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:  # noqa: F811
    world = asyncio.run(_seed(env.dsn))
    if not world.faq.available:
        pytest.skip("content.faq_items inexistente")
    return world


def _rows(html: str) -> int:
    return html.count(ROW)


def _body(html: str) -> str:
    """So a tabela (o menu e o seletor da pagina tambem citam os nomes das marcas)."""
    return html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


def _numbers(html: str) -> list[int]:
    return [int(n) for n in re.findall(r'title="id \d+ no banco">(\d+)</td>', html)]


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        r = c.get(URL)
    assert r.status_code == 302
    assert "accounts.positiveco.com.br/login" in r.headers["location"]


def test_pagina_completa_dez_por_pagina(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "<title>FAQ" in html and 'aria-current="page"' in html
    for header in ("Categoria", "Pergunta", "Resposta", "Atualizado", "Ações"):
        assert f">{header}</th>" in html
    assert _rows(html) == 10 and _numbers(html) == list(range(1, 11))
    assert "Página 1 de 3" in html and "25 itens total" in html
    assert "25 itens ativos na FAQ" in html
    assert "Importar planilha" in html and "+ Adicionar pergunta" in html
    assert 'id="faq-resultado"' in html


def test_marca_todas_aparece_primeiro(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL).text
    # 9 itens "todas" no cenario: os 9 primeiros; o 10o ja e de marca
    body = _body(html)
    assert body.count("todas as marcas</span>") == 9
    assert body.rindex("todas as marcas</span>") < body.index("Plant Power</span>")


def test_seletor_de_marca_com_todas_primeiro(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL).text
    select = html.split('id="faq-brand"')[1].split("</select>")[0]
    opts = re.findall(r'<option value="([a-z_]+)" ', select)
    assert opts[0] == "todas" and "jungle" in opts and len(opts) == 6


def test_filtro_de_marca_inclui_itens_de_todas(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"brand": "plant_power"}).text
        p2 = c.get(URL, params={"brand": "plant_power", "page": "2"}, headers=HX).text
    assert "17 itens ativos na FAQ" in html and "<strong>Plant Power</strong>" in html
    assert "Página 1 de 2" in html and _rows(html) == 10 and _rows(p2) == 7
    assert "A Tal da Castanha</span>" not in _body(html) + _body(p2)
    assert re.search(r'<option value="plant_power" selected>', html)


def test_marca_do_cabecalho_e_o_padrao_e_a_escolha_da_tela_vence(
    env: Env, seeded: factories.PanelWorld  # noqa: F811
) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "a_tal_da_castanha", domain=HOST)
        by_cookie = c.get(URL).text
        explicit = c.get(URL, params={"brand": "todas"}).text
    assert "17 itens ativos" in by_cookie and "<strong>A Tal da Castanha</strong>" in by_cookie
    assert "25 itens ativos" in explicit


def test_filtro_de_categoria(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"category": "PRODUTO"}).text
        combined = c.get(URL, params={"category": "PRODUTO", "brand": "plant_power"}).text
    assert _rows(html) == 5 and "5 itens total" not in html  # uma pagina so
    assert re.search(r'<option value="PRODUTO" selected>', html)
    assert _rows(combined) == 3  # PRODUTO: i=0,5,10,15,20 -> todas, A, B, todas, B... filtra marca
    assert "Limpar" in html


def test_busca_na_pergunta_e_na_resposta_sem_acento(
    env: Env, seeded: factories.PanelWorld  # noqa: F811
) -> None:
    asyncio.run(_add(env.dsn, "Qual é o prazo de entrega?", brand="plant_power"))
    with env.client() as c:
        login(c)
        by_question = c.get(URL, params={"q": "numero 17"}).text
        by_answer = c.get(URL, params={"q": "RESPOSTA de teste 3."}).text
        accent = c.get(URL, params={"q": "É O PRAZO"}).text
        plain = c.get(URL, params={"q": "e o prazo"}).text
    assert _rows(by_question) == 1 and "numero 17?" in by_question
    assert _rows(by_answer) == 1
    assert _rows(accent) == 1 and _rows(plain) == 1
    assert 'value="É O PRAZO"' in accent


def test_busca_trata_curingas_e_html_como_texto(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        pct = c.get(URL, params={"q": "%"}).text
        under = c.get(URL, params={"q": "_"}).text
        xss = c.get(URL, params={"q": '"><script>x</script>'}).text
    for html in (pct, under, xss):
        assert _rows(html) == 0 and "Nenhum item encontrado para os filtros aplicados." in html
    assert "<script>x</script>" not in xss


def test_desativadas_e_reativar(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    ids = list(seeded.faq.ids[:3])
    asyncio.run(
        _sql(
            env.dsn,
            "UPDATE content.faq_items SET active = false WHERE id = ANY($1::bigint[])",
            ids,
        )
    )
    with env.client() as c:
        login(c)
        actives = c.get(URL).text
        inactive = c.get(URL, params={"inativas": "1"}).text
        empty = c.get(URL, params={"inativas": "1", "q": "inexistente"}).text
    assert "22 itens ativos" in actives and "Desativar</button>" in actives
    assert "Reativar" not in actives and ">Desativadas</a>" in actives
    assert _rows(inactive) == 3 and "Reativar</button>" in inactive
    assert "Desativar</button>" not in inactive
    assert "voltar para as ativas" in inactive and 'name="inativas" value="1"' in inactive
    assert "Nenhum item encontrado para os filtros aplicados." in empty


def test_desativadas_vazio(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"inativas": "1"}).text
    assert "Nenhuma linha desativada." in html


def test_numeracao_continua_entre_paginas_e_pagina_alem_do_fim(
    env: Env, seeded: factories.PanelWorld  # noqa: F811
) -> None:
    with env.client() as c:
        login(c)
        p2 = c.get(URL, params={"page": "2"}, headers=HX).text
        beyond = c.get(URL, params={"page": "99"}, headers=HX).text
        junk = c.get(URL, params={"page": "abc", "brand": "xx", "category": "ZZ"}).text
    assert _numbers(p2) == list(range(11, 21)) and "Página 2 de 3" in p2
    assert _numbers(beyond) == list(range(21, 26)) and "Página 3 de 3" in beyond
    assert "Anterior" in beyond and "Próxima" not in beyond
    assert _rows(junk) == 10 and "25 itens ativos" in junk


def test_item_sem_resposta_e_variacoes(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        p3 = c.get(URL, params={"page": "3"}, headers=HX).text
        p1 = c.get(URL).text
    assert "o Jarvis não responde" in p3  # item 25 (i=24)... e o 20 em p2; 10o item sem resposta
    assert "+ 1 variação" in p1


def test_fragmento_htmx_traz_resultado_e_acoes_oob(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"q": "numero"}, headers=HX)
    assert r.status_code == 200
    assert "<html" not in r.text and "<form" not in r.text
    assert 'id="faq-acoes"' in r.text and 'hx-swap-oob="true"' in r.text
    assert 'hx-trigger="faqChanged from:body"' in r.text
    assert r.headers["vary"] == "HX-Request"


def test_botoes_apontam_para_rotas_das_outras_tarefas(
    env: Env, seeded: factories.PanelWorld  # noqa: F811
) -> None:
    with env.client() as c:
        login(c)
        html = c.get(URL).text
    first = seeded.faq.ids[0]
    assert 'hx-get="/dashboard/faq/nova"' in html and 'hx-get="/dashboard/faq/importar"' in html
    assert f'hx-get="/dashboard/faq/{first}/editar"' in html
    assert f'hx-post="/dashboard/faq/{first}/desativar"' in html


def test_conteudo_do_banco_e_escapado(env: Env, seeded: factories.PanelWorld) -> None:  # noqa: F811
    asyncio.run(_add(env.dsn, "<script>alert(1)</script> pergunta"))
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"q": "pergunta"}).text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_banco_sem_faq_nao_quebra(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert "Nenhum item ativo na base de FAQ." in r.text
    assert "0 itens ativos na FAQ" in r.text
