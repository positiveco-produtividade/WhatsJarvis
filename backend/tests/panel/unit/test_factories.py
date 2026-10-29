"""Valida as fabricas e dubles do painel (PN-T05)."""
from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import httpx
import pytest

from tests.panel import factories
from tests.panel.fakes import FakeIam, IamMode, ResendSpy, WahaSpy

STATUSES = {"answered_bot", "answered_human", "awaiting", "send_error"}


@pytest.mark.db
async def test_two_brands_and_three_sellers(
    db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    assert len(world.brands) == 2
    rows = await db_conn.fetch("SELECT id, brands FROM access.users ORDER BY id")
    by_id = {r["id"]: list(r["brands"]) for r in rows}
    assert len(by_id) == 3
    assert len(by_id[world.sellers.one_brand]) == 1
    assert len(by_id[world.sellers.two_brands]) == 2
    media = await db_conn.fetch(
        "SELECT kind, brand FROM chat.tickets WHERE user_id = $1", world.sellers.media_only
    )
    assert media and all(r["kind"] == "media" and r["brand"] is None for r in media)


@pytest.mark.db
async def test_tickets_cover_every_status(
    db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    rows = await db_conn.fetch("SELECT status, escalated, urgency FROM chat.tickets")
    assert {r["status"] for r in rows} == STATUSES
    assert len(rows) == len(world.tickets)
    assert any(r["escalated"] for r in rows)
    assert all(r["urgency"] for r in rows if r["escalated"])
    assert await db_conn.fetchval("SELECT count(*) FROM ops.v_tickets") == len(world.tickets)


@pytest.mark.db
async def test_seed_does_not_accumulate_between_tests(
    db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    # TRUNCATE por teste (PF): continua com exatamente 3 vendedores.
    assert await db_conn.fetchval("SELECT count(*) FROM access.users") == 3


@pytest.mark.db
async def test_faq_has_25_items_or_is_skipped(
    db_conn: asyncpg.Connection, world: factories.PanelWorld
) -> None:
    factories.skip_without_faq(world)  # migration 101 chega em PN-T08
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_items") == 25
    assert len(world.faq.ids) == factories.FAQ_SIZE
    again = await factories.create_faq(db_conn, world.now)
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_items") == 25
    assert again.available


@pytest.mark.db
async def test_faq_factory_is_tolerant_without_table(db_conn: asyncpg.Connection) -> None:
    if await factories.faq_table_exists(db_conn):
        pytest.skip("a tabela existe; o caminho tolerante nao se aplica")
    now = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)
    assert await factories.create_faq(db_conn, now) == factories.FaqSeed(available=False)


def test_normalize_question() -> None:
    assert factories.normalize_question("  Qual o PREÇO   da Castanha? ") == (
        "qual o preco da castanha?"
    )


@pytest.mark.parametrize(
    ("mode", "status"), [("ok", 200), ("unauthorized", 401), ("server_error", 503)]
)
async def test_fake_iam_status(mode: IamMode, status: int) -> None:
    fake = FakeIam(mode=mode)
    async with fake.client() as client:
        response = await client.get("/api/v1/auth/whoami")
    assert response.status_code == status
    assert fake.calls == 1
    if status == 200:
        assert response.json()["email"] == "admin@example.test"


async def test_fake_iam_timeout() -> None:
    fake = FakeIam(mode="timeout")
    async with fake.client() as client:
        with pytest.raises(httpx.TimeoutException):
            await client.get("/api/v1/auth/whoami")


async def test_http_spies_record_and_can_fail() -> None:
    resend, waha = ResendSpy(), WahaSpy()
    async with resend.client() as rc, waha.client() as wc:
        ok = await rc.post("https://api.resend.com/emails", json={"subject": "x"})
        assert ok.status_code == 200
        resend.fail_with = 500
        bad = await rc.post("https://api.resend.com/emails", json={"subject": "y"})
        assert bad.status_code == 500
        assert waha.calls == 0
        await wc.post("https://waha.example.test/api/sendText", json={"text": "oi"})
    assert resend.calls == 2
    assert resend.emails()[0]["subject"] == "x"
    assert waha.calls == 1
