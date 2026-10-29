"""FaqReader por revisao: propagacao, desativada, bot_responde=false, teto de idade (PN-T15)."""
from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.services.faq_reader import FaqReader
from tests.panel import factories

pytestmark = pytest.mark.db


class CountingDb:
    """Envolve a conexao e conta quantas vezes a tabela de itens foi lida."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self.loads = 0

    async def fetchval(self, query: str, *args: Any) -> Any:
        return await self._conn.fetchval(query, *args)

    async def fetch(self, query: str, *args: Any) -> Any:
        if "content.faq_items" in query:
            self.loads += 1
        return await self._conn.fetch(query, *args)


@pytest.fixture
async def seeded(db_conn: asyncpg.Connection, clock: FakeClock) -> factories.FaqSeed:
    seed = await factories.create_faq(db_conn, clock.now())
    if not seed.available:
        pytest.skip("content.faq_items inexistente")
    return seed


async def _update(conn: asyncpg.Connection, item_id: int, **cols: Any) -> None:
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(cols))  # nomes fixos do teste
    await conn.execute(
        f"UPDATE content.faq_items SET {sets} WHERE id = $1",  # noqa: S608
        item_id,
        *cols.values(),
    )


async def test_le_marca_e_todas(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    items = await FaqReader(db_conn, clock).snapshot(factories.BRAND_A)
    assert {i.brand for i in items} == {factories.BRAND_A, "todas"}


async def test_bot_responde_false_vem_com_a_flag(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    items = await FaqReader(db_conn, clock).snapshot("todas")
    flagged = [i for i in items if not i.bot_responde]
    assert flagged and all(i.answer is None for i in flagged)
    assert any(i.bot_responde for i in items)


async def test_revisao_reflete_aos_59s_sem_esperar_o_teto(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    db = CountingDb(db_conn)
    reader = FaqReader(db, clock)
    first = (await reader.snapshot(factories.BRAND_A))[0]
    assert db.loads == 1
    await reader.snapshot(factories.BRAND_A)
    assert db.loads == 1  # sem mudanca, cache
    await _update(db_conn, first.id, answer="Resposta nova.", bot_responde=True)
    clock.advance(seconds=59)
    again = {i.id: i for i in await reader.snapshot(factories.BRAND_A)}
    assert again[first.id].answer == "Resposta nova."
    assert db.loads == 2


async def test_apos_301s_continua_refletindo(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    reader = FaqReader(db_conn, clock)
    first = (await reader.snapshot(factories.BRAND_A))[0]
    await _update(db_conn, first.id, answer="Outra.", bot_responde=True)
    clock.advance(seconds=301)
    again = {i.id: i for i in await reader.snapshot(factories.BRAND_A)}
    assert again[first.id].answer == "Outra."


async def test_desativada_nao_e_lida(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    reader = FaqReader(db_conn, clock)
    first = (await reader.snapshot(factories.BRAND_A))[0]
    await _update(db_conn, first.id, active=False)
    clock.advance(seconds=1)
    assert first.id not in {i.id for i in await reader.snapshot(factories.BRAND_A)}


async def test_teto_de_idade_recarrega_sem_mudanca_de_revisao(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    db = CountingDb(db_conn)
    reader = FaqReader(db, clock)
    await reader.snapshot(factories.BRAND_A)
    clock.advance(seconds=59)
    await reader.snapshot(factories.BRAND_A)
    assert db.loads == 1
    clock.advance(seconds=1)
    await reader.snapshot(factories.BRAND_A)
    assert db.loads == 2


async def test_update_em_lote_conta_uma_revisao(
    db_conn: asyncpg.Connection, clock: FakeClock, seeded: factories.FaqSeed
) -> None:
    reader = FaqReader(db_conn, clock)
    before = await reader.revision()
    await db_conn.execute("UPDATE content.faq_items SET updated_by = 'lote' WHERE brand = 'todas'")
    assert await reader.revision() == before + 1
