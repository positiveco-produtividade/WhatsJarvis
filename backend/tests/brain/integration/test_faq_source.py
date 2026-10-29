"""FaqSource contra o Postgres de teste (CB-17, CB-28). Itens SINTETICOS."""
from __future__ import annotations

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services.brain.faq_source import FaqSource
from app.services.faq_reader import FaqReader
from tests.brain import factories

pytestmark = pytest.mark.db

Q = "Qual o sabor do produto exemplo A?"
A = factories.BRAND_A


def _source(conn: asyncpg.Connection, clock: FakeClock) -> FaqSource:
    return FaqSource(FaqReader(conn, clock=clock))


async def test_busca_so_marcas_permitidas(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    await factories.create_faq_items(db_conn, clock.now(), brand=A)
    await factories.create_faq_items(db_conn, clock.now(), brand=factories.BRAND_B)
    out = await _source(db_conn, clock).search(Q, brands=[A], limit=10, floor=0.1, conn=db_conn)
    assert out and {s.item.brand for s in out} == {A}
    assert out[0].item.ref.startswith("faq:") and out[0].item.answerable


async def test_origem_vem_do_banco_e_edicao_reflete(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    ids = await factories.create_faq_items(db_conn, clock.now(), brand=A)
    await db_conn.execute(
        "UPDATE content.faq_items SET origin = 'nielsen', "
        "answer = 'Dado sintetico. (Fonte: Nielsen sintetico)' WHERE id = $1",
        ids[0],
    )
    src = _source(db_conn, clock)
    ref = f"faq:{ids[0]}"
    first = await src.search(Q, brands=[A], limit=10, floor=0.1, conn=db_conn)
    top = next(s.item for s in first if s.item.ref == ref)
    assert top.origin == "nielsen" and "(Fonte: Nielsen sintetico)" in top.text
    await db_conn.execute(
        "UPDATE content.faq_items SET answer = 'Texto editado.' WHERE id = $1", ids[0]
    )
    second = await src.search(Q, brands=[A], limit=10, floor=0.1, conn=db_conn)
    assert next(s.item for s in second if s.item.ref == ref).text == "Texto editado."
    await db_conn.execute("UPDATE content.faq_items SET active = false WHERE id = $1", ids[0])
    third = await src.search(Q, brands=[A], limit=10, floor=0.1, conn=db_conn)
    assert all(s.item.ref != ref for s in third)


async def test_bot_responde_false_nao_e_respondivel(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    ids = await factories.create_faq_items(db_conn, clock.now(), brand=A)
    await db_conn.execute("UPDATE content.faq_items SET bot_responde = false WHERE id = $1", ids[0])
    out = await _source(db_conn, clock).search(Q, brands=[A], limit=10, floor=0.1, conn=db_conn)
    assert next(s.item for s in out if s.item.ref == f"faq:{ids[0]}").answerable is False


async def test_todas_sem_duplicar(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    await factories.create_faq_items(db_conn, clock.now(), brand="todas")
    out = await _source(db_conn, clock).search(
        Q, brands=[A, factories.BRAND_B], limit=10, floor=0.1, conn=db_conn
    )
    refs = [s.item.ref for s in out]
    assert refs and len(refs) == len(set(refs))
