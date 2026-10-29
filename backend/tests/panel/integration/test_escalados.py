"""Pagina Escalados (PN-19, PN-20, PN-21, PN-63, PN-60/61; task PN-T23) contra Postgres real."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
import json
import re
from datetime import timedelta
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    ORIGIN,
    Env,
    _sql,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

PATH = "/dashboard/escalados"
HTMX = {"HX-Request": "true"}
ESC_A, ESC_B = 4, 6  # escalados aguardando PRECO (marca A e marca B) do cenario padrao


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        world = await factories.seed_world(conn, NOW)
        sellers = world.sellers
        await _add(conn, 101, sellers.media_only, None, "MIDIA", "muito_urgente", "[audio 0:24]")
        await _add(conn, 102, sellers.media_only, BRAND_B, "MIDIA", "urgente", "[foto outra marca]")
        return world
    finally:
        await conn.close()


async def _add(
    conn: asyncpg.Connection, number: int, user_id: int, brand: str | None, cls: str,
    urgency: str, text: str,
) -> None:
    await factories.insert_ticket(
        conn, number=number, user_id=user_id, status="awaiting",
        created_at=NOW - timedelta(minutes=number), brand=brand, kind="media",
        classification=cls, escalated=True, urgency=urgency, message=text,
    )


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:
    return asyncio.run(_seed(env.dsn))


def _num(world: factories.PanelWorld, index: int) -> str:
    return world.tickets[index].ticket_number


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None, "pagina sem campo csrf"
    return match.group(1)


def _row(env: Env, number: str) -> Any:
    rows = asyncio.run(
        _sql(
            env.dsn,
            "SELECT status, urgency, answered_by, classification FROM chat.tickets "
            "WHERE ticket_number = $1",
            number,
            many=True,
        )
    )
    return rows[0]


def _audit(env: Env, action: str) -> int:
    return int(sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = $1", action))


def _post(c: TestClient, number: str, action: str, token: str, **data: str) -> Any:
    headers = {**post_headers(token), "HX-Request": "true"}
    return c.post(f"{PATH}/{number}/{action}", data=data, headers=headers)


def _toast(r: Any) -> str:
    return str(json.loads(r.headers["HX-Trigger"])["toast"]["message"])


def _page(c: TestClient, query: str = "") -> str:
    r = c.get(PATH + query)
    assert r.status_code == 200
    return r.text


# ---- PN-19: quadro por classificacao ---------------------------------------------------


def test_sem_sessao_redireciona_ao_iam(env: Env) -> None:
    with env.client() as c:
        r = c.get(PATH)
    assert r.status_code == 302 and "accounts.positiveco.com.br/login" in r.headers["location"]


def test_quadro_mostra_so_escalados_aguardando_por_coluna(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        r = c.get(PATH)
    html = r.text
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store, private"
    for label in ("Preço", "Produto", "Argumento", "Material", "Campanha", "Sem classificação"):
        assert f">{label}<" in html
    assert _num(seeded, ESC_A) in html and _num(seeded, ESC_B) in html
    assert _num(seeded, 2) not in html  # aguardando, mas nao escalado
    assert _num(seeded, 5) not in html  # respondido
    assert "[audio 0:24]" not in html  # midia fica atras do botao
    assert "Nenhuma dúvida de produto esperando" in html  # coluna vazia
    assert "Resumo automático" not in html  # chave do grupo desligada (padrao)


def test_urgencia_ordena_o_cartao_dentro_da_coluna(env: Env, seeded: factories.PanelWorld) -> None:
    sql(env.dsn, "UPDATE chat.tickets SET urgency = 'pouco_urgente' WHERE ticket_number = $1",
        _num(seeded, ESC_A))
    with env.client() as c:
        login(c)
        html = _page(c, "?midias=0")
    assert html.index(_num(seeded, ESC_B)) < html.index(_num(seeded, ESC_A))


def test_marca_em_foco_filtra_o_quadro(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", BRAND_A)
        html = _page(c)
    assert _num(seeded, ESC_A) in html and _num(seeded, ESC_B) not in html


def test_quadro_vazio_mostra_estados_vazios(env: Env) -> None:
    with env.client() as c:
        login(c)
        html = _page(c)
    assert "Nenhuma dúvida de preço na fila" in html and "Nada sem classificação" in html
    assert "🖼 Mídias" in html and "Mídias (" not in html


# ---- PN-20: botao Midias (N) respeita a marca ------------------------------------------


def test_botao_midias_conta_e_abre_o_quadro_de_midias(
    env: Env, seeded: factories.PanelWorld
) -> None:
    with env.client() as c:
        login(c)
        padrao = _page(c)
        midias = _page(c, "?midias=1")
    assert "Mídias (2)" in padrao and "[audio 0:24]" not in padrao
    assert "Mídias (2) ×" in midias
    assert "[audio 0:24]" in midias and "[foto outra marca]" in midias
    assert _num(seeded, ESC_A) not in midias
    assert "mídia</span>" in midias


def test_botao_midias_respeita_a_marca(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", BRAND_A)
        html = _page(c, "?midias=1")
    # Midia sem marca aparece em qualquer marca; a de outra marca nao.
    assert "[audio 0:24]" in html and "[foto outra marca]" not in html
    assert "Mídias (1) ×" in html


# ---- PN-21: mover para Concluida -------------------------------------------------------


def test_mover_para_concluida_tira_do_quadro_e_nao_envia_nada(
    env: Env, seeded: factories.PanelWorld
) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _post(c, number, "mover", token, destino="concluida")
        html = r.text
        again = _post(c, number, "mover", token, destino="concluida")
    assert r.status_code == 200 and "HX-Trigger" in r.headers
    assert "concluído" in _toast(r)
    assert number not in html and _num(seeded, ESC_B) in html
    row = _row(env, number)
    assert row["status"] == "answered_human" and row["answered_by"] == "Admin Teste"
    assert _audit(env, "ticket.conclude") == 1
    assert "Já concluído por Admin Teste" in _toast(again)
    assert _audit(env, "ticket.conclude") == 1  # no-op nao audita de novo


def test_mover_sem_alerta_e_sem_email(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        _post(c, _num(seeded, ESC_A), "mover", token, destino="concluida")
    alerts = sql(env.dsn, "SELECT count(*) FROM ops.alerts")
    assert int(alerts) == 0


def test_destino_invalido_nao_altera(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _post(c, number, "mover", token, destino="")
    assert r.status_code == 200 and number in r.text
    assert _row(env, number)["status"] == "awaiting"


def test_mover_ticket_inexistente_avisa(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _post(c, "J-9999", "mover", token, destino="concluida")
        bad = _post(c, "xx", "mover", token, destino="concluida")
    assert r.status_code == 200 and "não encontrado" in _toast(r)
    assert bad.status_code == 200


def test_mover_exige_csrf(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        _page(c)
        r = c.post(f"{PATH}/{number}/mover", data={"destino": "concluida"}, headers=HTMX)
        bad = _post(c, number, "mover", "token-errado", destino="concluida")
    assert r.status_code == 403 and bad.status_code == 403
    assert _row(env, number)["status"] == "awaiting"


# ---- PN-63: urgencia editavel ----------------------------------------------------------


def test_urgencia_no_cartao_muda_e_continua_em_escalados(
    env: Env, seeded: factories.PanelWorld
) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _post(c, number, "urgencia", token, urgency="pouco_urgente")
    assert r.status_code == 200 and number in r.text
    row = _row(env, number)
    assert row["urgency"] == "pouco_urgente" and row["status"] == "awaiting"
    assert _audit(env, "ticket.urgency") == 1


def test_urgencia_invalida_e_recusada(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        r = _post(c, number, "urgencia", token, urgency="explodindo")
    assert r.status_code == 200 and "não aceito" in _toast(r)
    assert _row(env, number)["urgency"] == "muito_urgente"
    assert _audit(env, "ticket.urgency") == 0


def test_urgencia_exige_csrf(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        _page(c)
        r = c.post(f"{PATH}/{number}/urgencia", data={"urgency": "urgente"}, headers=HTMX)
    assert r.status_code == 403
    assert _row(env, number)["urgency"] == "muito_urgente"


def test_acao_na_tela_de_midias_continua_nela(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        token = _csrf(_page(c))
        media = sql(env.dsn, "SELECT ticket_number FROM chat.tickets WHERE message = $1",
                    "[audio 0:24]")
        r = _post(c, media, "urgencia", token, urgency="urgente", midias="1")
    assert "Mídias (2) ×" in r.text and "[foto outra marca]" in r.text


# ---- PN-60/61: frase do resumo 08:05 so com a chave do grupo ligada --------------------


def test_frase_0805_so_com_a_chave_do_grupo_ligada(env: Env, seeded: factories.PanelWorld) -> None:
    with env.client() as c:
        login(c)
        desligada = _page(c)
        sql(env.dsn,
            "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
            "VALUES ('team_group_enabled', 'true'::jsonb, $1, 'teste')", NOW)
        ligada = _page(c)
    assert "08:05" not in desligada and "pelo grupo da equipe" not in desligada
    assert "08:05" in ligada and "pelo grupo da equipe" in ligada


# ---- drawer reaproveitado --------------------------------------------------------------


def test_cartao_abre_o_drawer_e_leva_ao_inbox(env: Env, seeded: factories.PanelWorld) -> None:
    number = _num(seeded, ESC_A)
    with env.client() as c:
        login(c)
        html = _page(c)
        drawer = c.get(f"/dashboard/tickets/{number}")
    assert f'hx-get="/dashboard/tickets/{number}"' in html and 'hx-target="#drawer-body"' in html
    assert f'data-href="/dashboard/inbox?u={seeded.tickets[ESC_A].user_id}"' in html
    assert drawer.status_code == 200 and number in drawer.text
