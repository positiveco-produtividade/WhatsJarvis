"""Ficha, revogar, reativar e reenviar (PN-34, PN-35, PN-39, PN-55; task PN-T35) contra Postgres."""

# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeChannel
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


async def _add(dsn: str, *, status: str = "active", seq: int = 1, profile: str = "vendedor") -> int:
    conn = await asyncpg.connect(dsn)
    try:
        base = "pending_optin" if status == "pending_optin" else "active"
        uid = await insert_user(
            conn, name=f"Pessoa Teste {seq}", seq=seq, brands=[BRAND_A], now=NOW, status=base
        )
        if status == "revoked":
            await conn.execute(_REMOVE, uid, "revoked", NOW, "left_company", None)
        elif status == "declined":
            await conn.execute(_REMOVE, uid, "declined", None, None, NOW)
        if profile != "vendedor":
            await conn.execute("UPDATE access.users SET profile = $2 WHERE id = $1", uid, profile)
        return uid
    finally:
        await conn.close()


def add(env: Env, **kw: Any) -> int:
    return asyncio.run(_add(env.dsn, **kw))


async def _row(dsn: str, uid: int) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
        assert row is not None
        return row
    finally:
        await conn.close()


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, token: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(token), **HX})


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


def _status(env: Env, uid: int) -> str:
    return str(sql(env.dsn, "SELECT status FROM access.users WHERE id = $1", uid))


def _audit(env: Env, action: str) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = $1", action))


def _outbound(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages"))


# ---- ficha ---------------------------------------------------------------------------------


def test_ficha_mostra_telefone_completo(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/{uid}", headers=HX)
    assert r.status_code == 200
    assert "+551190000" in r.text and "00001" in r.text
    assert "Pessoa Teste 1" in r.text and "Revogar acesso" in r.text
    assert r.headers["cache-control"] == "no-store, private"


def test_ficha_inexistente_404_com_aviso(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(f"{BASE}/99999", headers=HX)
    assert r.status_code == 404
    assert _events(r)["toast"]["kind"] == "erro"


def test_ficha_exige_sessao(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        r = c.get(f"{BASE}/{uid}")
    assert r.status_code == 302


def test_ficha_retirada_oferece_reativar_sem_revogar(env: Env) -> None:
    uid = add(env, status="revoked")
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
    assert "Reativar" in html and "Revogar acesso" not in html
    assert "Saiu da empresa" in html


def test_ficha_do_proprio_admin_sem_botao_revogar(env: Env) -> None:
    uid = add(env, profile="administrador")
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
    assert "Revogar acesso" not in html and "Administrador" in html


# ---- revogar (PN-34, PN-35) ---------------------------------------------------------------


def test_modal_revogar_lista_fixa_sem_outro(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}/revogar", headers=HX).text
    for label in (
        "Saiu da empresa",
        "Troca de número",
        "Fim do contrato do parceiro",
        "Reconciliação mensal (3C)",
        "Recusou o aviso de privacidade",
    ):
        assert label in html
    assert 'value="other"' not in html


def test_revogar_grava_motivo_autor_e_audita_sem_enviar(env: Env) -> None:
    uid = add(env)
    spy = FakeChannel()
    before = _outbound(env)
    with env.client() as c:
        c.app.state.channel = spy  # type: ignore[attr-defined]
        login(c)
        r = _post(c, f"{BASE}/{uid}/revogar", _token(c), {"reason": "left_company"})
    assert r.status_code == 200 and r.text == ""
    assert r.headers["HX-Reswap"] == "none"
    events = _events(r)
    assert "pessoasChanged" in events and "closeOverlays" in events
    assert events["toast"]["kind"] == "ok"
    row = asyncio.run(_row(env.dsn, uid))
    assert row["status"] == "revoked"
    assert row["revoked_reason"] == "left_company"
    assert row["revoked_by"] == "admin@example.test" and row["revoked_at"] is not None
    assert _audit(env, "user.revoked") == 1
    assert int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE actor_type = 'panel'")) == 1
    assert _outbound(env) == before
    assert spy.calls == []


@pytest.mark.parametrize("reason", ["", "other", "inventado", "LEFT_COMPANY"])
def test_revogar_motivo_fora_da_lista_e_recusado(env: Env, reason: str) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/revogar", _token(c), {"reason": reason})
    assert r.status_code == 200
    assert "Escolha um motivo da lista." in r.text and "HX-Trigger" not in r.headers
    assert _status(env, uid) == "active"
    assert _audit(env, "user.revoked") == 0


def test_revogar_proprio_admin_recusado_no_servidor(env: Env) -> None:
    uid = add(env, profile="administrador")
    with env.client() as c:
        login(c)
        token = _token(c)
        form = c.get(f"{BASE}/{uid}/revogar", headers=HX)
        r = _post(c, f"{BASE}/{uid}/revogar", token, {"reason": "left_company"})
    assert form.status_code == 403 and r.status_code == 403
    assert _events(r)["toast"]["kind"] == "erro"
    assert _status(env, uid) == "active"
    assert _audit(env, "user.revoked") == 0


def test_revogar_ja_retirada_nao_muda_nada(env: Env) -> None:
    uid = add(env, status="revoked")
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/revogar", _token(c), {"reason": "number_change"})
    assert r.status_code == 200 and _events(r)["toast"]["kind"] == "aviso"
    assert asyncio.run(_row(env.dsn, uid))["revoked_reason"] == "left_company"
    assert _audit(env, "user.revoked") == 0


def test_revogar_inexistente_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/99999/revogar", _token(c), {"reason": "left_company"})
    assert r.status_code == 404


def test_revogar_sem_csrf_e_negado(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = c.post(f"{BASE}/{uid}/revogar", data={"reason": "left_company"}, headers=HX)
    assert r.status_code == 403
    assert _status(env, uid) == "active"


# ---- reativar e reenviar (PN-39) ----------------------------------------------------------


@pytest.mark.parametrize("status", ["revoked", "declined"])
def test_reativar_volta_a_pending_optin_com_aceite_novo(env: Env, status: str) -> None:
    uid = add(env, status=status)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/reativar", _token(c))
    assert r.status_code == 200 and r.headers["HX-Reswap"] == "none"
    assert "pessoasChanged" in _events(r)
    row = asyncio.run(_row(env.dsn, uid))
    assert row["status"] == "pending_optin"
    assert row["accepted_at"] is None and row["accepted_notice_version"] is None
    assert row["revoked_at"] is None and row["revoked_by"] is None and row["revoked_reason"] is None
    opened = sql(
        env.dsn,
        "SELECT count(*) FROM access.optin_requests WHERE user_id = $1 AND status = 'open'",
        uid,
    )
    assert int(opened) == 1
    assert _audit(env, "user.resent") == 1


def test_reativar_pessoa_ativa_so_avisa(env: Env) -> None:
    uid = add(env)
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/{uid}/reativar", _token(c))
    assert r.status_code == 200 and _events(r)["toast"]["kind"] == "aviso"
    assert _status(env, uid) == "active"
    assert _audit(env, "user.resent") == 0


def test_reenviar_aviso_a_quem_ainda_nao_aceitou(env: Env) -> None:
    uid = add(env, status="pending_optin")
    with env.client() as c:
        login(c)
        html = c.get(f"{BASE}/{uid}", headers=HX).text
        r = _post(c, f"{BASE}/{uid}/reenviar", _token(c))
    assert "Reenviar aviso" in html
    assert r.status_code == 200 and _events(r)["toast"]["kind"] == "ok"
    assert _status(env, uid) == "pending_optin"
    assert _audit(env, "user.resent") == 1


def test_reativar_inexistente_404(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/99999/reativar", _token(c))
    assert r.status_code == 404
