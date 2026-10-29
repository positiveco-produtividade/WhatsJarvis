"""Fixtures do painel (dono: PN-T05).

Banco: reaproveita as de PF (`tests.db_support`: database_url, migrated_database,
db_pool, db_conn, TRUNCATE por teste, recusa banco que nao termine em `_test`).
Testes `db` pulam sem TEST_DATABASE_URL (tests/conftest.py). Valem para todas as
subpastas de tests/panel (unit, integration, security).
"""
from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.clock import FakeClock
from tests.db_support import *  # noqa: F401,F403
from tests.fakes import FakeChannel
from tests.panel import factories
from tests.panel.fakes import FakeIam, ResendSpy, WahaSpy

PANEL_NOW = datetime(2026, 10, 26, 12, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(PANEL_NOW)


@pytest.fixture
def channel() -> FakeChannel:
    return FakeChannel()


@pytest.fixture
def iam() -> FakeIam:
    return FakeIam()


@pytest.fixture
def resend_spy() -> ResendSpy:
    return ResendSpy()


@pytest.fixture
def waha_spy() -> WahaSpy:
    return WahaSpy()


@pytest.fixture
async def world(db_conn: asyncpg.Connection, clock: FakeClock) -> factories.PanelWorld:
    """Cenario padrao semeado (duas marcas, 3 vendedores, tickets, FAQ se existir)."""
    return await factories.seed_world(db_conn, clock.now())
