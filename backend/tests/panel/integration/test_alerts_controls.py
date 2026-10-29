"""Controles: pausa e marcas (PN-45, PN-47, PN-48, PN-55; task PN-T43)."""
# ruff: noqa: F811
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.panel.integration.test_access import (  # noqa: F401
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

HX = {"HX-Request": "true"}
BASE = "/dashboard/control"
FLAG_ACTION = "flag.set"


def _token(c: TestClient) -> str:
    return str(c.get("/dashboard/_t/token").json()["token"])


def _post(c: TestClient, url: str, token: str, data: Any = None) -> Any:
    return c.post(url, data=data or {}, headers={**post_headers(token), **HX})


def _events(r: Any) -> dict[str, Any]:
    return json.loads(r.headers["HX-Trigger"])  # type: ignore[no-any-return]


def _count(env: Env, query: str, *args: Any) -> int:
    return int(sql(env.dsn, query, *args))


def _alerts(env: Env, kind: str) -> int:
    return _count(env, "SELECT count(*) FROM ops.alerts WHERE kind = $1", kind)


def _audits(env: Env, key: str) -> int:
    return _count(
        env,
        "SELECT count(*) FROM ops.audit_log WHERE action = $1 AND target_id = $2",
        FLAG_ACTION,
        key,
    )


def _value(env: Env, key: str) -> str | None:
    return sql(env.dsn, "SELECT value::text FROM ops.system_state WHERE key = $1", key)  # type: ignore[no-any-return]


def test_pausar_grava_audita_e_alerta_sem_email(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/pausa", _token(c), {"desired": "pause"})
    assert r.status_code == 200 and r.headers["HX-Reswap"] == "none"
    assert {"controlsChanged", "toast", "closeOverlays"} <= set(_events(r))
    assert _value(env, "paused") == "true"
    assert _audits(env, "paused") == 1
    assert _alerts(env, "pause") == 1
    assert _count(env, "SELECT count(*) FROM ops.deliveries WHERE channel = 'email'") == 0
    assert _count(env, "SELECT count(*) FROM ops.deliveries WHERE channel = 'group'") == 1
    actor = sql(env.dsn, "SELECT actor_type FROM ops.audit_log WHERE action = $1", FLAG_ACTION)
    assert actor == "panel"


def test_dois_post_iguais_nao_alternam(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        _post(c, f"{BASE}/pausa", tok, {"desired": "pause"})
        r = _post(c, f"{BASE}/pausa", tok, {"desired": "pause"})
    assert _events(r)["toast"]["kind"] == "aviso"
    assert _value(env, "paused") == "true"
    assert _audits(env, "paused") == 1
    assert _alerts(env, "pause") == 1


def test_retomar_sem_estar_pausado_nao_faz_nada(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = _post(c, f"{BASE}/pausa", _token(c), {"desired": "resume"})
    assert r.status_code == 200
    assert _value(env, "paused") is None
    assert _audits(env, "paused") == 0 and _alerts(env, "resume") == 0


def test_pausar_e_retomar(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        _post(c, f"{BASE}/pausa", tok, {"desired": "pause"})
        _post(c, f"{BASE}/pausa", tok, {"desired": "resume"})
    assert _value(env, "paused") == "false"
    assert _alerts(env, "pause") == 1 and _alerts(env, "resume") == 1
    assert _audits(env, "paused") == 2


def test_valor_invalido_e_marca_invalida(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        bad = _post(c, f"{BASE}/pausa", tok, {"desired": "alternar"})
        bad_brand = _post(c, f"{BASE}/marca/nao_existe", tok, {"desired": "off"})
        empty = _post(c, f"{BASE}/marca/zaya", tok)
    assert _events(bad)["toast"]["kind"] == "erro"
    assert bad_brand.status_code == 404
    assert _events(empty)["toast"]["kind"] == "erro"
    assert _count(env, "SELECT count(*) FROM ops.alerts") == 0


def test_marca_desligar_ligar_idempotente(env: Env) -> None:
    key = "brand_enabled:zaya"
    with env.client() as c:
        login(c)
        tok = _token(c)
        on_again = _post(c, f"{BASE}/marca/zaya", tok, {"desired": "on"})
        assert _events(on_again)["toast"]["kind"] == "aviso"
        assert _alerts(env, "brand_on") == 0 and _value(env, key) is None
        _post(c, f"{BASE}/marca/zaya", tok, {"desired": "off"})
        _post(c, f"{BASE}/marca/zaya", tok, {"desired": "off"})
        assert _value(env, key) == "false"
        assert _alerts(env, "brand_off") == 1 and _audits(env, key) == 1
        _post(c, f"{BASE}/marca/zaya", tok, {"desired": "on"})
    assert _value(env, key) == "true" and _alerts(env, "brand_on") == 1
    payload = sql(env.dsn, "SELECT payload::text FROM ops.alerts WHERE kind = 'brand_on'")
    assert json.loads(payload)["brand"] == "zaya"
    assert _count(env, "SELECT count(*) FROM ops.deliveries WHERE channel = 'email'") == 0


def test_dois_post_simultaneos_geram_um_alerta(env: Env) -> None:
    def fire(_: int) -> int:
        with env.client() as c:
            login(c)
            return int(_post(c, f"{BASE}/pausa", _token(c), {"desired": "pause"}).status_code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(fire, range(2)))
    assert codes == [200, 200]
    assert _value(env, "paused") == "true"
    assert _alerts(env, "pause") == 1
    assert _audits(env, "paused") == 1


def test_modal_abre_com_estado_atual(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        aberto = c.get(f"{BASE}/pausa", headers=HX)
        _post(c, f"{BASE}/pausa", tok, {"desired": "pause"})
        pausado = c.get(f"{BASE}/pausa", headers=HX)
    assert 'value="pause"' in aberto.text and "Agora: Jarvis em operação" in aberto.text
    assert 'value="resume"' in pausado.text and "Agora: Jarvis pausado" in pausado.text
    assert "consentimento" in aberto.text
    for page in (aberto, pausado):
        assert "confirm(" not in page.text and "<script src=" in page.text


def test_modal_da_marca_mostra_agente_e_estado(env: Env) -> None:
    with env.client() as c:
        login(c)
        tok = _token(c)
        ligada = c.get(f"{BASE}/marca/jungle", headers=HX)
        _post(c, f"{BASE}/marca/jungle", tok, {"desired": "off"})
        desligada = c.get(f"{BASE}/marca/jungle", headers=HX)
        missing = c.get(f"{BASE}/marca/xyz", headers=HX)
    assert 'value="off"' in ligada.text and "Duda" in ligada.text and "rosto-jungle" in ligada.text
    assert 'value="on"' in desligada.text and "Agora: Desligado" in desligada.text
    assert missing.status_code == 404


def test_painel_lista_marcas_com_rosto_e_estado(env: Env) -> None:
    with env.client() as c:
        login(c)
        _post(c, f"{BASE}/marca/zaya", _token(c), {"desired": "off"})
        html = c.get(f"{BASE}/painel", headers=HX).text
    assert "Respondendo em" in html and "4 de 5" in html
    for name in ("Duda", "Léo", "Nico", "Rita", "Tati"):
        assert name in html
    assert "rosto-zaya" in html and "Desligado — a equipe atende" in html
    assert "Pausar Jarvis" in html


def test_painel_pausado_mostra_aviso_de_consentimento(env: Env) -> None:
    with env.client() as c:
        login(c)
        _post(c, f"{BASE}/pausa", _token(c), {"desired": "pause"})
        html = c.get(f"{BASE}/painel", headers=HX).text
    assert "Retomar operação" in html and "Jarvis pausado." in html and "consentimento" in html


def test_exige_csrf_e_sessao(env: Env) -> None:
    with env.client() as c:
        assert c.post(f"{BASE}/pausa", data={"desired": "pause"}).status_code in (302, 401, 403)
        login(c)
        assert c.post(f"{BASE}/pausa", data={"desired": "pause"}, headers=HX).status_code == 403
        assert c.post(f"{BASE}/marca/zaya", data={"desired": "off"}, headers=HX).status_code == 403
    assert _value(env, "paused") is None and _alerts(env, "pause") == 0


def test_alerta_sem_pii(env: Env) -> None:
    with env.client() as c:
        login(c)
        _post(c, f"{BASE}/pausa", _token(c), {"desired": "pause"})
    row = sql(env.dsn, "SELECT message || payload::text FROM ops.alerts WHERE kind = 'pause'")
    assert "@" not in row and "example.test" not in row
