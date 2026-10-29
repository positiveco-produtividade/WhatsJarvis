"""Importacao da FAQ: lote, conferencia, gravacao atomica e revalidacao (PN-T29, PN-27..29)."""

from __future__ import annotations

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import faq_admin
from app.panel.services import faq_import as fi
from app.panel.services import import_batches as ib

pytestmark = pytest.mark.db

ACTOR = "caio@example.test"
CSV = (
    "pergunta;resposta;categoria;bot responde\n"
    "Qual o sabor?;Chocolate.;Produto;sim\n"
    "Quanto custa?;;Preço;não\n"
    "Tem link?;veja https://x.com;Produto;sim\n"
    "Ja existe?;Sim.;Produto;sim\n"
    "Sem categoria?;Ok.;;sim\n"
)


@pytest.fixture
async def conn(db_conn: asyncpg.Connection) -> asyncpg.Connection:
    return db_conn


async def _seed_existing(conn: asyncpg.Connection, clock: FakeClock, brand: str = "jungle") -> None:
    res = await faq_admin.create_item(
        conn,
        faq_admin.FaqInput(brand, "PRODUTO", "Ja existe?", "x", (), True),
        actor=ACTOR,
        clock=clock,
    )
    assert res.ok


async def _preview(
    conn: asyncpg.Connection, clock: FakeClock, text: str = CSV, brand: str = "jungle"
) -> ib.BatchRecord:
    up = await fi.load_upload(text.encode("utf-8"), "faq.csv")
    return await fi.create_preview(
        conn, up, filename="dir/sub/faq.csv", brand=brand, actor=ACTOR, clock=clock
    )


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))  # noqa: S608 - constante


async def test_preview_classifies_and_writes_nothing(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    await _seed_existing(conn, clock)
    before = await _count(conn, "content.faq_items")
    batch = await _preview(conn, clock)
    assert batch.status == ib.PREVIEW and batch.brand == "jungle" and batch.filename == "faq.csv"
    assert [r["situacao"] for r in batch.rows] == [
        fi.ENTRA,
        fi.ENTRA,
        fi.ERRO,
        fi.FICA_DE_FORA,
        fi.ENTRA,
    ]
    assert batch.summary == {"entra": 3, "fica_de_fora": 1, "erro": 1, "total": 5, "conferir": 1}
    assert await _count(conn, "content.faq_items") == before
    reopened = await ib.get_batch(conn, batch.id, clock=clock)
    assert reopened is not None and reopened.rows == batch.rows


async def test_preview_todas_brand_counts_as_existing(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    await _seed_existing(conn, clock, brand="todas")
    batch = await _preview(conn, clock)
    assert batch.rows[3]["situacao"] == fi.FICA_DE_FORA


async def test_preview_rejects_bad_brand(conn: asyncpg.Connection, clock: FakeClock) -> None:
    with pytest.raises(fi.ImportFileError) as exc:
        await _preview(conn, clock, brand="nope")
    assert exc.value.code == "bad_brand"


async def test_commit_inserts_entering_rows(conn: asyncpg.Connection, clock: FakeClock) -> None:
    await _seed_existing(conn, clock)
    batch = await _preview(conn, clock)
    res = await fi.commit_import(
        conn, batch.id, actor=ACTOR, categories={6: "ARGUMENTO"}, clock=clock
    )
    assert res is not None and res.imported == 3 and res.skipped == 2
    rows = await conn.fetch(
        "SELECT question, category, answer, bot_responde, origin, origin_ref, active "
        "FROM content.faq_items WHERE origin = 'import' ORDER BY id"
    )
    assert [r["question"] for r in rows] == ["Qual o sabor?", "Quanto custa?", "Sem categoria?"]
    assert [r["category"] for r in rows] == ["PRODUTO", "PRECO", "ARGUMENTO"]
    assert rows[1]["answer"] is None and rows[1]["bot_responde"] is False
    assert all(r["origin_ref"] == str(batch.id) and r["active"] for r in rows)
    kinds = await conn.fetchval(
        "SELECT count(*) FROM content.faq_item_versions WHERE change_kind = 'import'"
    )
    assert kinds == 3
    audits = await conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE action = 'faq.import'")
    assert audits == 1
    done = await ib.get_batch(conn, batch.id, clock=clock)
    assert done is not None and done.status == ib.COMMITTED


async def test_second_commit_does_not_duplicate(conn: asyncpg.Connection, clock: FakeClock) -> None:
    batch = await _preview(conn, clock)
    assert await fi.commit_import(conn, batch.id, actor=ACTOR, clock=clock) is not None
    count = await _count(conn, "content.faq_items")
    assert await fi.commit_import(conn, batch.id, actor=ACTOR, clock=clock) is None
    assert await _count(conn, "content.faq_items") == count


async def test_commit_revalidates_new_duplicate_stays_out(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _preview(conn, clock)
    await _seed_existing(
        conn, clock
    )  # "Ja existe?" entra depois da conferencia? ja fora; usa outra
    res = await faq_admin.create_item(
        conn,
        faq_admin.FaqInput("jungle", "PRODUTO", "Qual o sabor?", "outra", (), True),
        actor=ACTOR,
        clock=clock,
    )
    assert res.ok
    out = await fi.commit_import(conn, batch.id, actor=ACTOR, clock=clock)
    assert out is not None and out.imported == 2 and out.skipped == 3
    questions = await conn.fetch(
        "SELECT answer FROM content.faq_items WHERE question = 'Qual o sabor?'"
    )
    assert [r["answer"] for r in questions] == ["outra"]


async def test_failure_rolls_back_everything(conn: asyncpg.Connection, clock: FakeClock) -> None:
    batch = await _preview(conn, clock)
    before = await _count(conn, "content.faq_items")
    with pytest.raises(fi.ImportCommitError) as exc:
        await fi.commit_import(conn, batch.id, actor=ACTOR, categories={3: "NOPE"}, clock=clock)
    assert exc.value.code == "bad_category"
    assert await _count(conn, "content.faq_items") == before
    again = await ib.get_batch(conn, batch.id, clock=clock)
    assert again is not None and again.status == ib.PREVIEW


async def test_failure_midway_reverts_inserted_rows(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    batch = await _preview(conn, clock)
    # Corrompe a 2a linha que entra no lote: resposta com link, so detectavel na revalidacao.
    rows = [dict(r) for r in batch.rows]
    rows[1]["resposta"] = "veja https://x.com"
    rows[1]["bot_responde"] = True
    import json

    await conn.execute(
        "UPDATE ops.import_batches SET rows = $2::jsonb WHERE id = $1", batch.id, json.dumps(rows)
    )
    before = await _count(conn, "content.faq_items")
    with pytest.raises(fi.ImportCommitError) as exc:
        await fi.commit_import(conn, batch.id, actor=ACTOR, clock=clock)
    assert exc.value.code == "revalidation_failed" and exc.value.line == 3
    assert await _count(conn, "content.faq_items") == before
    assert await _count(conn, "content.faq_item_versions") == 0
    status = await conn.fetchval("SELECT status FROM ops.import_batches WHERE id = $1", batch.id)
    assert status == ib.PREVIEW


async def test_cancelled_and_expired_batches_do_not_commit(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    b1 = await _preview(conn, clock)
    assert await ib.cancel_batch(conn, b1.id, clock=clock)
    assert await fi.commit_import(conn, b1.id, actor=ACTOR, clock=clock) is None
    b2 = await _preview(conn, clock, text="pergunta;resposta\nZ?;ok\n")
    clock.advance(hours=3)
    assert await fi.commit_import(conn, b2.id, actor=ACTOR, clock=clock) is None
    assert await _count(conn, "content.faq_items") == 0
