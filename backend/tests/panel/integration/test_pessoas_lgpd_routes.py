"""LGPD na ficha: exportar e excluir (PN-56, PN-57, PN-4; task PN-T40) contra Postgres."""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel.factories import BRAND_A, insert_user
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

HX = {"HX-Request": "true"}
BASE = "/dashboard/pessoas"
NAME = "Pessoa Teste 1"


async def _add(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return int(await insert_user(conn, name=NAME, seq=1, brands=[BRAND_A], now=NOW))
    finally:
        await conn.close()


def add(env: Env) -> int:
    return asyncio.run(_add(env.dsn))


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, token: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(token), **HX})


def _name(env: Env, uid: int) -> str:
    return str(sql(env.dsn, "SELECT name FROM access.users WHERE id = $1", uid))


def _audit(env: Env, action: str) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = $1", action))


# ---- bloco na ficha ------------------------------------------------------------------------


def test_ficha_traz_exportar_e_excluir(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
    assert f'action="{BASE}/{uid}/exportar"' in html and "Exportar dados" in html
    assert f'hx-get="{BASE}/{uid}/excluir"' in html and "Excluir dados" in html


# ---- exportar (PN-56) ----------------------------------------------------------------------


def test_exportar_baixa_json_sem_cache_e_audita(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/exportar", _token(c))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, private"
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment; filename=") and f"dados-pessoa-{uid}-" in disposition
    assert NAME not in disposition and "+55" not in disposition
    assert r.headers["content-type"].startswith("application/json")
    assert json.loads(r.content)["person"]["name"] == NAME
    assert _audit(env, "lgpd.export") == 1


def test_nenhum_get_exporta_ou_exclui(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        gets = [
            c.get(f"{BASE}/{uid}/exportar", headers=HX),
            c.get(f"{BASE}/{uid}/excluir", headers=HX),
            c.get(f"{BASE}/{uid}", headers=HX),
        ]
    assert gets[0].status_code == 405
    assert gets[1].status_code == 200 and "confirm_name" in gets[1].text
    assert gets[2].status_code == 200
    assert _audit(env, "lgpd.export") == 0 and _audit(env, "lgpd.delete") == 0
    assert _name(env, uid) == NAME


def test_exportar_sem_csrf_e_negado(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = c.post(f"{BASE}/{uid}/exportar", headers=HX)
    assert r.status_code == 403 and _audit(env, "lgpd.export") == 0


def test_exportar_inexistente_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/99999/exportar", _token(c))
    assert r.status_code == 404


def test_exportar_exige_sessao(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        r = c.post(f"{BASE}/{uid}/exportar", follow_redirects=False)
    assert r.status_code in (302, 401, 403) and _audit(env, "lgpd.export") == 0


# ---- excluir (PN-57) -----------------------------------------------------------------------


def test_modal_excluir_pede_o_nome(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/{uid}/excluir", headers=HX)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    assert NAME in r.text and 'name="confirm_name"' in r.text and "não pode ser desfeito" in r.text


def test_modal_excluir_inexistente_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/99999/excluir", headers=HX)
    assert r.status_code == 404


@pytest.mark.parametrize("typed", ["", "pessoa teste 1", "Pessoa Teste", NAME + " ", "Outro"])
def test_excluir_com_nome_errado_nao_escreve_nada(env: Env, typed: str) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/excluir", _token(c), {"confirm_name": typed})
    assert r.status_code == 200 and "não confere" in r.text
    assert "HX-Trigger" not in r.headers
    assert _name(env, uid) == NAME
    assert _audit(env, "lgpd.delete") == 0
    assert str(sql(env.dsn, "SELECT status FROM access.users WHERE id = $1", uid)) == "active"


def test_excluir_sem_o_campo_nao_escreve_nada(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/excluir", _token(c))
    assert r.status_code == 200 and _name(env, uid) == NAME


def test_excluir_com_nome_certo_anonimiza_e_audita(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/excluir", _token(c), {"confirm_name": NAME})
    assert r.status_code == 200 and r.text == ""
    assert r.headers["HX-Reswap"] == "none"
    events = json.loads(r.headers["HX-Trigger"])
    assert "pessoasChanged" in events and "closeOverlays" in events
    assert events["toast"]["kind"] == "ok"
    assert _name(env, uid) == f"anon:{uid}"
    assert str(sql(env.dsn, "SELECT status FROM access.users WHERE id = $1", uid)) == "revoked"
    assert _audit(env, "lgpd.delete") == 1


def test_excluir_duas_vezes_so_avisa(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        _post(c, f"{BASE}/{uid}/excluir", token, {"confirm_name": NAME})
        again = _post(c, f"{BASE}/{uid}/excluir", token, {"confirm_name": f"anon:{uid}"})
        modal = c.get(f"{BASE}/{uid}/excluir", headers=HX)
        export = _post(c, f"{BASE}/{uid}/exportar", token)
    assert again.status_code == 200
    assert json.loads(again.headers["HX-Trigger"])["toast"]["kind"] == "aviso"
    assert modal.status_code == 409 and export.status_code == 409
    assert _audit(env, "lgpd.delete") == 1 and _audit(env, "lgpd.export") == 0


def test_excluir_sem_csrf_e_negado(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = c.post(f"{BASE}/{uid}/excluir", data={"confirm_name": NAME}, headers=HX)
    assert r.status_code == 403
    assert _name(env, uid) == NAME and _audit(env, "lgpd.delete") == 0


def test_excluir_inexistente_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/99999/excluir", _token(c), {"confirm_name": NAME})
    assert r.status_code == 404


# ---- administrador nao pode ser excluido (decisao do Caio, 2026-10-26) ---------------------


def test_admin_nao_pode_ter_dados_excluidos(env: Env) -> None:
    uid = add(env)
    sql(env.dsn, "UPDATE access.users SET profile = 'administrador' WHERE id = $1", uid)
    with env.client() as c:
        login(c)
        token = _token(c)
        ficha = c.get(f"{BASE}/{uid}", headers=HX).text
        modal = c.get(f"{BASE}/{uid}/excluir", headers=HX)
        post = _post(c, f"{BASE}/{uid}/excluir", token, {"confirm_name": NAME})
    assert f'hx-get="{BASE}/{uid}/excluir"' not in ficha
    assert "Dados de administrador não podem ser excluídos" in ficha
    assert modal.status_code == 403 and post.status_code == 403
    assert _name(env, uid) == NAME and _audit(env, "lgpd.delete") == 0


def test_excluida_nao_mostra_reativar_e_servidor_recusa(env: Env) -> None:
    """Depois de excluir os dados, a ficha nao oferece Reativar e o POST nao reativa."""
    uid = add(env)
    with env.client() as c:
        login(c)
        token = _token(c)
        _post(c, f"{BASE}/{uid}/excluir", token, {"confirm_name": NAME})
        ficha = c.get(f"{BASE}/{uid}", headers=HX).text
        r = _post(c, f"{BASE}/{uid}/reativar", token)
    assert f"{BASE}/{uid}/reativar" not in ficha and "Reenviar aviso" not in ficha
    assert r.status_code == 404
    assert str(sql(env.dsn, "SELECT status FROM access.users WHERE id = $1", uid)) == "revoked"
