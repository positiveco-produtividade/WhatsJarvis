"""Editar e trocar o numero pela ficha (cadastro.1, CA-T03/CA-T04; CA-1..CA-6, CA-12, CA-13)."""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.services.phone import normalize_phone
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
_REMOVE = (
    "UPDATE access.users SET status = $2, revoked_at = $3, revoked_by = 'x@example.test', "
    "revoked_reason = $4, declined_at = $5 WHERE id = $1"
)


async def _add(dsn: str, *, status: str = "active", seq: int = 1) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        base = "pending_optin" if status == "pending_optin" else "active"
        uid = await insert_user(
            conn, name=f"Pessoa Editar {seq}", seq=seq, brands=[BRAND_A], now=NOW, status=base
        )
        if status == "revoked":
            await conn.execute(_REMOVE, uid, "revoked", NOW, "left_company", None)
        elif status == "declined":
            await conn.execute(_REMOVE, uid, "declined", None, None, NOW)
        return uid
    finally:
        await conn.close()


def add(env: Env, **kw: Any) -> int:
    return asyncio.run(_add(env.dsn, **kw))


async def _fetchrow(dsn: str, query: str, *args: Any) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(query, *args)
        assert row is not None
        return row
    finally:
        await conn.close()


def row(env: Env, uid: int) -> asyncpg.Record:
    return asyncio.run(_fetchrow(env.dsn, "SELECT * FROM access.users WHERE id = $1", uid))


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, data: Any) -> Any:
    return c.post(url, data=data, headers={**post_headers(_token(c)), **HX})


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


def _form(r: asyncpg.Record, **over: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "nome": r["name"],
        "perfil": r["profile"],
        "empresa": r["company"],
        "ufs": list(r["ufs"]),
        "canais": list(r["channels"]),
        "marcas": list(r["brands"]),
        "gestor": r["manager_name"],
        "versao": r["updated_at"].isoformat(),
    }
    data.update(over)
    return data


# --- editar (CA-1..CA-6) -----------------------------------------------------------------


def test_ficha_tem_editar_e_trocar_numero(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
    assert f"{BASE}/{uid}/editar" in html and f"{BASE}/{uid}/trocar-numero" in html


def test_revogada_sem_botoes_e_post_recusado(env: Env) -> None:
    uid = add(env, status="revoked")
    r = row(env, uid)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
        assert "/editar" not in html and "/trocar-numero" not in html
        assert c.get(f"{BASE}/{uid}/editar", headers=HX).status_code == 409
        resp = _post(c, f"{BASE}/{uid}/editar", _form(r, ufs=["RJ"]))
    assert resp.status_code == 409
    assert list(row(env, uid)["ufs"]) == list(r["ufs"])


def test_formulario_preenchido_com_telefone_so_leitura(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}/editar", headers=HX).text
    assert "Editar cadastro" in html and r["name"] in html
    assert 'name="versao"' in html and r["updated_at"].isoformat() in html
    assert 'name="telefone"' not in html  # telefone so leitura, sem campo enviado
    assert r["phone_e164"] in html and "readonly" in html


def test_salvar_atualiza_e_audita(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        resp = _post(c, f"{BASE}/{uid}/editar", _form(r, ufs=["CE", "PI"], gestor="Novo Gestor"))
    assert resp.status_code == 200
    assert _events(resp)["toast"]["message"] == "Cadastro atualizado."
    after = row(env, uid)
    assert sorted(after["ufs"]) == ["CE", "PI"] and after["manager_name"] == "Novo Gestor"
    assert after["phone_e164"] == r["phone_e164"] and after["status"] == r["status"]
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'user.update'") == 1


def test_telefone_do_formulario_e_ignorado(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        _post(c, f"{BASE}/{uid}/editar", _form(r, telefone="+5585999990000", ufs=["BA"]))
    assert row(env, uid)["phone_e164"] == r["phone_e164"]


def test_dados_invalidos_voltam_no_formulario(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        resp = _post(c, f"{BASE}/{uid}/editar", _form(r, nome="", ufs=[]))
    assert resp.status_code == 200 and "Editar cadastro" in resp.text
    assert "Nome" in resp.text and "UFs" in resp.text
    assert row(env, uid)["updated_at"] == r["updated_at"]


def test_sem_mudanca_avisa_e_nao_grava(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        resp = _post(c, f"{BASE}/{uid}/editar", _form(r))
    assert _events(resp)["toast"]["message"] == "Nada foi alterado."
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log") == 0


def test_edicao_concorrente_mostra_dados_atuais(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        assert _post(c, f"{BASE}/{uid}/editar", _form(r, ufs=["RJ"])).status_code == 200
        late = _post(c, f"{BASE}/{uid}/editar", _form(r, ufs=["MG"]))  # versao antiga
    assert "Outra pessoa alterou este cadastro" in late.text
    assert list(row(env, uid)["ufs"]) == ["RJ"]


def test_editar_exige_csrf(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        login(c)
        resp = c.post(f"{BASE}/{uid}/editar", data=_form(r, ufs=["RJ"]), headers=HX)
    assert resp.status_code == 403
    assert list(row(env, uid)["ufs"]) == list(r["ufs"])


def test_editar_sem_sessao_nao_grava(env: Env) -> None:
    uid = add(env)
    r = row(env, uid)
    with env.client() as c:
        resp = c.post(f"{BASE}/{uid}/editar", data=_form(r, ufs=["RJ"]), headers=HX)
    assert resp.status_code in (302, 401, 403)
    assert list(row(env, uid)["ufs"]) == list(r["ufs"])


# --- trocar numero (CA-12, CA-13) ---------------------------------------------------------

NEW_PHONE = "+5585988887777"


def test_trocar_numero_cria_ficha_nova_e_retira_a_antiga(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        assert "Número novo" in c.get(f"{BASE}/{uid}/trocar-numero", headers=HX).text
        resp = _post(c, f"{BASE}/{uid}/trocar-numero", {"telefone": NEW_PHONE})
    assert resp.status_code == 200
    assert "Número trocado" in _events(resp)["toast"]["message"]
    old = row(env, uid)
    assert old["status"] == "revoked" and old["revoked_reason"] == "number_change"
    new_id = int(sql(env.dsn, "SELECT id FROM access.users WHERE phone_e164 = $1", NEW_PHONE))
    new = row(env, new_id)
    assert new["status"] == "pending_optin" and list(new["brands"]) == list(old["brands"])
    assert NEW_PHONE in resp.text  # o modal ja mostra a ficha nova
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action LIKE '%swap%'") == 1


def test_trocar_para_numero_ja_cadastrado_nao_muda_nada(env: Env) -> None:
    uid = add(env, seq=1)
    other = add(env, seq=2)
    other_phone = str(row(env, other)["phone_e164"])
    # A factory do painel grava phone_key sem a regra do app; aqui vale a regra real (9o digito).
    sql(env.dsn, "UPDATE access.users SET phone_key = $2 WHERE id = $1", other,
        normalize_phone(other_phone).match_key)  # fmt: skip
    # mesmo numero escrito de outro jeito (a regra do 9o digito e testada em test_phone)
    formatted = f"+55 ({other_phone[3:5]}) {other_phone[5:10]}-{other_phone[10:]}"
    with env.client() as c:
        login(c)
        for typed in (other_phone, formatted):
            resp = _post(c, f"{BASE}/{uid}/trocar-numero", {"telefone": typed})
            assert "já está cadastrado" in resp.text, typed
    assert row(env, uid)["status"] == "active"


def test_trocar_para_numero_invalido(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        resp = _post(c, f"{BASE}/{uid}/trocar-numero", {"telefone": "123"})
    assert "Telefone inválido" in resp.text
    assert row(env, uid)["status"] == "active"


def test_trocar_numero_de_revogada_recusado(env: Env) -> None:
    uid = add(env, status="revoked")
    with env.client() as c:
        login(c)
        resp = _post(c, f"{BASE}/{uid}/trocar-numero", {"telefone": NEW_PHONE})
    assert resp.status_code == 409
    assert sql(env.dsn, "SELECT count(*) FROM access.users WHERE phone_e164 = $1", NEW_PHONE) == 0


def test_trocar_numero_exige_csrf(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        resp = c.post(f"{BASE}/{uid}/trocar-numero", data={"telefone": NEW_PHONE}, headers=HX)
    assert resp.status_code == 403
    assert row(env, uid)["status"] == "active"
