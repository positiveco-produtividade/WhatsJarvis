"""Pagina Pessoas, tres abas (PN-31, PN-43; task PN-T33) contra Postgres real."""
from __future__ import annotations

import asyncio
import re

import asyncpg
import pytest

from tests.panel.factories import BRAND_A, insert_user
from tests.panel.integration.test_access import NOW, Env, env, login  # noqa: F401
from tests.panel.integration.test_people_read import add_attempt, set_display, set_status

pytestmark = pytest.mark.db

URL = "/dashboard/pessoas"
TABS = ("autorizadas", "pedindo", "retiradas")
HX = {"HX-Request": "true"}
SECRET_TEXT = "texto secreto da primeira mensagem"


async def _seed(dsn: str, *, authorized: int = 3, removed: int = 2, pending: int = 2) -> list[int]:
    conn = await asyncpg.connect(dsn)
    try:
        ids: list[int] = []
        for i in range(authorized):
            ids.append(
                await insert_user(
                    conn, name=f"Pessoa Ativa {i:03d}", seq=i + 1, brands=[BRAND_A], now=NOW
                )
            )
        for i in range(removed):
            uid = await insert_user(
                conn, name=f"Pessoa Retirada {i}", seq=500 + i, brands=[BRAND_A], now=NOW
            )
            await set_status(conn, uid, "revoked", NOW)
            ids.append(uid)
        for i in range(pending):
            await add_attempt(conn, i + 1, NOW, count=3 + i, body=SECRET_TEXT)
        return ids
    finally:
        await conn.close()


async def _display(dsn: str, value: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await set_display(conn, value, NOW)
    finally:
        await conn.close()


def _tbody(html: str) -> str:
    return html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]


def _rows(html: str) -> int:
    return _tbody(html).count("<tr ")


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        r = c.get(URL)
    assert r.status_code == 302
    assert "accounts.positiveco.com.br/login" in r.headers["location"]


def test_pagina_completa_com_tres_abas_e_contagens(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        r = c.get(URL)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "<title>Pessoas autorizadas" in html and 'aria-current="page"' in html
    for tab in ("Autorizadas", "Pedindo acesso", "Retiradas"):
        assert tab in html
    tabs = re.findall(r'role="tab" aria-selected="(\w+)"', html)
    assert tabs == ["true", "false", "false"]
    assert ">3</span>" in html and ">2</span>" in html
    assert "3 de 3" in html
    assert _rows(html) == 3
    for header in ("Nome", "Telefone", "Perfil", "UF", "Aceite do aviso"):
        assert f">{header}</th>" in html
    assert "Importar CSV" in html and "+ Cadastrar" in html
    assert 'id="pessoas-resultado"' in html


def test_telefone_sempre_mascarado(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        pages = [c.get(URL, params={"aba": a}).text for a in TABS]
    for html in pages:
        assert "••••" in html
        assert "55119000" not in html and "55859000" not in html
        assert not re.search(r"\+55 11 9\d{4}-\d{4}", html)


def test_cinquenta_por_pagina_e_x_de_y(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, authorized=60, removed=0, pending=0))
    with env.client() as c:
        login(c)
        p1 = c.get(URL).text
        p2 = c.get(URL, params={"page": "2"}, headers=HX).text
    assert _rows(p1) == 50 and "Página 1 de 2" in p1 and "60 itens total" in p1
    assert "60 de 60" in p1
    assert _rows(p2) == 10 and "Página 2 de 2" in p2
    assert "<html" not in p2 and 'id="pessoas-resultado"' not in p2


def test_busca_mostra_x_de_y_com_total_sem_busca(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn, authorized=12, removed=2, pending=0))
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"q": "ativa 007"}, headers=HX).text
        none = c.get(URL, params={"q": "zzzz"}, headers=HX).text
    assert _rows(html) == 1 and "Pessoa Ativa 007" in html
    assert "1 de 12" in html
    assert "Limpar" in html
    assert _rows(none) == 0 and "Ninguém encontrado para “zzzz”." in none
    assert "0 de 12" in none


def test_aba_retiradas_mostra_motivo_e_reativar(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"aba": "retiradas"}).text
    assert _rows(html) == 2
    assert "Saiu da empresa" in html and "Reativar" in html
    assert html.count("/excluir") == 2  # botao Excluir em cada linha (decisao 2026-10-27)
    assert "2 de 2" in html


def test_aba_pedindo_destaca_tentativas_e_oculta_texto_por_padrao(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        html = c.get(URL, params={"aba": "pedindo"}).text
    assert _rows(html) == 2 and "⚠" in html
    assert "Autorizar" in html and "/dashboard/pessoas/nova?pedido=" in html
    assert SECRET_TEXT not in html
    assert "Texto · 34 caracteres" in html


def test_aba_pedindo_mostra_texto_so_com_a_chave_ligada(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    asyncio.run(_display(env.dsn, True))
    with env.client() as c:
        login(c)
        pedindo = c.get(URL, params={"aba": "pedindo"}).text
        outras = [c.get(URL, params={"aba": a}).text for a in ("autorizadas", "retiradas")]
    assert SECRET_TEXT in pedindo
    assert all(SECRET_TEXT not in html for html in outras)


def test_estados_vazios(env: Env) -> None:  # noqa: F811
    with env.client() as c:
        login(c)
        pages = {a: c.get(URL, params={"aba": a}).text for a in TABS}
    assert "Nenhuma pessoa autorizada ainda." in pages["autorizadas"]
    assert "Nenhum número pedindo acesso." in pages["pedindo"]
    assert "Nenhuma pessoa retirada." in pages["retiradas"]
    assert all("0 de 0" in html for html in pages.values())


def test_parametros_invalidos_caem_nos_padroes(env: Env) -> None:  # noqa: F811
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        r = c.get(URL, params={"aba": "xpto", "page": "abc"})
        far = c.get(URL, params={"page": "99"}, headers=HX)
    assert r.status_code == 200 and _rows(r.text) == 3
    assert far.status_code == 200 and "Nenhuma pessoa autorizada ainda." in far.text
