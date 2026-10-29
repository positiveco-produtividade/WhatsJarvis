"""Contadores de seguranca: incremento em memoria e flush em lote (AC-2/AC-4, 4.6)."""

from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.services.counters import KINDS, SecurityCounters


async def _rows(conn: asyncpg.Connection) -> dict[tuple[str, str], int]:
    rows = await conn.fetch("SELECT day, kind, count FROM ops.security_counters")
    return {(r["day"].isoformat(), r["kind"]): r["count"] for r in rows}


def test_incr_acumula_em_memoria_por_dia_e_kind() -> None:
    clock = FakeClock(datetime(2026, 3, 1, 23, 0, tzinfo=UTC))
    c = SecurityCounters(clock)
    c.incr("invalid_signature")
    c.incr("invalid_signature", 2)
    clock.advance(hours=2)
    c.incr("invalid_signature")
    pend = {(d.isoformat(), k): v for (d, k), v in c.pending().items()}
    assert pend == {("2026-03-01", "invalid_signature"): 3, ("2026-03-02", "invalid_signature"): 1}


def test_kind_e_quantidade_invalidos_recusados() -> None:
    c = SecurityCounters(FakeClock())
    with pytest.raises(InvalidValue):
        c.incr("outro")
    with pytest.raises(InvalidValue):
        c.incr("invalid_json", 0)
    assert c.pending() == {}


def test_kinds_da_spec() -> None:
    assert set(KINDS) == {
        "invalid_signature",
        "oversize_payload",
        "bad_verify_token",
        "invalid_json",
        "wrong_recipient",
    }


@pytest.mark.db
async def test_flush_grava_em_lote_e_zera_a_memoria(db_conn: asyncpg.Connection) -> None:
    c = SecurityCounters(FakeClock())
    c.incr("invalid_signature", 3)
    c.incr("oversize_payload")
    c.incr("wrong_recipient", 2)
    assert await c.flush(db_conn) == 3
    assert c.pending() == {}
    assert await _rows(db_conn) == {
        ("2026-01-01", "invalid_signature"): 3,
        ("2026-01-01", "oversize_payload"): 1,
        ("2026-01-01", "wrong_recipient"): 2,
    }


@pytest.mark.db
async def test_flush_soma_ao_valor_existente(db_conn: asyncpg.Connection) -> None:
    c = SecurityCounters(FakeClock())
    c.incr("bad_verify_token", 2)
    await c.flush(db_conn)
    c.incr("bad_verify_token", 5)
    c.incr("invalid_json")
    await c.flush(db_conn)
    assert await _rows(db_conn) == {
        ("2026-01-01", "bad_verify_token"): 7,
        ("2026-01-01", "invalid_json"): 1,
    }


@pytest.mark.db
async def test_flush_vazio_nao_grava(db_conn: asyncpg.Connection) -> None:
    assert await SecurityCounters(FakeClock()).flush(db_conn) == 0
    assert await _rows(db_conn) == {}


class _BrokenConn:
    async def execute(self, *args: object) -> None:
        raise ConnectionError("falha simulada")


@pytest.mark.db
async def test_flush_com_falha_devolve_valores_para_a_memoria(
    db_conn: asyncpg.Connection,
) -> None:
    c = SecurityCounters(FakeClock())
    c.incr("invalid_signature", 4)
    with pytest.raises(ConnectionError):
        await c.flush(_BrokenConn())
    assert sum(c.pending().values()) == 4
    assert await c.flush(db_conn) == 1
    assert await _rows(db_conn) == {("2026-01-01", "invalid_signature"): 4}
