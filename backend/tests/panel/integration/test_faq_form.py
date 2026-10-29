"""Formulario da FAQ e acoes (PN-23, PN-24, PN-26, PN-4; task PN-T27) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    ORIGIN,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

NEW = "/dashboard/faq/nova"
HX = {"HX-Request": "true"}


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:
    world = asyncio.run(_seed(env.dsn))
    if not world.faq.available:
        pytest.skip("content.faq_items inexistente")
    return world


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, token: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(token), **HX})


def _fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "brand": "jungle",
        "category": "PRODUTO",
        "question": "Qual o sabor do produto novo?",
        "answer": "Tem sabor de baunilha.",
        "bot_responde": "1",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


def _count(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM content.faq_items"))


def _first_id(world: factories.PanelWorld) -> int:
    return world.faq.ids[0]


async def _row(dsn: str, question: str) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM content.faq_items WHERE question = $1", question)
        assert row is not None
        return row
    finally:
        await conn.close()


# ---- acesso (PN-4) ---------------------------------------------------------------------


def test_sem_sessao_nenhuma_rota_responde(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        for url in (NEW, f"/dashboard/faq/{fid}/editar"):
            r = c.get(url)
            assert r.status_code == 302 and "accounts.positiveco.com.br" in r.headers["location"]
        r = c.post(f"/dashboard/faq/{fid}/desativar", data={})
        assert r.status_code == 302


def test_post_sem_csrf_ou_origin_e_recusado(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    before = _count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        r = c.post(NEW, data=_fields(), headers={"Origin": ORIGIN})
        assert r.status_code == 403
        r = c.post(NEW, data=_fields(), headers={"X-CSRF-Token": token})
        assert r.status_code == 403
        r = c.post(f"/dashboard/faq/{fid}/desativar", data={}, headers={"Origin": ORIGIN})
        assert r.status_code == 403
    assert _count(env) == before
    assert sql(env.dsn, "SELECT active FROM content.faq_items WHERE id = $1", fid) is True


# ---- formulario novo --------------------------------------------------------------------


def test_formulario_novo_vazio_com_campos_do_prototipo(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        r = c.get(NEW)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "Adicionar pergunta" in html and f'action="{NEW}"' in html
    for needle in (
        'name="brand"',
        'name="category"',
        'name="question"',
        'name="answer"',
        "Variações da resposta",
        "O Jarvis responde sozinho.",
        "Todas as marcas",
        'name="csrf"',
        ">Criar</button>",
    ):
        assert needle in html
    assert 'name="version"' not in html and "Preenchido a partir" not in html


def test_formulario_usa_a_marca_do_cookie(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "zaya", domain="jarvis.positiveco.com.br")
        html = c.get(NEW).text
    assert re.search(r'<option value="zaya" selected>', html)


def test_preenche_pelo_ticket(env: Env, seeded: factories.PanelWorld) -> None:
    ticket = seeded.tickets[4]  # escalado PRECO
    with env.client() as c:
        login(c)
        r = c.get(NEW, params={"ticket": ticket.ticket_number})
    html = r.text
    assert f"Pergunta de teste {ticket.ticket_number}" in html
    assert f'<strong class="font-mono">{ticket.ticket_number}</strong>' in html
    assert '<option value="PRECO" selected>' in html


def test_ticket_inexistente_ou_texto_na_url_nao_preenche(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        for value in ("J-999999", "Qual o preco do produto X?", "<script>1</script>"):
            r = c.get(NEW, params={"ticket": value})
            assert r.status_code == 200
            assert "Preenchido a partir" not in r.text
            assert "Qual o preco do produto X?" not in r.text
            assert "<script>1" not in r.text


# ---- criar (PN-23) ----------------------------------------------------------------------


def test_criar_grava_versao_auditoria_e_avisa(env: Env, seeded: factories.PanelWorld) -> None:
    before = _count(env)
    with env.client() as c:
        login(c)
        r = _post(c, NEW, _token(c), _fields(variations=["Outra forma.", "  ", "Mais uma."]))
    assert r.status_code == 200 and r.text == ""
    assert r.headers["HX-Reswap"] == "none"
    events = _events(r)
    assert {"faqChanged", "toast", "closeOverlays"} <= set(events)
    assert events["toast"]["kind"] == "ok"
    assert _count(env) == before + 1
    row = asyncio.run(_row(env.dsn, "Qual o sabor do produto novo?"))
    assert row["origin"] == "manual" and row["version"] == 1 and row["active"] is True
    assert list(row["variations"]) == ["Outra forma.", "Mais uma."]
    versions = sql(
        env.dsn, "SELECT count(*) FROM content.faq_item_versions WHERE item_id = $1", row["id"]
    )
    assert versions == 1
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'faq.create'") == 1


def test_criar_invalido_lista_todos_os_problemas_e_mantem_o_que_foi_digitado(
    env: Env, seeded: factories.PanelWorld
) -> None:
    before = _count(env)
    data = _fields(
        question="  ",
        answer="Ligue para (11) 91234-5678 ou veja https://x.example",
        variations=["fale@exemplo.com"],
    )
    with env.client() as c:
        login(c)
        r = _post(c, NEW, _token(c), data)
    assert r.status_code == 200 and "HX-Trigger" not in r.headers
    html = r.text
    assert "Não salvei" in html
    assert "A pergunta é obrigatória." in html
    assert "Não coloque telefone no texto." in html
    assert "Não coloque link no texto." in html
    assert "Não coloque e-mail no texto." in html
    assert "fale@exemplo.com" in html and "(11) 91234-5678" in html
    assert _count(env) == before


def test_criar_sem_resposta_exige_resposta_se_o_bot_responde(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        bad = _post(c, NEW, token, _fields(answer=""))
        ok = _post(c, NEW, token, _fields(answer="", bot_responde=None))
    assert "Escreva a resposta ou desmarque" in bad.text
    assert ok.status_code == 200 and "faqChanged" in _events(ok)
    row = asyncio.run(_row(env.dsn, "Qual o sabor do produto novo?"))
    assert row["bot_responde"] is False and row["answer"] is None


def test_criar_duplicada_na_marca(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        first = _post(c, NEW, token, _fields())
        second = _post(c, NEW, token, _fields(question="QUAL O SABOR do produto  novo?"))
        other = _post(c, NEW, token, _fields(brand="zaya"))
    assert "faqChanged" in _events(first)
    assert "Já existe uma pergunta ativa igual nesta marca." in second.text
    assert "faqChanged" in _events(other)


# ---- editar (PN-23, PN-26) --------------------------------------------------------------


def test_formulario_de_edicao_traz_o_item_e_a_versao(
    env: Env, seeded: factories.PanelWorld
) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        r = c.get(f"/dashboard/faq/{fid}/editar")
    html = r.text
    assert r.status_code == 200 and "Editar pergunta" in html
    assert 'name="version" value="1"' in html and f'action="/dashboard/faq/{fid}/editar"' in html
    assert "Pergunta de teste numero 1?" in html and "Resposta de teste 1." in html
    assert "variacao 1" in html and ">Salvar</button>" in html
    assert "Hoje ela vale para as cinco marcas" in html  # o item 1 e de "todas"


def test_salvar_incrementa_versao_e_audita(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        r = _post(
            c,
            f"/dashboard/faq/{fid}/editar",
            _token(c),
            _fields(brand="todas", question="Pergunta nova?", version="1"),
        )
    assert r.status_code == 200 and "closeOverlays" in _events(r)
    assert sql(env.dsn, "SELECT version FROM content.faq_items WHERE id = $1", fid) == 2
    question = sql(env.dsn, "SELECT question FROM content.faq_items WHERE id = $1", fid)
    assert question == "Pergunta nova?"
    versions = sql(
        env.dsn, "SELECT count(*) FROM content.faq_item_versions WHERE item_id = $1", fid
    )
    assert versions == 1  # a semente de teste nao grava a versao 1; a edicao grava a 2
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'faq.update'") == 1


def _other_person_saves_first(c: TestClient, token: str, fid: int) -> None:
    other = _fields(brand="todas", question="Texto da outra pessoa?", version="1")
    _post(c, f"/dashboard/faq/{fid}/editar", token, other)


def test_conflito_mostra_texto_atual_e_nao_grava(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        token = _token(c)
        _other_person_saves_first(c, token, fid)
        mine = _fields(brand="todas", question="Meu texto?", version="1")
        r = _post(c, f"/dashboard/faq/{fid}/editar", token, mine)
    html = r.text
    assert r.status_code == 200 and "HX-Trigger" not in r.headers
    assert "Este item mudou desde que você abriu." in html
    assert "Texto da outra pessoa?" in html  # texto atual no aviso
    assert "Meu texto?" in html  # o que o usuario digitou continua no campo
    assert 'name="force" value="1"' in html and "Gravar mesmo assim" in html
    assert 'name="version" value="1"' in html
    question = sql(env.dsn, "SELECT question FROM content.faq_items WHERE id = $1", fid)
    assert question == "Texto da outra pessoa?"


def test_force_sobrescreve_apenas_com_force_1(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        token = _token(c)
        _other_person_saves_first(c, token, fid)
        data = _fields(brand="todas", question="Meu texto?", version="1")
        semforce = _post(c, f"/dashboard/faq/{fid}/editar", token, data)
        forced = _post(c, f"/dashboard/faq/{fid}/editar", token, {**data, "force": "1"})
    assert "Este item mudou" in semforce.text
    assert forced.status_code == 200 and "faqChanged" in _events(forced)
    assert sql(env.dsn, "SELECT question FROM content.faq_items WHERE id = $1", fid) == "Meu texto?"
    assert sql(env.dsn, "SELECT version FROM content.faq_items WHERE id = $1", fid) == 3


def test_editar_invalido_mantem_formulario_de_edicao(
    env: Env, seeded: factories.PanelWorld
) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        data = _fields(brand="todas", question="", version="1")
        r = _post(c, f"/dashboard/faq/{fid}/editar", _token(c), data)
    assert "A pergunta é obrigatória." in r.text
    assert f'action="/dashboard/faq/{fid}/editar"' in r.text


def test_item_inexistente(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        got = c.get("/dashboard/faq/999999/editar")
        saved = _post(c, "/dashboard/faq/999999/editar", token, _fields(version="1"))
        off = _post(c, "/dashboard/faq/999999/desativar", token)
    for r in (got, saved, off):
        assert r.status_code == 404
        events = _events(r)
        assert events["toast"]["kind"] == "erro" and "faqChanged" in events


# ---- desativar e reativar (PN-24) -------------------------------------------------------


def test_desativar_e_reativar_nao_apagam(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    before = _count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        off = _post(c, f"/dashboard/faq/{fid}/desativar", token)
        assert off.status_code == 200 and off.text == ""
        assert off.headers["HX-Reswap"] == "none"
        assert "faqChanged" in _events(off) and "Desativadas" in _events(off)["toast"]["message"]
        assert sql(env.dsn, "SELECT active FROM content.faq_items WHERE id = $1", fid) is False
        again = _post(c, f"/dashboard/faq/{fid}/desativar", token)
        assert _events(again)["toast"]["kind"] == "aviso"
        on = _post(c, f"/dashboard/faq/{fid}/reativar", token)
        assert "reativada" in _events(on)["toast"]["message"]
    assert _count(env) == before
    assert sql(env.dsn, "SELECT active FROM content.faq_items WHERE id = $1", fid) is True
    kinds = sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action LIKE 'faq.%activate'")
    assert kinds == 2


def test_reativar_com_duplicata_avisa_erro(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        token = _token(c)
        _post(c, f"/dashboard/faq/{fid}/desativar", token)
        dup = _post(c, NEW, token, _fields(brand="todas", question="Pergunta de teste numero 1?"))
        assert "faqChanged" in _events(dup)
        r = _post(c, f"/dashboard/faq/{fid}/reativar", token)
    toast = _events(r)["toast"]
    assert toast["kind"] == "erro" and "Já existe uma pergunta ativa igual" in toast["message"]
    assert sql(env.dsn, "SELECT active FROM content.faq_items WHERE id = $1", fid) is False


def test_lista_aponta_para_as_rotas_do_formulario(env: Env, seeded: factories.PanelWorld) -> None:
    fid = _first_id(seeded)
    with env.client() as c:
        login(c)
        html = c.get("/dashboard/faq").text
    for needle in (
        'hx-get="/dashboard/faq/nova"',
        f'hx-get="/dashboard/faq/{fid}/editar"',
        f'hx-post="/dashboard/faq/{fid}/desativar"',
    ):
        assert needle in html
