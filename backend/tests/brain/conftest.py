"""Fixtures do cerebro (CB-T02). Banco: reaproveita as de PF (`tests.db_support`).

Testes `db` pulam sem TEST_DATABASE_URL (tests/conftest.py). Valem para unit,
integration e security de tests/brain.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import asyncpg
import httpx
import pytest

from app.core.clock import FakeClock
from tests.brain import factories
from tests.db_support import *  # noqa: F401,F403
from tests.fakes import FakeChannel

BRAIN_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(BRAIN_NOW)


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def gemini() -> factories.FakeGemini:
    return factories.FakeGemini()


@pytest.fixture
async def gemini_client(gemini: factories.FakeGemini) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=gemini.transport, base_url=factories.GEMINI_BASE_URL
    ) as client:
        yield client


@pytest.fixture
async def brain_user(db_conn: asyncpg.Connection, clock: FakeClock) -> int:
    """Vendedor ativo com duas marcas."""
    return await factories.create_active_user(db_conn, clock.now())
