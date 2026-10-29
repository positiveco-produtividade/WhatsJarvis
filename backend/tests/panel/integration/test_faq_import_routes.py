"""Importar FAQ: telas e rotas (PN-27, PN-28, PN-29, PN-4; task PN-T30) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import io
import re
import uuid
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]

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

BASE = "/dashboard/faq/importar"
HX = {"HX-Request": "true"}
CSV = (
    "pergunta;resposta;categoria;bot responde\n"
    "Qual o sabor do novo?;Chocolate.;Produto;sim\n"
    "Quanto custa o novo?;;Preço;não\n"
    "Tem link?;veja https://x.com;Produto;sim\n"
    "Pergunta de teste numero 1?;Repetida.;Produto;sim\n"
    "Sem categoria?;Ok.;;sim\n"
)


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


def _upload(
    c: TestClient,
    token: str,
    content: bytes | None = CSV.encode("utf-8"),
    name: str = "faq.csv",
    brand: str = "jungle",
    headers: dict[str, str] | None = None,
) -> Any:
    files = (
        {"file": (name, content or b"", "application/octet-stream")}
        if content is not None
        else None
    )
    return c.post(
        BASE,
        data={"brand": brand},
        files=files,
        headers={**post_headers(token), **(headers or {})},
    )


def _batch_id(r: Any) -> str:
    location = r.headers["location"]
    match = re.fullmatch(rf"{BASE}/([0-9a-f-]{{36}})", location)
    assert match, location
    return match.group(1)


def _post(c: TestClient, url: str, token: str, data: dict[str, Any] | None = None) -> Any:
    return c.post(url, data=data or {"x": "1"}, headers=post_headers(token))


def _faq_count(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM content.faq_items"))


def _status(env: Env, batch_id: str) -> str:
    return str(
        sql(env.dsn, "SELECT status FROM ops.import_batches WHERE id = $1", uuid.UUID(batch_id))
    )


def _xlsx(rows: list[list[str]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    assert ws is not None
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---- acesso (PN-4) ---------------------------------------------------------------------


def test_sem_sessao_nenhuma_rota_responde(env: Env, seeded: factories.PanelWorld) -> None:
    lote = uuid.uuid4()
    with env.client() as c:
        for url in (BASE, BASE + "/modelo", f"{BASE}/{lote}"):
            r = c.get(url)
            assert r.status_code == 302 and "accounts.positiveco.com.br" in r.headers["location"]
        for url in (BASE, f"{BASE}/{lote}/gravar", f"{BASE}/{lote}/cancelar"):
            assert c.post(url, data={}).status_code == 302


def test_post_sem_csrf_ou_origin_e_recusado(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        files = {"file": ("faq.csv", CSV.encode(), "text/csv")}
        r = c.post(BASE, data={"brand": "jungle"}, files=files, headers={"Origin": ORIGIN})
        assert r.status_code == 403
        r = c.post(BASE, data={"brand": "jungle"}, files=files, headers={"X-CSRF-Token": token})
        assert r.status_code == 403
        lote = uuid.uuid4()
        for url in (f"{BASE}/{lote}/gravar", f"{BASE}/{lote}/cancelar"):
            assert c.post(url, data={}, headers={"Origin": ORIGIN}).status_code == 403
    assert sql(env.dsn, "SELECT count(*) FROM ops.import_batches") == 0


# ---- modal e modelo ---------------------------------------------------------------------


def test_modal_tem_os_campos_e_o_link_do_modelo(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "zaya", domain="jarvis.positiveco.com.br")
        r = c.get(BASE)
    html = r.text
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    for needle in (
        "Importar planilha",
        'name="file"',
        'name="brand"',
        'name="csrf"',
        'enctype="multipart/form-data"',
        f'href="{BASE}/modelo"',
        "Até 200 perguntas e 2 MB.",
        "Conferir antes de gravar",
        '<option value="zaya" selected>',
    ):
        assert needle in html
    assert 'value="todas"' not in html


def test_modelo_baixa_xlsx_valido(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        r = c.get(BASE + "/modelo")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert "faq-modelo-jarvis.xlsx" in r.headers["content-disposition"]
    sheet = load_workbook(io.BytesIO(r.content)).worksheets[0]
    header = [c.value for c in next(sheet.iter_rows(max_row=1))]
    assert header == ["pergunta", "resposta", "categoria", "bot responde"]


# ---- enviar e conferir (PN-27, PN-28) ---------------------------------------------------


def test_enviar_cria_lote_sem_gravar_e_vai_a_conferencia(
    env: Env, seeded: factories.PanelWorld
) -> None:
    before = _faq_count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        r = _upload(c, token)
        assert r.status_code == 303
        lote = _batch_id(r)
        page = c.get(r.headers["location"])
    assert _faq_count(env) == before
    assert _status(env, lote) == "preview"
    html = page.text
    assert page.status_code == 200 and "Nada foi gravado ainda." in html
    assert "Gravar 3 perguntas" in html  # sabor, preco (sem resposta), sem categoria
    for needle in (
        "vão entrar na FAQ",
        "já existem e ficam de fora",
        "com erro, ficam de fora",
        "Já existe na FAQ.",
        "Texto com link.",
        "sem resposta — vai para a equipe",
        "faq.csv",
        "marca <strong>Jungle</strong>",
        'name="cat_2"',
        "confira",
    ):
        assert needle in html
    assert 'name="cat_4"' not in html and 'name="cat_5"' not in html  # erro e fica de fora


def test_enviar_pelo_htmx_responde_hx_redirect(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c), headers=HX)
    assert r.status_code == 200
    assert re.fullmatch(rf"{BASE}/[0-9a-f-]{{36}}", r.headers["hx-redirect"])


def test_enviar_xlsx(env: Env, seeded: factories.PanelWorld) -> None:
    data = _xlsx([["Pergunta", "Resposta"], ["Tem glúten?", "Não tem."]])
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c), data, "faq.xlsx")
        page = c.get(r.headers["location"])
    assert "Gravar 1 pergunta" in page.text and "Tem glúten?" in page.text


@pytest.mark.parametrize(
    ("content", "name", "needle"),
    [
        (b"a;b\n1;2\n", "faq.csv", "A planilha precisa das colunas pergunta e resposta"),
        (b"%PDF", "faq.pdf", "Envie um arquivo .xlsx ou .csv."),
        (b"nao e zip", "faq.xlsx", "Não consegui ler a planilha."),
        (b"x" * (2 * 1024 * 1024 + 1), "faq.csv", "O arquivo passa de 2 MB."),
        (b"pergunta;resposta\n", "faq.csv", "A planilha não tem nenhuma pergunta."),
        (
            ("pergunta;resposta\n" + "p;r\n" * 201).encode(),
            "faq.csv",
            "passa do limite de 200 perguntas",
        ),
    ],
    ids=["sem-colunas", "extensao", "xlsx-quebrado", "2mb", "vazia", "201-linhas"],
)
def test_arquivo_recusado_volta_o_formulario_com_mensagem_e_link(
    env: Env, seeded: factories.PanelWorld, content: bytes, name: str, needle: str
) -> None:
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c), content, name, headers=HX)
    html = r.text
    assert r.status_code == 200 and "hx-redirect" not in r.headers
    assert 'role="alert"' in html and needle in html
    assert f'href="{BASE}/modelo" class="underline font-medium"' in html
    assert sql(env.dsn, "SELECT count(*) FROM ops.import_batches") == 0


def test_sem_arquivo_ou_marca_invalida(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        none = _upload(c, token, None)
        empty = _upload(c, token, b"", "faq.csv")
        brand = _upload(c, token, brand="marca-x")
        all_brands = _upload(c, token, brand="todas")
    assert "Escolha um arquivo" in none.text and "Escolha um arquivo" in empty.text
    assert "Marca inválida." in brand.text
    assert "Marca inválida." in all_brands.text
    assert sql(env.dsn, "SELECT count(*) FROM ops.import_batches") == 0


# ---- conferencia reabrivel --------------------------------------------------------------


def test_conferencia_reabre_enquanto_o_lote_vale(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        url = _upload(c, _token(c)).headers["location"]
        first, second = c.get(url), c.get(url)
    assert first.text == second.text and "Gravar 3 perguntas" in first.text


def test_conferencia_inexistente_ou_malformada_da_404(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        for lote in (str(uuid.uuid4()), "nao-e-uuid", "modelo-x"):
            assert c.get(f"{BASE}/{lote}").status_code == 404


def test_conferencia_vencida_mostra_aviso_e_apaga_nada_da_faq(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        url = _upload(c, _token(c)).headers["location"]
        sql(env.dsn, "UPDATE ops.import_batches SET expires_at = $1", NOW)
        page = c.get(url)
    assert page.status_code == 200 and "venceu" in page.text and "<table" not in page.text


# ---- gravar (PN-29) ---------------------------------------------------------------------


def test_gravar_com_categoria_escolhida_na_tela(env: Env, seeded: factories.PanelWorld) -> None:
    before = _faq_count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        r = _post(c, f"{BASE}/{lote}/gravar", token, {"cat_2": "MATERIAL", "cat_6": "PRECO"})
        assert r.status_code == 303
        assert r.headers["location"] == f"{BASE}/{lote}?gravadas=3&fora=2"
        page = c.get(r.headers["location"])
    assert _faq_count(env) == before + 3
    assert _status(env, lote) == "committed"
    rows = sql(
        env.dsn,
        "SELECT category FROM content.faq_items WHERE origin = 'import' AND question = $1",
        "Qual o sabor do novo?",
    )
    assert rows == "MATERIAL"
    q = "SELECT category FROM content.faq_items WHERE question = $1"
    assert sql(env.dsn, q, "Sem categoria?") == "PRECO"
    assert sql(env.dsn, q, "Quanto custa o novo?") == "PRECO"
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'faq.import'") == 1
    assert "3 perguntas importadas" in page.text and "2 ficaram de fora" in page.text
    assert "<table" not in page.text


def test_gravar_duas_vezes_nao_duplica(env: Env, seeded: factories.PanelWorld) -> None:
    before = _faq_count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        _post(c, f"{BASE}/{lote}/gravar", token)
        again = _post(c, f"{BASE}/{lote}/gravar", token)
        page = c.get(again.headers["location"])
    assert again.status_code == 303 and again.headers["location"] == f"{BASE}/{lote}"
    assert _faq_count(env) == before + 3
    assert "já foi gravada" in page.text


def test_categoria_invalida_nao_grava_e_volta_a_conferencia(
    env: Env, seeded: factories.PanelWorld
) -> None:
    before = _faq_count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        r = _post(c, f"{BASE}/{lote}/gravar", token, {"cat_2": "INVENTADA"})
        retry = _post(c, f"{BASE}/{lote}/gravar", token, {"cat_9": "PRECO"})
    assert r.status_code == 200 and "Categoria inválida na linha 2." in r.text
    assert "Gravar 3 perguntas" in r.text  # segue em conferencia
    assert "não bate com a importação" in retry.text
    assert _faq_count(env) == before and _status(env, lote) == "preview"


def test_gravar_lote_inexistente_ou_malformado(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        bad = _post(c, f"{BASE}/nao-e-uuid/gravar", token)
        missing = _post(c, f"{BASE}/{uuid.uuid4()}/gravar", token)
    assert bad.status_code == 404
    assert missing.status_code == 303


# ---- cancelar ---------------------------------------------------------------------------


def test_cancelar_descarta_e_nao_deixa_gravar(env: Env, seeded: factories.PanelWorld) -> None:
    before = _faq_count(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        r = _post(c, f"{BASE}/{lote}/cancelar", token)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard/faq"
        late = _post(c, f"{BASE}/{lote}/gravar", token)
        page = c.get(f"{BASE}/{lote}")
        again = _post(c, f"{BASE}/{lote}/cancelar", token)
    assert _status(env, lote) == "cancelled"
    assert late.status_code == 303 and again.status_code == 303
    assert "cancelada" in page.text and "<table" not in page.text
    assert _faq_count(env) == before


def test_cancelar_nao_mexe_em_lote_de_pessoas(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        other = uuid.uuid4()
        sql(
            env.dsn,
            "INSERT INTO ops.import_batches (id, kind, actor, filename, sha256, size_bytes, rows, "
            "summary, status, created_at, expires_at) VALUES ($1, 'people', 'a@x.test', 'p.csv', "
            "'h', 1, '[]'::jsonb, '{}'::jsonb, 'preview', $2, $3)",
            other,
            NOW,
            NOW.replace(hour=23),
        )
        r = _post(c, f"{BASE}/{other}/cancelar", token)
        page = c.get(f"{BASE}/{other}")
    assert r.status_code == 303 and page.status_code == 404
    assert _status(env, str(other)) == "preview"
