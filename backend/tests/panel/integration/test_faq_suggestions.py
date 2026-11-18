"""Sugestoes para a FAQ em Relatorios e criacao do item (retorno-humano.1; task RH-T10).

RH-17 (lista, total, filtro, marca em foco), RH-18 (formulario preenchido; item com origem
`sugestao` e sugestao aprovada na mesma transacao), RH-19 (descartar). Postgres real.
"""
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
from tests.panel.integration.test_access import (  # noqa: F401
    ADMIN,
    NOW,
    Env,
    env,
    login,
    post_headers,
    sql,
)

pytestmark = pytest.mark.db

REL = "/dashboard/relatorios"
SUG = REL + "/sugestoes"
FAQ_NEW = "/dashboard/faq/nova"
NEW_ITEMS = "SELECT count(*) FROM content.faq_items WHERE origin = 'sugestao'"


async def _seed(dsn: str) -> factories.PanelWorld:
    conn = await asyncpg.connect(dsn)
    try:
        return await factories.seed_world(conn, NOW)
    finally:
        await conn.close()


@pytest.fixture
def seeded(env: Env) -> factories.PanelWorld:
    return asyncio.run(_seed(env.dsn))


def _suggestion(
    env: Env,
    world: factories.PanelWorld,
    index: int,
    *,
    brand: str | None = "jungle",
    question: str = "Tem versão sem lactose?",
    answer: str = "Temos sim, a linha Zero.",
    status: str = "pendente",
    age_minutes: int = 0,
) -> int:
    ticket = world.tickets[index]
    decided = None if status == "pendente" else NOW
    return int(
        sql(
            env.dsn,
            "INSERT INTO content.faq_suggestions (ticket_id, ticket_number, user_id, brand, "
            "category, question, answer, status, author, decided_by, decided_at, created_at, "
            "updated_at) VALUES ($1, $2, $3, $4, 'PRODUTO', $5, $6, $7, 'Caio Franco', "
            "CASE WHEN $8::timestamptz IS NULL THEN NULL ELSE 'Bia' END, $8, $9, $9) RETURNING id",
            ticket.id,
            ticket.ticket_number,
            ticket.user_id,
            brand,
            question,
            answer,
            status,
            decided,
            NOW - timedelta(minutes=age_minutes),
        )
    )


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _post(c: TestClient, url: str, token: str, **data: Any) -> Any:
    return c.post(url, data=data, headers={**post_headers(token), "HX-Request": "true"})


def test_card_lista_pendentes_mais_recentes_primeiro(
    env: Env, seeded: factories.PanelWorld
) -> None:
    old = _suggestion(env, seeded, 0, question="Pergunta antiga", age_minutes=60)
    new = _suggestion(env, seeded, 1, question="Pergunta nova")
    _suggestion(env, seeded, 2, question="Ja descartada", status="descartada")
    with env.client() as c:
        login(c)
        html = c.get(REL).text
    assert "Sugestões para a FAQ" in html and "(2 pendentes)" in html
    assert html.index(f'data-suggestion="{new}"') < html.index(f'data-suggestion="{old}"')
    assert "Ja descartada" not in html
    assert "Temos sim, a linha Zero." in html and "por Caio Franco" in html
    assert seeded.tickets[1].ticket_number in html
    assert f"{FAQ_NEW}?sugestao={new}" in html


def test_filtro_todas_mostra_decididas(env: Env, seeded: factories.PanelWorld) -> None:
    _suggestion(env, seeded, 0, question="Ja descartada", status="descartada")
    with env.client() as c:
        login(c)
        pend = c.get(SUG, params={"status": "pendentes"}, headers={"HX-Request": "true"}).text
        todas = c.get(SUG, params={"status": "todas"}, headers={"HX-Request": "true"}).text
    assert "Ja descartada" not in pend and "Nenhuma sugestão pendente" in pend
    assert "Ja descartada" in todas and "Descartada por Bia" in todas
    assert "<html" not in todas  # fragmento


def test_marca_em_foco_filtra_e_sem_marca_aparece_em_todas(
    env: Env, seeded: factories.PanelWorld
) -> None:
    _suggestion(env, seeded, 0, brand="jungle", question="Da Jungle")
    _suggestion(env, seeded, 1, brand="zaya", question="Da Zaya")
    _suggestion(env, seeded, 2, brand=None, question="Sem marca")
    with env.client() as c:
        login(c)
        c.cookies.set("jarvis_brand", "jungle", domain="jarvis.positiveco.com.br")
        html = c.get(SUG, headers={"HX-Request": "true"}).text
    assert "Da Jungle" in html and "Sem marca" in html and "Da Zaya" not in html


def test_descartar(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    with env.client() as c:
        login(c)
        token = _csrf(c.get(REL).text)
        r = _post(c, f"{SUG}/{sid}/descartar", token, status="pendentes")
        again = _post(c, f"{SUG}/{sid}/descartar", token, status="pendentes")
    assert r.status_code == 200 and "Sugestão descartada." in r.text
    assert f'data-suggestion="{sid}"' not in r.text
    assert "Esta sugestão já tinha sido decidida." in again.text
    row = sql(env.dsn, "SELECT status || '|' || decided_by FROM content.faq_suggestions")
    assert row == f"descartada|{ADMIN}"
    assert sql(
        env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'faq_suggestion.discard'"
    ) == 1


def test_descartar_exige_csrf(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    with env.client() as c:
        login(c)
        r = c.post(f"{SUG}/{sid}/descartar", headers={"HX-Request": "true"})
    assert r.status_code == 403
    assert sql(env.dsn, "SELECT status FROM content.faq_suggestions") == "pendente"


def test_formulario_preenchido_pela_sugestao(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    number = seeded.tickets[0].ticket_number
    with env.client() as c:
        login(c)
        html = c.get(FAQ_NEW, params={"sugestao": sid}).text
    assert f'name="sugestao" value="{sid}"' in html
    assert "Tem versão sem lactose?" in html and "Temos sim, a linha Zero." in html
    assert f"resposta da equipe no ticket <strong class=\"font-mono\">{number}</strong>" in html
    assert '<option value="jungle" selected' in html


def test_criar_item_aprova_a_sugestao(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    number = seeded.tickets[0].ticket_number
    with env.client() as c:
        login(c)
        token = _csrf(c.get(FAQ_NEW, params={"sugestao": sid}).text)
        r = _post(
            c, FAQ_NEW, token, sugestao=str(sid), brand="jungle", category="PRODUTO",
            question="Tem versão sem lactose?", answer="Sim: a linha Zero não tem lactose.",
            bot_responde="1",
        )  # fmt: skip
    assert r.status_code == 200 and r.headers["HX-Reswap"] == "none"
    events = json.loads(r.headers["HX-Trigger"])
    assert "suggestionsChanged" in events and "faqChanged" in events
    item = asyncio.run(_one(env.dsn, "SELECT * FROM content.faq_items WHERE origin = 'sugestao'"))
    assert (item["origin"], item["origin_ref"], item["active"]) == ("sugestao", number, True)
    assert item["answer"] == "Sim: a linha Zero não tem lactose."
    sug = asyncio.run(_one(env.dsn, "SELECT * FROM content.faq_suggestions"))
    assert (sug["status"], sug["decided_by"], sug["faq_item_id"]) == ("aprovada", ADMIN, item["id"])
    actions = sql(
        env.dsn,
        "SELECT string_agg(action, ',' ORDER BY id) FROM ops.audit_log "
        "WHERE action IN ('faq.create', 'faq_suggestion.approve')",
    )
    assert actions == "faq.create,faq_suggestion.approve"


def test_sugestao_ja_decidida_nao_cria_item(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    with env.client() as c:
        login(c)
        token = _csrf(c.get(FAQ_NEW, params={"sugestao": sid}).text)
        sql(
            env.dsn,
            "UPDATE content.faq_suggestions SET status = 'descartada', decided_by = 'Bia', "
            "decided_at = $1",
            NOW,
        )
        r = _post(
            c, FAQ_NEW, token, sugestao=str(sid), brand="jungle", category="PRODUTO",
            question="Tem versão sem lactose?", answer="Sim.", bot_responde="1",
        )  # fmt: skip
        reopened = c.get(FAQ_NEW, params={"sugestao": sid}).text
    assert "Esta sugestão já foi decidida" in r.text
    assert sql(env.dsn, NEW_ITEMS) == 0
    assert "Esta sugestão já foi decidida" in reopened


def test_problema_de_validacao_mantem_a_sugestao(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    with env.client() as c:
        login(c)
        token = _csrf(c.get(FAQ_NEW, params={"sugestao": sid}).text)
        r = _post(
            c, FAQ_NEW, token, sugestao=str(sid), brand="jungle", category="PRODUTO",
            question="", answer="Sim.", bot_responde="1",
        )  # fmt: skip
    assert f'name="sugestao" value="{sid}"' in r.text
    assert sql(env.dsn, "SELECT status FROM content.faq_suggestions") == "pendente"
    assert sql(env.dsn, NEW_ITEMS) == 0


def test_cancelar_nao_muda_a_sugestao(env: Env, seeded: factories.PanelWorld) -> None:
    sid = _suggestion(env, seeded, 0)
    with env.client() as c:
        login(c)
        c.get(FAQ_NEW, params={"sugestao": sid})
    assert sql(env.dsn, "SELECT status FROM content.faq_suggestions") == "pendente"


def test_prefill_por_ticket_continua_igual(env: Env, seeded: factories.PanelWorld) -> None:
    number = seeded.tickets[2].ticket_number
    with env.client() as c:
        login(c)
        html = c.get(FAQ_NEW, params={"ticket": number}).text
    assert f"Preenchido a partir do ticket <strong class=\"font-mono\">{number}</strong>" in html
    assert 'name="sugestao"' not in html


async def _one(dsn: str, query: str) -> asyncpg.Record:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(query)
        assert row is not None
        return row
    finally:
        await conn.close()
