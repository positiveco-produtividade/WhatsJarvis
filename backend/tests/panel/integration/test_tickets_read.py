"""Leitura de tickets do painel (PN-6, PN-7, PN-8, PN-59) contra Postgres real."""
from __future__ import annotations

from datetime import timedelta

import asyncpg
import pytest

from app.panel.services.scope import Scope
from app.panel.services.tickets_read import (
    PAGE_SIZE,
    TicketFilters,
    parse_filters,
    ticket_kpis,
    tickets_page,
)
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B, PanelWorld

pytestmark = pytest.mark.db

ALL = Scope(None)


async def test_kpis_todas_as_marcas(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    k = await ticket_kpis(db_conn, ALL, now=world.now)
    assert k.total == 12
    assert k.awaiting == 4
    assert k.awaiting_media == 1
    assert k.awaiting_over_48h == 0
    assert k.escalated == 2
    assert k.bot_answers == 3
    assert k.unclassified_pct == 0.0


async def test_kpis_conferem_com_consulta_direta(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    k = await ticket_kpis(db_conn, ALL, now=world.now)
    direct = await db_conn.fetchrow(
        "SELECT count(*) AS n, count(*) FILTER (WHERE status='awaiting') AS a "
        "FROM ops.v_tickets"
    )
    assert direct is not None
    assert (k.total, k.awaiting) == (direct["n"], direct["a"])


async def test_kpi_aguardando_ha_mais_de_48h_e_sem_classificacao(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await factories.insert_ticket(
        db_conn, number=900, user_id=world.sellers.one_brand, status="awaiting",
        created_at=world.now - timedelta(hours=49), brand=BRAND_A,
        classification="NAO_IDENTIFICADO",
    )
    k = await ticket_kpis(db_conn, ALL, now=world.now)
    assert k.total == 13
    assert k.awaiting_over_48h == 1
    assert k.unclassified_pct == pytest.approx(100 / 13)


async def test_kpis_respeitam_periodo(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await factories.insert_ticket(
        db_conn, number=901, user_id=world.sellers.one_brand, status="answered_bot",
        created_at=world.now - timedelta(days=10), brand=BRAND_A,
    )
    assert (await ticket_kpis(db_conn, ALL, now=world.now, days=7)).total == 12
    assert (await ticket_kpis(db_conn, ALL, now=world.now, days=14)).total == 13


async def test_kpis_sem_tickets(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await db_conn.execute("DELETE FROM chat.tickets")
    k = await ticket_kpis(db_conn, ALL, now=world.now)
    assert k.total == 0
    assert k.unclassified_pct is None


async def test_escopo_de_marca_inclui_midia_sem_marca(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    a = await ticket_kpis(db_conn, Scope(BRAND_A), now=world.now)
    b = await ticket_kpis(db_conn, Scope(BRAND_B), now=world.now)
    assert a.total == 8
    assert b.total == 6
    page = await tickets_page(db_conn, Scope(BRAND_B), TicketFilters(), now=world.now)
    assert {r.brand for r in page.rows} == {BRAND_B, None}
    assert all(r.brand == BRAND_B or r.kind == "media" for r in page.rows)


async def test_lista_ordenada_do_mais_novo_e_com_nome(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    page = await tickets_page(db_conn, ALL, TicketFilters(), now=world.now)
    assert page.total == 12
    assert [r.created_at for r in page.rows] == sorted(
        (r.created_at for r in page.rows), reverse=True
    )
    assert page.rows[0].ticket_number == "J-0001"
    assert page.rows[0].user_name == "Vendedor Uma Marca"


async def test_filtros_status_e_classificacao_em_interseccao(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    f = TicketFilters(status="awaiting", classification="PRECO")
    page = await tickets_page(db_conn, ALL, f, now=world.now)
    assert page.total == 2
    assert {(r.status, r.classification) for r in page.rows} == {("awaiting", "PRECO")}
    f2 = TicketFilters(status="awaiting", classification="PRECO")
    only_a = await tickets_page(db_conn, Scope(BRAND_A), f2, now=world.now)
    assert only_a.total == 1


async def test_busca_por_numero_mensagem_e_nome_sem_acento_nem_caixa(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await factories.insert_ticket(
        db_conn, number=902, user_id=world.sellers.one_brand, status="answered_bot",
        created_at=world.now - timedelta(minutes=30), brand=BRAND_A,
        message="Qual o PREÇO da Castanha?",
    )
    by_msg = await tickets_page(db_conn, ALL, TicketFilters(q="preco da CASTANHA"), now=world.now)
    assert [r.ticket_number for r in by_msg.rows] == ["J-0902"]
    by_num = await tickets_page(db_conn, ALL, TicketFilters(q="j-0902"), now=world.now)
    assert by_num.total == 1
    by_name = await tickets_page(db_conn, ALL, TicketFilters(q="DUAS marcas"), now=world.now)
    assert by_name.total == 6
    assert {r.user_name for r in by_name.rows} == {"Vendedor Duas Marcas"}


async def test_busca_trata_curingas_como_texto(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    for term in ("%", "_", "\\"):
        page = await tickets_page(db_conn, ALL, TicketFilters(q=term), now=world.now)
        assert page.total == 0, term


async def test_paginacao_de_25(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    for i in range(30):
        await factories.insert_ticket(
            db_conn, number=1000 + i, user_id=world.sellers.one_brand, status="answered_bot",
            created_at=world.now - timedelta(days=1, minutes=i), brand=BRAND_A,
        )
    p1 = await tickets_page(db_conn, ALL, TicketFilters(page=1), now=world.now)
    p2 = await tickets_page(db_conn, ALL, TicketFilters(page=2), now=world.now)
    p9 = await tickets_page(db_conn, ALL, TicketFilters(page=9), now=world.now)
    assert (len(p1.rows), p1.total, p1.pages) == (PAGE_SIZE, 42, 2)
    assert len(p2.rows) == 17
    assert not {r.id for r in p1.rows} & {r.id for r in p2.rows}
    assert (p9.rows, p9.total) == ([], 42)


async def test_periodo_limita_a_lista(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    await factories.insert_ticket(
        db_conn, number=903, user_id=world.sellers.one_brand, status="answered_bot",
        created_at=world.now - timedelta(days=20), brand=BRAND_A,
    )
    assert (await tickets_page(db_conn, ALL, TicketFilters(days=7), now=world.now)).total == 12
    assert (await tickets_page(db_conn, ALL, TicketFilters(days=30), now=world.now)).total == 13


async def test_lista_vazia(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    page = await tickets_page(db_conn, ALL, TicketFilters(q="nada disso existe"), now=world.now)
    assert (page.rows, page.total, page.pages) == ([], 0, 1)


def test_parse_filters_normaliza_entrada() -> None:
    f = parse_filters(q="  oi ", classification="X", status="awaiting", days="abc", page="-3")
    assert (f.q, f.classification, f.status, f.days, f.page) == ("oi", None, "awaiting", 30, 1)
    g = parse_filters(classification="PRECO", days="7", page="2")
    assert (g.classification, g.days, g.page) == ("PRECO", 7, 2)
    assert parse_filters(days="8").days == 30
