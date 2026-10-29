"""Cadastrar pessoa e autorizar pedido (PN-32, PN-33, PN-36, PN-4; task PN-T34), Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

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

NEW = "/dashboard/pessoas/nova"
HX = {"HX-Request": "true"}
SECRET_TEXT = "texto-secreto-da-primeira-mensagem"
PHONE_WITH_9 = "+55 85 98888-7777"
PHONE_WITHOUT_9 = "+55 85 8888-7777"
PHONE_KEY = "558588887777"
WELCOME = {"welcome_template_name": "boas_vindas_teste"}


def _client(env: Env) -> TestClient:
    return env.client(**WELCOME)


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, token: str, data: dict[str, Any]) -> Any:
    return c.post(NEW, data=data, headers={**post_headers(token), **HX})


def _fields(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "nome": "Maria Teste",
        "telefone": PHONE_WITH_9,
        "perfil": "vendedor",
        "empresa": "Empresa Teste",
        "ufs": ["CE", "PE"],
        "canais": ["farma"],
        "marcas": ["jungle", "zaya"],
        "gestor": "Gestor Teste",
    }
    base.update(over)
    return {k: v for k, v in base.items() if v is not None}


def _users(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM access.users"))


def _outbox(env: Env) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM chat.outbound_messages"))


def _seed_attempt(env: Env, *, phone_key: str | None = PHONE_KEY, count: int = 3) -> int:
    return int(
        sql(
            env.dsn,
            "INSERT INTO access.access_attempts (phone_key, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type, first_msg_chars) "
            "VALUES ($1, 'unknown', $2, $3, $3, 'text', 20) RETURNING id",
            phone_key,
            count,
            NOW,
        )
    )


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


# ---- acesso (PN-4) -----------------------------------------------------------------------


def test_sem_sessao_e_sem_csrf_nada_acontece(env: Env) -> None:
    with _client(env) as c:
        assert c.get(NEW).status_code == 302
        assert c.post(NEW, data=_fields()).status_code == 302
        login(c)
        token = _token(c)
        assert c.post(NEW, data=_fields(), headers={"Origin": ORIGIN}).status_code == 403
        assert c.post(NEW, data=_fields(), headers={"X-CSRF-Token": token}).status_code == 403
    assert _users(env) == 0 and _outbox(env) == 0


# ---- formulario --------------------------------------------------------------------------


def test_formulario_tem_os_8_campos(env: Env) -> None:
    with _client(env) as c:
        login(c)
        r = c.get(NEW)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    html = r.text
    assert "Cadastrar pessoa" in html and f'action="{NEW}"' in html
    for needle in (
        'name="nome"', 'name="telefone"', 'name="perfil"', 'name="empresa"', 'name="ufs"',
        'name="canais"', 'name="marcas"', 'name="gestor"', "Gestor responsável", "boas-vindas",
    ):  # fmt: skip
        assert needle in html
    assert 'name="pedido"' not in html


# ---- cadastro (PN-32) ---------------------------------------------------------------------


def test_cadastro_valido_grava_e_enfileira_boas_vindas(env: Env) -> None:
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields())
    assert r.status_code == 200 and r.text == "" and r.headers["HX-Reswap"] == "none"
    events = _events(r)
    assert set(events) == {"pessoasChanged", "toast", "closeOverlays"}
    assert events["toast"]["kind"] == "ok"
    assert sql(env.dsn, "SELECT status FROM access.users") == "pending_optin"
    assert sql(env.dsn, "SELECT phone_key FROM access.users") == PHONE_KEY
    assert sql(env.dsn, "SELECT ufs FROM access.users") == ["CE", "PE"]
    assert sql(env.dsn, "SELECT brands FROM access.users") == ["jungle", "zaya"]
    assert sql(env.dsn, "SELECT manager_name FROM access.users") == "Gestor Teste"
    assert sql(env.dsn, "SELECT created_by FROM access.users") == "admin@example.test"
    # Boas-vindas so na fila (o envio e dos workers de PF).
    assert sql(env.dsn, "SELECT kind FROM chat.outbound_messages") == "welcome_template"
    assert sql(env.dsn, "SELECT status FROM chat.outbound_messages") == "pending"
    audit = sql(
        env.dsn,
        "SELECT count(*) FROM ops.audit_log WHERE action='user.registered' "
        "AND actor_type='panel'",
    )
    assert audit == 1


@pytest.mark.parametrize(
    ("field", "label"),
    [
        ("nome", "Nome"),
        ("telefone", "Telefone"),
        ("empresa", "Empresa"),
        ("ufs", "UFs"),
        ("canais", "Canais"),
        ("marcas", "Marcas"),
        ("gestor", "Gestor responsável"),
    ],
)
def test_campo_ausente_nomeia_o_campo_e_nao_grava(env: Env, field: str, label: str) -> None:
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields(**{field: None}))
    assert r.status_code == 200 and "HX-Trigger" not in r.headers
    assert "Não salvei" in r.text and f"{label}: campo obrigatório" in r.text
    assert _users(env) == 0 and _outbox(env) == 0


def test_perfil_fora_da_lista_e_varios_erros_voltam_juntos(env: Env) -> None:
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields(perfil="chefe", nome="", gestor=""))
    assert "Perfil: perfil fora da lista" in r.text
    assert "Nome: campo obrigatório" in r.text and "Gestor responsável: campo obrigatório" in r.text
    assert "3 problemas" in r.text
    assert _users(env) == 0


def test_valores_digitados_voltam_no_formulario(env: Env) -> None:
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields(gestor="", nome="Fulano Digitado"))
    assert 'value="Fulano Digitado"' in r.text
    assert 'value="CE" checked' in r.text and 'value="jungle" checked' in r.text


# ---- telefone (PN-33) ---------------------------------------------------------------------


def test_telefone_incompleto_e_recusado(env: Env) -> None:
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields(telefone="+55 85 9888"))
    assert "Telefone:" in r.text and "Não salvei" in r.text
    assert _users(env) == 0 and _outbox(env) == 0


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (PHONE_WITH_9, PHONE_WITH_9),
        (PHONE_WITH_9, PHONE_WITHOUT_9),
        (PHONE_WITHOUT_9, PHONE_WITH_9),
    ],
)
def test_telefone_duplicado_inclusive_pelo_nono_digito(env: Env, first: str, second: str) -> None:
    with _client(env) as c:
        login(c)
        token = _token(c)
        assert "HX-Trigger" in _post(c, token, _fields(telefone=first)).headers
        r = _post(c, token, _fields(telefone=second, nome="Outra Pessoa"))
    assert r.status_code == 200 and "HX-Trigger" not in r.headers
    assert "já está cadastrado" in r.text and 'value="Outra Pessoa"' in r.text
    assert _users(env) == 1 and _outbox(env) == 1


# ---- autorizar pedido (PN-36) -------------------------------------------------------------


def test_pedido_preenche_o_telefone_sem_mostrar_a_primeira_mensagem(env: Env) -> None:
    attempt = _seed_attempt(env)
    sql(
        env.dsn,
        "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at) "
        "VALUES ($1, $2, $3) RETURNING attempt_id",
        attempt,
        SECRET_TEXT,
        NOW,
    )
    with _client(env) as c:
        login(c)
        r = c.get(f"{NEW}?pedido={attempt}")
    html = r.text
    assert "Autorizar número" in html and f'name="pedido" value="{attempt}"' in html
    assert f'value="+{PHONE_KEY}"' in html and "mandou 3 mensagens" in html
    assert SECRET_TEXT not in html


@pytest.mark.parametrize("pedido", ["abc", "-1", "0", "99999999", "1;drop"])
def test_pedido_invalido_ou_inexistente_abre_formulario_em_branco(env: Env, pedido: str) -> None:
    with _client(env) as c:
        login(c)
        r = c.get(NEW, params={"pedido": pedido})
    assert r.status_code == 200 and "Cadastrar pessoa" in r.text
    assert 'name="pedido"' not in r.text


def test_pedido_sem_telefone_abre_sem_prefill(env: Env) -> None:
    attempt = int(
        sql(
            env.dsn,
            "INSERT INTO access.access_attempts (bsuid, reason, attempt_count, first_attempt_at, "
            "last_attempt_at, first_msg_type) VALUES ('BR.abc', 'unknown', 1, $1, $1, 'audio') "
            "RETURNING id",
            NOW,
        )
    )
    with _client(env) as c:
        login(c)
        r = c.get(f"{NEW}?pedido={attempt}")
    assert 'name="telefone" value=""' in r.text and "mandou 1 mensagem." in r.text


def test_autorizar_resolve_o_pedido_na_mesma_transacao(env: Env) -> None:
    attempt = _seed_attempt(env)
    with _client(env) as c:
        login(c)
        r = _post(c, _token(c), _fields(pedido=str(attempt)))
    assert r.status_code == 200 and "pessoasChanged" in _events(r)
    user_id = sql(env.dsn, "SELECT id FROM access.users")
    resolved = sql(env.dsn, "SELECT user_id FROM access.access_attempts WHERE id=$1", attempt)
    assert resolved == user_id
    pending = sql(
        env.dsn,
        "SELECT count(*) FROM access.access_attempts "
        "WHERE user_id IS NULL AND dismissed_at IS NULL",
    )
    assert pending == 0
    assert _outbox(env) == 1
    audited = sql(
        env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action='access_attempt.authorized'"
    )
    assert audited == 1


def test_falha_do_cadastro_nao_resolve_o_pedido(env: Env) -> None:
    attempt = _seed_attempt(env)
    with _client(env) as c:
        login(c)
        token = _token(c)
        r = _post(c, token, _fields(gestor="", pedido=str(attempt)))
        assert 'name="pedido"' in r.text
        assert _post(c, token, _fields()).status_code == 200  # cadastra o mesmo numero antes
        r = _post(c, token, _fields(telefone=PHONE_WITHOUT_9, pedido=str(attempt)))
    assert "já está cadastrado" in r.text and f'name="pedido" value="{attempt}"' in r.text
    assert sql(env.dsn, "SELECT user_id FROM access.access_attempts WHERE id=$1", attempt) is None
    assert _users(env) == 1
