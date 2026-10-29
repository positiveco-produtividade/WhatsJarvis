"""Pedindo acesso: ignorar, texto da 1a mensagem e botao da chave (PN-37, PN-38, PN-67; PN-T36)."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.services import attempts
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)
from tests.panel.integration.test_people_read import add_attempt, set_display

pytestmark = pytest.mark.db

HX = {"HX-Request": "true"}
BASE = "/dashboard/pessoas/pedidos"
LIST = "/dashboard/pessoas?aba=pedindo"
SECRET = "texto secreto da primeira mensagem"
KEY = "unknown_first_text_display"


async def _seed(dsn: str, *, count: int = 3) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await add_attempt(conn, 1, NOW, count=count, body=SECRET)
    finally:
        await conn.close()


async def _display(dsn: str, value: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await set_display(conn, value, NOW)
    finally:
        await conn.close()


async def _write_again(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        result = await attempts.record(
            conn, phone_key="5585900000001", bsuid=None, reason="unknown", msg_type="text", now=NOW
        )
        return result.attempt_count
    finally:
        await conn.close()


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, token: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(token), **HX})


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


def _audit(env: Env, action: str) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = $1", action))


def test_ignorar_some_da_lista_audita_e_volta_se_escrever(env: Env) -> None:
    aid = asyncio.run(_seed(env.dsn, count=3))
    with env.client() as c:
        login(c)
        assert "Ignorar" in c.get(LIST, headers=HX).text
        r = _post(c, f"{BASE}/{aid}/ignorar", _token(c))
        assert r.status_code == 200 and r.headers["HX-Reswap"] == "none"
        assert "pessoasChanged" in _events(r)
        assert "Ignorar" not in c.get(LIST, headers=HX).text
    assert _audit(env, "access_attempt.dismiss") == 1
    assert asyncio.run(_write_again(env.dsn)) == 4
    with env.client() as c:
        login(c)
        html = c.get(LIST, headers=HX).text
    assert "Ignorar" in html and ">4" in html.replace(" ", "").replace("\n", "")


def test_ignorar_duas_vezes_avisa_sem_erro(env: Env) -> None:
    aid = asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        tok = _token(c)
        _post(c, f"{BASE}/{aid}/ignorar", tok)
        r = _post(c, f"{BASE}/{aid}/ignorar", tok)
    assert r.status_code == 200 and _events(r)["toast"]["kind"] == "aviso"
    assert _audit(env, "access_attempt.dismiss") == 1


def test_ignorar_exige_csrf_e_sessao(env: Env) -> None:
    aid = asyncio.run(_seed(env.dsn))
    with env.client() as c:
        assert c.post(f"{BASE}/{aid}/ignorar").status_code in (302, 401, 403)
        login(c)
        assert c.post(f"{BASE}/{aid}/ignorar", headers=HX).status_code == 403
    assert _audit(env, "access_attempt.dismiss") == 0


def test_texto_so_com_flag_ligada_e_so_na_aba(env: Env) -> None:
    asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        assert SECRET not in c.get(LIST, headers=HX).text
        asyncio.run(_display(env.dsn, True))
        assert SECRET in c.get(LIST, headers=HX).text
        for aba in ("autorizadas", "retiradas"):
            assert SECRET not in c.get(f"/dashboard/pessoas?aba={aba}", headers=HX).text


def test_botao_mostra_estado_e_acao(env: Env) -> None:
    with env.client() as c:
        login(c)
        html = c.get(LIST, headers=HX).text
        assert "Desligado" in html and "Ligar" in html
        asyncio.run(_display(env.dsn, True))
        html = c.get(LIST, headers=HX).text
    assert "Ligado" in html and "Desligar" in html


def test_modal_de_confirmacao_sem_confirm_nativo(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/exibicao", headers=HX)
    assert r.status_code == 200
    assert 'name="value" value="on"' in r.text and "aval jurídico" in r.text
    assert "confirm(" not in r.text and "<script" not in r.text


def test_ligar_e_desligar_gravam_com_auditoria_panel(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        r = _post(c, f"{BASE}/exibicao", tok, {"value": "on"})
        assert r.status_code == 200 and "closeOverlays" in _events(r)
        assert "Ligado" in c.get(LIST, headers=HX).text
        r = _post(c, f"{BASE}/exibicao", tok, {"value": "off"})
        assert r.status_code == 200
        assert "Desligado" in c.get(LIST, headers=HX).text
    assert sql(env.dsn, "SELECT value::text FROM ops.system_state WHERE key = $1", KEY) == "false"
    assert _audit(env, "flag.set") == 2
    assert sql(env.dsn, "SELECT DISTINCT actor_type FROM ops.audit_log") == "panel"


def test_valor_invalido_nao_grava(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/exibicao", _token(c), {"value": "talvez"})
    assert _events(r)["toast"]["kind"] == "erro"
    assert sql(env.dsn, "SELECT count(*) FROM ops.system_state WHERE key = $1", KEY) == 0
    assert _audit(env, "flag.set") == 0


def test_exibicao_exige_csrf(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.post(f"{BASE}/exibicao", data={"value": "on"}, headers=HX)
    assert r.status_code == 403
    assert sql(env.dsn, "SELECT count(*) FROM ops.system_state WHERE key = $1", KEY) == 0


def test_desconhecido_nao_gera_alerta_nem_email(env: Env) -> None:
    aid = asyncio.run(_seed(env.dsn))
    with env.client() as c:
        login(c)
        _post(c, f"{BASE}/{aid}/ignorar", _token(c))
    assert sql(env.dsn, "SELECT count(*) FROM ops.alerts") == 0
