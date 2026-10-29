"""Pagina Alertas (PN-44, PN-43, PN-5; task PN-T42) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from app.panel.api.alerts import ALERT_LABELS, LIST_LIMIT
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B
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

PATH = "/dashboard/alerts"
EMPTY = "Nenhum alerta. Sistema saudável."
_INSERT = (
    "INSERT INTO ops.alerts (severity, kind, message, payload, ticket_number, auto_action, "
    "acknowledged_at, created_at) VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8) RETURNING id"
)


def add_alert(
    env: Env,
    kind: str = "api_failure",
    *,
    severity: str = "warn",
    message: str = "Mensagem de teste",
    payload: dict[str, Any] | None = None,
    ticket: str | None = None,
    auto: str | None = None,
    seen: bool = False,
    minutes_ago: int = 5,
) -> int:
    created = NOW - timedelta(minutes=minutes_ago)
    return int(
        sql(
            env.dsn, _INSERT, severity, kind, message, json.dumps(payload or {}), ticket, auto,
            created if seen else None, created,
        )
    )


async def _seed_tickets(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None, "pagina sem campo csrf"
    return match.group(1)


def _page(c: Any, query: str = "") -> str:
    r = c.get(PATH + query)
    assert r.status_code == 200
    return str(r.text)


def _cards(html: str) -> list[int]:
    return [int(i) for i in re.findall(r'<article id="alerta-(\d+)"', html)]


def _ack(c: Any, alert_id: str | int, token: str, **data: str) -> Any:
    headers = {**post_headers(token), "HX-Request": "true"}
    return c.post(f"{PATH}/{alert_id}/ack", data=data, headers=headers)


def _toast(r: Any) -> str:
    return str(json.loads(r.headers["HX-Trigger"])["toast"]["message"])


# ---- acesso e vazio (PN-43) -----------------------------------------------------------


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:
    with env.client() as c:
        r = c.get(PATH)
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]


def test_vazio_mostra_estado_saudavel(env: Env) -> None:
    with env.client() as c:
        login(c)
        r = c.get(PATH)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    assert EMPTY in r.text and "Alertas do sistema" in r.text
    assert "Não vistos (0)" in r.text


# ---- PN-44: so sistema e seguranca, rotulos PT-BR -----------------------------------------


def test_rotulos_pt_br_de_todos_os_tipos(env: Env) -> None:
    for kind in ALERT_LABELS:
        add_alert(env, kind)
    with env.client() as c:
        login(c)
        html = _page(c)
    for label in set(ALERT_LABELS.values()):
        assert label in html
    for needle in ("Falha ao enviar boas-vindas", "Conflito de identidade", "Pausa manual",
                   "Limite de 60 msg/h", "Jarvis ligado em uma marca"):
        assert needle in html
    assert len(_cards(html)) == len(ALERT_LABELS)


def test_escalacao_midia_e_desconhecido_nao_sao_alertas(env: Env) -> None:
    kept = add_alert(env, "error_rate", severity="high")
    for kind in ("media_received", "escalation_sla", "unknown_number", "escalated"):
        add_alert(env, kind, message=f"antigo {kind}")
    with env.client() as c:
        login(c)
        html = _page(c)
        again = _page(c, "?filtro=nao_vistos")
    assert _cards(html) == [kept] == _cards(again)
    for kind in ("media_received", "escalation_sla", "unknown_number", "escalated"):
        assert f"antigo {kind}" not in html
    assert "Não vistos (1)" in html


def test_cartao_mostra_severidade_selo_detalhes_e_escapa_texto(env: Env) -> None:
    add_alert(
        env, "injection_attempt", severity="critical", auto="recusado",
        message="<script>x</script> tentou mudar regras", payload={"trecho": "ignore tudo"},
    )
    with env.client() as c:
        login(c)
        html = _page(c)
    assert "CRITICAL" in html and "AUTO: recusado" in html
    assert "Ver detalhes" in html and "ignore tudo" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;x&lt;/script&gt;" in html
    assert "Marcar como visto" in html


def test_ordem_mais_novo_primeiro_e_limite(env: Env) -> None:
    old = add_alert(env, minutes_ago=50)
    new = add_alert(env, minutes_ago=2)
    mid = add_alert(env, minutes_ago=20)
    with env.client() as c:
        login(c)
        assert _cards(_page(c)) == [new, mid, old]
    assert LIST_LIMIT == 100


# ---- filtros -------------------------------------------------------------------------


def test_filtros_nao_vistos_e_criticos(env: Env) -> None:
    seen = add_alert(env, severity="critical", seen=True, minutes_ago=3)
    crit = add_alert(env, severity="critical", minutes_ago=4)
    warn = add_alert(env, severity="warn", minutes_ago=5)
    with env.client() as c:
        login(c)
        todos = _page(c)
        unseen = _page(c, "?filtro=nao_vistos")
        criticos = _page(c, "?filtro=criticos")
        invalido = _page(c, "?filtro=qualquer")
    assert _cards(todos) == [seen, crit, warn] == _cards(invalido)
    assert _cards(unseen) == [crit, warn]
    assert _cards(criticos) == [seen, crit]
    assert "✓ visto" in todos and "Não vistos (2)" in todos
    assert 'aria-current="true"' in criticos


def test_filtro_sem_resultado_mostra_vazio(env: Env) -> None:
    add_alert(env, severity="warn")
    with env.client() as c:
        login(c)
        assert EMPTY in _page(c, "?filtro=criticos")


# ---- PN-5: escopo por marca ---------------------------------------------------------------


def test_marca_em_foco_usa_o_brand_do_payload(env: Env) -> None:
    a = add_alert(env, "pause", payload={"brand": BRAND_A})
    b = add_alert(env, "brand_off", payload={"brand": BRAND_B})
    geral = add_alert(env, "volume_spike", payload={})
    with env.client() as c:
        login(c)
        todas = _cards(_page(c))
        c.cookies.set("jarvis_brand", BRAND_A)
        so_a = _cards(_page(c))
        unseen_a = _page(c)
        c.cookies.set("jarvis_brand", BRAND_B)
        so_b = _cards(_page(c))
    assert set(todas) == {a, b, geral}
    assert set(so_a) == {a, geral} and set(so_b) == {b, geral}
    assert "Não vistos (2)" in unseen_a


# ---- ?abrir=<id> --------------------------------------------------------------------------


def test_abrir_destaca_o_cartao(env: Env) -> None:
    add_alert(env, minutes_ago=1)
    target = add_alert(env, minutes_ago=30)
    with env.client() as c:
        login(c)
        html = _page(c, f"?abrir={target}")
        plain = _page(c)
    assert html.count("data-alert-focus") == 1
    focus = re.search(rf'<article id="alerta-{target}"[^>]*data-alert-focus', html)
    assert focus is not None
    assert "data-alert-focus" not in plain


def test_abrir_aparece_mesmo_fora_do_filtro_e_do_limite(env: Env) -> None:
    seen = add_alert(env, seen=True, minutes_ago=500)
    for i in range(LIST_LIMIT + 1):
        add_alert(env, minutes_ago=i)
    with env.client() as c:
        login(c)
        html = _page(c, f"?filtro=nao_vistos&abrir={seen}")
    assert _cards(html)[0] == seen and "data-alert-focus" in html


def test_abrir_invalido_desconhecido_ou_de_outro_tipo_e_ignorado(env: Env) -> None:
    real = add_alert(env)
    not_alert = add_alert(env, "unknown_number", message="numero desconhecido antigo")
    other_brand = add_alert(env, "pause", payload={"brand": BRAND_B})
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", BRAND_A)
        for value in ("abc", "-1", "0", "99999999999999999999", str(not_alert),
                      str(other_brand), "999999"):
            html = _page(c, f"?abrir={value}")
            assert "data-alert-focus" not in html and _cards(html) == [real], value


# ---- Abrir J-xxxx (drawer) -------------------------------------------------------------------


def test_abrir_ticket_so_quando_o_ticket_existe(env: Env) -> None:
    world = asyncio.run(_seed_tickets(env.dsn))
    number = world.tickets[0].ticket_number
    add_alert(env, "api_failure", ticket=number, minutes_ago=1)
    add_alert(env, "api_failure", ticket="J-9999", minutes_ago=2)
    with env.client() as c:
        login(c)
        html = _page(c)
    assert f"Abrir {number}" in html and f'hx-get="/dashboard/tickets/{number}"' in html
    assert "Abrir J-9999" not in html


# ---- marcar como visto ---------------------------------------------------------------------


def test_ack_marca_visto_e_audita(env: Env) -> None:
    alert = add_alert(env, severity="critical")
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _ack(c, alert, token, filtro="nao_vistos")
        after = _page(c, "?filtro=nao_vistos")
    assert r.status_code == 200 and _toast(r) == "Alerta marcado como visto."
    assert "Marcar como visto" not in r.text and EMPTY in r.text
    assert EMPTY in after
    row = asyncio.run(_row(env.dsn, alert))
    assert row["acknowledged_at"] is not None and row["acknowledged_by"] == "admin@example.test"
    audit = sql(
        env.dsn,
        "SELECT count(*) FROM ops.audit_log WHERE action = 'alert.ack' AND target_id = $1",
        str(alert),
    )
    assert audit == 1


async def _row(dsn: str, alert_id: int) -> Any:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow("SELECT * FROM ops.alerts WHERE id = $1", alert_id)
    finally:
        await conn.close()


def test_ack_repetido_nao_altera_nem_audita_de_novo(env: Env) -> None:
    alert = add_alert(env)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        assert _toast(_ack(c, alert, token)) == "Alerta marcado como visto."
        first = asyncio.run(_row(env.dsn, alert))["acknowledged_at"]
        again = _ack(c, alert, token)
    assert _toast(again) == "Este alerta já estava visto."
    assert asyncio.run(_row(env.dsn, alert))["acknowledged_at"] == first
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'alert.ack'") == 1


def test_ack_nao_alcanca_o_que_nao_e_alerta_nem_outra_marca(env: Env) -> None:
    unknown = add_alert(env, "unknown_number")
    other = add_alert(env, "pause", payload={"brand": BRAND_B})
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        c.cookies.set("jarvis_brand", BRAND_A)
        for value in (unknown, other, "abc", "999999", "99999999999999999999"):
            r = _ack(c, value, token)
            assert r.status_code == 200 and _toast(r) == "Alerta não encontrado."
    assert asyncio.run(_row(env.dsn, unknown))["acknowledged_at"] is None
    assert asyncio.run(_row(env.dsn, other))["acknowledged_at"] is None
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'alert.ack'") == 0


def test_ack_exige_csrf(env: Env) -> None:
    alert = add_alert(env)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        sem_token = c.post(f"{PATH}/{alert}/ack", headers={"Origin": ORIGIN})
        origem_ruim = c.post(
            f"{PATH}/{alert}/ack", headers=post_headers(token, origin="https://evil.example")
        )
        sem_sessao_get = c.get(PATH + f"/{alert}/ack")
    assert sem_token.status_code == 403 and origem_ruim.status_code == 403
    assert sem_sessao_get.status_code in (404, 405)
    assert asyncio.run(_row(env.dsn, alert))["acknowledged_at"] is None


# ---- controles (parciais de outra tarefa, ignore missing) ---------------------------------


def test_controles_carregam_do_fragmento_do_router_de_controles(env: Env) -> None:
    with env.client() as c:
        login(c)
        html = _page(c)
    assert 'hx-get="/dashboard/control/painel"' in html and 'hx-trigger="load"' in html
    assert 'hx-trigger="controlsChanged from:body"' in html
    for leak in ("Jarvis pausado.", "Em quais marcas"):
        assert leak not in html  # vem do fragmento, nao da pagina
