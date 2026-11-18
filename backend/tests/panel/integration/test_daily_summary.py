"""Resumo diario das 08:05 no grupo da equipe (painel.1 PN-61, task PN-T72)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
import httpx
import pytest

from app.config import Settings
from app.core.clock import FakeClock
from app.panel.services import daily_summary as ds
from app.panel.services.workers import discover_loops
from app.services.flags import Flags
from app.services.notifications import DeliveryResult, DeliveryStatus
from tests.brain import factories

pytestmark = pytest.mark.db

TZ = ds.TZ
# Quarta-feira, 18/11/2026, horario de Fortaleza.
DAY = datetime(2026, 11, 18, 8, 5, tzinfo=TZ)
SELLER_NAME = "Vendedora Resumo Teste"


class SendSpy:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.texts: list[str] = []

    async def __call__(
        self, text: str, cfg: Settings, client: httpx.AsyncClient | None
    ) -> DeliveryResult:
        self.texts.append(text)
        if self.ok:
            return DeliveryResult(DeliveryStatus.OK)
        return DeliveryResult(DeliveryStatus.TRANSIENT, "waha_down")


def _settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="postgresql://u:p@localhost/db_test",  # type: ignore[arg-type]
        panel_base_url="https://jarvis.example.test",
    )


def _deps(pool: asyncpg.Pool, clock: FakeClock, spy: SendSpy, *, group: bool = True) -> Any:
    return ds.SummaryDeps(
        settings=_settings(),
        flags=Flags(pool, clock, team_group_initial=group),
        clock=clock,
        send=spy,
    )


async def _ticket(
    conn: asyncpg.Connection,
    user_id: int,
    number: str,
    created: datetime,
    *,
    urgency: str = "urgente",
    status: str = "awaiting",
    reason: str = "no_answer",
) -> None:
    await conn.execute(
        "INSERT INTO chat.tickets (ticket_number, user_id, kind, brand, message, status, "
        "escalated, urgency, escalation_reason, created_at, updated_at) "
        "VALUES ($1, $2, 'question', 'jungle', 'Pergunta de teste?', $3, true, $4, $5, $6, $6)",
        number, user_id, status, urgency, reason, created,
    )  # fmt: skip


async def _sent_count(conn: asyncpg.Connection) -> int:
    return int(
        await conn.fetchval("SELECT count(*) FROM ops.audit_log WHERE action = $1", ds.AUDIT_ACTION)
    )


async def test_so_a_partir_das_0805_e_uma_vez_por_dia(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection
) -> None:
    clock = FakeClock(DAY - timedelta(minutes=1))
    spy = SendSpy()
    deps = _deps(db_pool, clock, spy)
    assert await ds.run_if_due(db_conn, deps) == "not_due"
    clock.set(DAY)
    assert await ds.run_if_due(db_conn, deps) == "sent"
    clock.advance(minutes=1)
    assert await ds.run_if_due(db_conn, deps) == "not_due"  # mesmo dia: nao repete
    clock.set(DAY + timedelta(hours=10))
    assert await ds.run_if_due(db_conn, deps) == "not_due"
    clock.set(DAY + timedelta(days=1))
    assert await ds.run_if_due(db_conn, deps) == "sent"  # dia seguinte
    assert len(spy.texts) == 2 and await _sent_count(db_conn) == 2


async def test_fim_de_semana_tambem_sai(db_pool: asyncpg.Pool, db_conn: asyncpg.Connection) -> None:
    saturday = DAY + timedelta(days=3)
    assert saturday.weekday() == 5
    spy = SendSpy()
    assert await ds.run_if_due(db_conn, _deps(db_pool, FakeClock(saturday), spy)) == "sent"


async def test_sem_pendentes_manda_zero(db_pool: asyncpg.Pool, db_conn: asyncpg.Connection) -> None:
    spy = SendSpy()
    await ds.run_if_due(db_conn, _deps(db_pool, FakeClock(DAY), spy))
    assert spy.texts == ["📊 Jarvis — 0 pendentes (18/11/2026)"]


async def test_texto_com_contagens_mais_antigo_e_link(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection
) -> None:
    uid = await factories.create_active_user(db_conn, DAY)
    await db_conn.execute("UPDATE access.users SET name = $1 WHERE id = $2", SELLER_NAME, uid)
    await _ticket(db_conn, uid, "J-0101", DAY - timedelta(days=3), urgency="muito_urgente")
    await _ticket(db_conn, uid, "J-0102", DAY - timedelta(hours=1))
    await _ticket(db_conn, uid, "J-0103", DAY - timedelta(days=1), urgency="pouco_urgente",
                  reason="brand_access_request")  # fmt: skip
    await _ticket(db_conn, uid, "J-0104", DAY - timedelta(days=9), status="answered_human")
    await _ticket(db_conn, uid, "J-0105", DAY - timedelta(days=2), status="send_error")
    spy = SendSpy()
    await ds.run_if_due(db_conn, _deps(db_pool, FakeClock(DAY), spy))
    (text,) = spy.texts
    assert text == (
        "📊 Jarvis — pendentes (18/11/2026)\n"
        "⏳ Escalados aguardando resposta: 3\n"
        "   🔴 Muito urgentes: 1 · 🟠 Urgentes: 1 · 🟢 Pouco urgentes: 1\n"
        "🕐 Mais antigo: J-0101, há 3 dias\n"
        "🔑 Pedidos de acesso a marca: 1\n"
        "⚠ Respostas da equipe que falharam: 1\n"
        "Abrir: https://jarvis.example.test/dashboard/escalados"
    )
    assert SELLER_NAME not in text and "+55" not in text


async def test_grupo_desligado_nao_manda_e_manda_quando_ligar(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection
) -> None:
    clock = FakeClock(DAY)
    spy = SendSpy()
    assert await ds.run_if_due(db_conn, _deps(db_pool, clock, spy, group=False)) == "group_off"
    assert spy.texts == [] and await _sent_count(db_conn) == 0
    clock.advance(hours=2)
    assert await ds.run_if_due(db_conn, _deps(db_pool, clock, spy, group=True)) == "sent"


async def test_waha_fora_tenta_no_proximo_ciclo_sem_repetir(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection
) -> None:
    clock = FakeClock(DAY)
    down = SendSpy(ok=False)
    assert await ds.run_if_due(db_conn, _deps(db_pool, clock, down)) == "failed"
    assert await _sent_count(db_conn) == 0
    clock.advance(minutes=1)
    up = SendSpy()
    assert await ds.run_if_due(db_conn, _deps(db_pool, clock, up)) == "sent"
    clock.advance(minutes=1)
    assert await ds.run_if_due(db_conn, _deps(db_pool, clock, up)) == "not_due"
    assert len(up.texts) == 1 and await _sent_count(db_conn) == 1


def test_laco_registrado() -> None:
    loops = {loop.name: loop for loop in discover_loops()}
    assert loops["daily_summary"].interval_seconds == 60


def test_agendamento_em_fortaleza() -> None:
    utc_0805 = datetime(2026, 11, 18, 11, 5, tzinfo=ZoneInfo("UTC"))  # 08:05 em Fortaleza (UTC-3)
    assert ds.is_due(utc_0805, None)
    assert not ds.is_due(utc_0805 - timedelta(seconds=1), None)
    assert not ds.is_due(utc_0805 + timedelta(hours=1), utc_0805)


