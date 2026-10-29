"""Importar pessoas: telas e rotas (PN-40, PN-4; task PN-T38) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.panel.integration.test_access import (  # noqa: F401
    ORIGIN,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

BASE = "/dashboard/pessoas/importar"
HX = {"HX-Request": "true"}
HEADER = "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor"
LINES = [
    "Ana Souza,+5585991231201,vendedor,Loja X,CE|PE,atacarejo;farma,jungle,Bia Lima",
    "Beto Dias,+5511987654321,vendedor,Loja Y,SP,atacarejo,jungle,Bia Lima",
    "Cida Erro,123,vendedor,Loja Z,SP,atacarejo,jungle,Bia Lima",
    "Ana Repetida,+558591231201,vendedor,Loja X,CE,atacarejo,jungle,Bia Lima",
]
CSV = "\n".join([HEADER, *LINES]) + "\n"


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _upload(
    c: TestClient,
    token: str,
    content: bytes | None = CSV.encode("utf-8"),
    name: str = "pessoas.csv",
    headers: dict[str, str] | None = None,
) -> Any:
    files = {"file": (name, content, "text/csv")} if content is not None else None
    merged = {**post_headers(token), **(headers or {})}
    return c.post(BASE, files=files, data={"x": "1"}, headers=merged)


def _batch_id(r: Any) -> str:
    match = re.fullmatch(rf"{BASE}/([0-9a-f-]{{36}})", r.headers["location"])
    assert match, r.headers["location"]
    return match.group(1)


def _post(c: TestClient, url: str, token: str) -> Any:
    return c.post(url, data={"x": "1"}, headers=post_headers(token))


def _people(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM access.users"))


def _row_state(env: Env, batch_id: str) -> tuple[str, int]:
    status = str(
        sql(env.dsn, "SELECT status FROM ops.import_batches WHERE id = $1", uuid.UUID(batch_id))
    )
    size = int(
        sql(
            env.dsn,
            "SELECT jsonb_array_length(rows) FROM ops.import_batches WHERE id = $1",
            uuid.UUID(batch_id),
        )
    )
    return status, size


# ---- acesso (PN-4) ---------------------------------------------------------------------


def test_sem_sessao_nenhuma_rota_responde(env: Env) -> None:
    lote = uuid.uuid4()
    with env.client() as c:
        for url in (BASE, BASE + "/modelo", f"{BASE}/{lote}"):
            r = c.get(url)
            assert r.status_code == 302 and "accounts.positiveco.com.br" in r.headers["location"]
        for url in (BASE, f"{BASE}/{lote}/gravar", f"{BASE}/{lote}/cancelar"):
            assert c.post(url, data={}).status_code == 302


def test_post_sem_csrf_ou_origin_e_recusado(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        files = {"file": ("p.csv", CSV.encode(), "text/csv")}
        assert c.post(BASE, files=files, headers={"Origin": ORIGIN}).status_code == 403
        assert c.post(BASE, files=files, headers={"X-CSRF-Token": token}).status_code == 403
        lote = uuid.uuid4()
        for url in (f"{BASE}/{lote}/gravar", f"{BASE}/{lote}/cancelar"):
            assert c.post(url, data={}, headers={"Origin": ORIGIN}).status_code == 403
    assert sql(env.dsn, "SELECT count(*) FROM ops.import_batches") == 0


# ---- modal e modelo --------------------------------------------------------------------


def test_modal_e_modelo(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(BASE)
        assert r.status_code == 200 and 'name="file"' in r.text and "modelo" in r.text
        m = c.get(BASE + "/modelo")
        assert m.status_code == 200 and "text/csv" in m.headers["content-type"]
        assert "attachment" in m.headers["content-disposition"]
        assert m.content.decode("utf-8-sig").splitlines()[0] == HEADER


# ---- enviar e conferir -----------------------------------------------------------------


def test_envio_cria_lote_sem_cadastrar_e_confere_com_telefone_mascarado(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c))
        assert r.status_code == 303
        lote = _batch_id(r)
        page = c.get(f"{BASE}/{lote}")
    assert page.status_code == 200
    assert "no-store" in page.headers["cache-control"]
    assert "Ana Souza" in page.text and "Nada foi gravado ainda" in page.text
    assert "+5585991231201" not in page.text and "1201" in page.text  # mascarado
    assert "Gravar 2 pessoas" in page.text
    assert "Telefone repetido no arquivo." in page.text
    assert _people(env) == 0
    assert _row_state(env, lote) == ("preview", 4)


def test_envio_htmx_responde_hx_redirect(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c), headers=HX)
    assert r.status_code == 200 and r.headers["hx-redirect"].startswith(BASE + "/")


@pytest.mark.parametrize(
    ("content", "name", "texto"),
    [
        (b"nome,telefone\nA,1\n", "p.csv", "primeira linha precisa ter as colunas"),
        (CSV.encode(), "p.xlsx", "Envie um arquivo .csv"),
        (b"", "p.csv", "Escolha um arquivo .csv"),
        (None, "p.csv", "Escolha um arquivo .csv"),
        (b"\xff\xfe\x00bad", "p.csv", "Não consegui ler"),
        (HEADER.encode() + b"\n", "p.csv", "nenhuma pessoa"),
    ],
)
def test_arquivo_recusado_volta_o_formulario(
    env: Env, content: bytes | None, name: str, texto: str
) -> None:
    with env.client() as c:
        login(c)
        r = _upload(c, _token(c), content=content, name=name)
    assert r.status_code == 200 and texto in r.text and 'name="file"' in r.text
    assert sql(env.dsn, "SELECT count(*) FROM ops.import_batches") == 0


def test_conferencia_de_lote_inexistente_ou_invalido_e_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        assert c.get(f"{BASE}/{uuid.uuid4()}").status_code == 404
        assert c.get(f"{BASE}/nao-e-uuid").status_code == 404
        assert _post(c, f"{BASE}/{uuid.uuid4()}/gravar", _token(c)).status_code == 404


# ---- gravar e cancelar -----------------------------------------------------------------


def test_gravar_cadastra_so_as_validas_e_mostra_relatorio_uma_vez(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        r = _post(c, f"{BASE}/{lote}/gravar", token)
        assert r.status_code == 200
        assert "2 pessoas cadastradas" in r.text
        assert "2 linhas ficaram de fora" in r.text
        assert "Cida Erro" in r.text and "Telefone repetido no arquivo." in r.text
        assert "no-store" in r.headers["cache-control"]
        assert _people(env) == 2
        assert _row_state(env, lote) == ("committed", 0)
        assert int(sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages")) >= 2
        again = _post(c, f"{BASE}/{lote}/gravar", token)
        assert again.status_code == 303 and again.headers["location"] == f"{BASE}/{lote}"
        page = c.get(f"{BASE}/{lote}")
    assert "já foi gravada" in page.text
    assert "Cida Erro" not in page.text and "Ana Souza" not in page.text
    assert _people(env) == 2


def test_gravar_pula_telefone_cadastrado_depois_da_conferencia(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        first = _batch_id(_upload(c, token))
        assert "2 pessoas cadastradas" in _post(c, f"{BASE}/{first}/gravar", token).text
        r = _post(c, f"{BASE}/{lote}/gravar", token)
    assert "Ninguém novo foi cadastrado" in r.text
    assert "Telefone cadastrado enquanto você conferia." in r.text
    assert _people(env) == 2


def test_cancelar_descarta_e_apaga_as_linhas(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        r = _post(c, f"{BASE}/{lote}/cancelar", token)
        assert r.status_code == 303 and r.headers["location"] == "/dashboard/pessoas"
        page = c.get(f"{BASE}/{lote}")
        assert "foi cancelada" in page.text and "Ana Souza" not in page.text
        assert _post(c, f"{BASE}/{lote}/gravar", token).status_code == 303
    assert _row_state(env, lote) == ("cancelled", 0)
    assert _people(env) == 0


def test_lote_vencido_mostra_expirado_e_nao_grava(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        env.clock.advance(minutes=61)
        page = c.get(f"{BASE}/{lote}")
        assert "venceu" in page.text and "Ana Souza" not in page.text
        assert _post(c, f"{BASE}/{lote}/gravar", token).status_code == 303
    assert _people(env) == 0
    assert _row_state(env, lote) == ("expired", 0)


def test_lote_de_faq_nao_abre_aqui(env: Env) -> None:
    with env.client() as c:
        login(c)
        token = _token(c)
        lote = _batch_id(_upload(c, token))
        sql(env.dsn, "UPDATE ops.import_batches SET kind = 'faq' WHERE id = $1", uuid.UUID(lote))
        assert c.get(f"{BASE}/{lote}").status_code == 404
        assert _post(c, f"{BASE}/{lote}/gravar", token).status_code == 404
