"""Consultas de Relatorios (PN-42, PN-43) contra Postgres real."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.panel.services.reports import (
    field_report,
    month_bounds,
    monthly_report,
    parse_days,
    parse_month,
)
from app.panel.services.scope import Scope
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B, PanelWorld

ALL = Scope(None)


async def _clean(conn: asyncpg.Connection) -> None:
    await conn.execute("DELETE FROM chat.tickets")


async def _add(
    conn: asyncpg.Connection, world: PanelWorld, n: int, text: str, **kw: Any
) -> None:
    kw.setdefault("status", "answered_bot")
    kw.setdefault("created_at", world.now - timedelta(days=1, minutes=n))
    await factories.insert_ticket(
        conn, number=500 + n, user_id=world.sellers.one_brand, message=text, brand=BRAND_A, **kw
    )


@pytest.mark.db
async def test_ranking_agrupa_sem_acento_caixa_e_espacos(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    await _add(db_conn, world, 1, "Validade do Café?")
    await _add(db_conn, world, 2, "  validade  do cafe? ")
    await _add(db_conn, world, 3, "VALIDADE DO CAFÉ?")
    await _add(db_conn, world, 4, "Doses por lata")
    await _add(db_conn, world, 5, "Doses por lata")
    await _add(db_conn, world, 6, "Sabores")
    rep = await field_report(db_conn, ALL, now=world.now)
    assert [i.count for i in rep.top_questions] == [3, 2, 1]
    assert rep.top_questions[0].text == "Validade do Café?"  # a mais recente (n=1)
    assert not rep.is_empty


@pytest.mark.db
async def test_midia_e_mensagem_vazia_ficam_de_fora(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await _clean(db_conn)
    await _add(db_conn, world, 1, "[midia]", kind="media", classification="MIDIA")
    await _add(db_conn, world, 2, "   ")
    rep = await field_report(db_conn, ALL, now=world.now)
    assert rep.is_empty


@pytest.mark.db
async def test_objecoes_so_argumento(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await _clean(db_conn)
    await _add(db_conn, world, 1, "Leite de castanha e caro", classification="ARGUMENTO")
    await _add(db_conn, world, 2, "Leite de castanha e caro", classification="ARGUMENTO")
    await _add(db_conn, world, 3, "Cafeina por dose", classification="PRODUTO")
    rep = await field_report(db_conn, ALL, now=world.now)
    assert [(i.text, i.count) for i in rep.objections] == [("Leite de castanha e caro", 2)]
    assert len(rep.top_questions) == 2


@pytest.mark.db
async def test_sem_resposta_exclui_respondidas_faq_e_marca_desligada(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    factories.skip_without_faq(world)
    await _clean(db_conn)
    await _add(db_conn, world, 1, "Ficha tecnica do Proteico", status="awaiting")
    await _add(db_conn, world, 2, "Ja respondida", status="answered_bot")
    await _add(db_conn, world, 3, "Pergunta de teste numero 1?", status="awaiting")  # na FAQ
    t = await factories.insert_ticket(
        db_conn, number=599, user_id=world.sellers.one_brand, status="awaiting",
        created_at=world.now - timedelta(hours=3), brand=BRAND_A, message="Marca desligada",
    )
    await db_conn.execute(
        "UPDATE chat.tickets SET escalation_reason='brand_off' WHERE id=$1", t.id
    )
    rep = await field_report(db_conn, ALL, now=world.now)
    assert [i.text for i in rep.unanswered] == ["Ficha tecnica do Proteico"]
    assert rep.unanswered[0].ticket_id > 0


@pytest.mark.db
async def test_escopo_de_marca_e_periodo(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await _clean(db_conn)
    await _add(db_conn, world, 1, "Pergunta da A")
    await factories.insert_ticket(
        db_conn, number=700, user_id=world.sellers.two_brands, status="answered_bot",
        created_at=world.now - timedelta(days=1), brand=BRAND_B, message="Pergunta da B",
    )
    await _add(db_conn, world, 2, "Pergunta antiga", created_at=world.now - timedelta(days=20))
    b = await field_report(db_conn, Scope(BRAND_B), now=world.now)
    assert [i.text for i in b.top_questions] == ["Pergunta da B"]
    todas7 = await field_report(db_conn, ALL, now=world.now, days=7)
    assert {i.text for i in todas7.top_questions} == {"Pergunta da A", "Pergunta da B"}
    todas30 = await field_report(db_conn, ALL, now=world.now, days=30)
    assert len(todas30.top_questions) == 3


@pytest.mark.db
async def test_banco_vazio_nao_quebra(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await _clean(db_conn)
    rep = await field_report(db_conn, ALL, now=world.now)
    assert rep.is_empty
    m = await monthly_report(db_conn, ALL, "2026-10")
    assert m.is_empty
    assert m.by_brand == [] and m.by_classification == [] and m.top_questions == []
    assert m.response_p50_seconds is None and m.response_p95_seconds is None


@pytest.mark.db
async def test_mensal_totais_e_tempos(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await _clean(db_conn)
    base = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    await _add(db_conn, world, 1, "Q1", created_at=base)  # respondido em 5 min
    await _add(db_conn, world, 2, "Q1", created_at=base, status="answered_human")
    await _add(
        db_conn, world, 3, "Q2", created_at=base, status="awaiting",
        escalated=True, classification="PRECO",
    )
    # Limites em Sao Paulo (UTC-3): 01/10 02:30 UTC ainda e setembro; 01/11 02:59 UTC e outubro.
    await _add(db_conn, world, 4, "Setembro", created_at=datetime(2026, 10, 1, 2, 30, tzinfo=UTC))
    fim = datetime(2026, 11, 1, 2, 59, tzinfo=UTC)
    await _add(db_conn, world, 5, "Fim de outubro", created_at=fim)
    await _add(db_conn, world, 6, "Novembro", created_at=datetime(2026, 11, 1, 3, 0, tzinfo=UTC))
    m = await monthly_report(db_conn, ALL, "2026-10")
    assert m.total == 4  # Q1, Q1, Q2 e "Fim de outubro"
    assert (m.answered_bot, m.answered_human, m.awaiting, m.escalated) == (2, 1, 1, 1)
    assert m.by_brand == [(BRAND_A, 4)]
    assert dict(m.by_classification)["PRECO"] == 1
    assert m.top_questions[0].count == 2
    assert m.response_p50_seconds == pytest.approx(300.0)
    assert m.response_p95_seconds == pytest.approx(300.0)


@pytest.mark.db
async def test_mensal_respeita_escopo(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await _clean(db_conn)
    base = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)
    await _add(db_conn, world, 1, "Q1", created_at=base)
    assert (await monthly_report(db_conn, Scope(BRAND_B), "2026-10")).total == 0
    assert (await monthly_report(db_conn, Scope(BRAND_A), "2026-10")).total == 1


def test_parse_e_limites() -> None:
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    assert parse_month("2026-09", now) == "2026-09"
    for bad in (None, "", "2026-13", "abc", "2026-9", "2026/09"):
        assert parse_month(bad, now) == "2026-10"
    start, end = month_bounds("2026-12")
    assert start == datetime(2026, 12, 1, 3, 0, tzinfo=UTC)
    assert end == datetime(2027, 1, 1, 3, 0, tzinfo=UTC)
    assert parse_days("7") == 7
    assert parse_days("x") == 30 and parse_days("8") == 30 and parse_days(None) == 30
