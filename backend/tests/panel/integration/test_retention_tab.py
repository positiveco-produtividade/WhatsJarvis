"""Aba Retencao de Logs: volume, expira em 30 dias, dry-run, ultima execucao (PN-58; PN-T62)."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.api.logs_retencao import parse_last_run
from app.panel.services import retention
from app.services import audit
from tests.panel import factories
from tests.panel.integration.test_access import (  # noqa: F401
    Env,
    env,
    login,
    sql,
)

pytestmark = pytest.mark.db

URL = "/dashboard/logs/retencao"
HX = {"HX-Request": "true"}


async def _seed_traces(dsn: str, moments: list[datetime]) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        uid = await factories.insert_user(
            conn, name="Vendedor T", seq=62, brands=["jungle"], now=datetime.now(UTC)
        )
        for i, at in enumerate(moments):
            await conn.execute(
                "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
                "access_decision, decision) VALUES ($1, $2, 'text', $3, 'allowed', 'answered')",
                f"wamid.tab.{i}",
                at,
                uid,
            )
    finally:
        await conn.close()


async def _seed_run(dsn: str, *, dry_run: bool) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await audit.write(
            conn,
            "system",
            "retention",
            retention.AUDIT_ACTION,
            "retention",
            "purge",
            None,
            {"dry_run": dry_run, "cutoff": "2025-10-26", "months": 12, "counts": {"tickets": 7}},
            clock=FakeClock(datetime.now(UTC)),
        )
    finally:
        await conn.close()


def test_vazio_mostra_estados_vazios_e_exige_admin(env: Env) -> None:
    with env.client() as c:
        assert c.get(URL).status_code == 302
        login(c)
        r = c.get(URL, headers=HX)
    assert r.status_code == 200
    assert "Nenhum registro dentro do período de retenção." in r.text
    assert "A rotina ainda não rodou." in r.text
    assert "Expira nos próximos 30 dias" in r.text


def test_volume_por_mes_e_expiracao_em_30_dias(env: Env) -> None:
    now = datetime.now(UTC)
    cutoff = retention.months_back(now, 12)
    asyncio.run(
        _seed_traces(
            env.dsn,
            [now - timedelta(days=1), now - timedelta(days=2), cutoff + timedelta(days=10)],
        )
    )
    with env.client() as c:
        login(c)
        r = c.get(URL, headers=HX)
    assert r.status_code == 200
    assert f"{now.month:02d}/{now.year}" in r.text
    assert "1 mensagens" in r.text


def test_dry_run_e_ultima_execucao(env: Env) -> None:
    asyncio.run(_seed_run(env.dsn, dry_run=True))
    with env.client(retention_dry_run=True) as c:
        login(c)
        dry = c.get(URL, headers=HX).text
    assert "Em simulação" in dry
    assert "7 registros seriam apagados" in dry
    assert "anteriores a 2025-10-26" in dry
    with env.client(retention_dry_run=False) as c:
        login(c)
        live = c.get(URL, headers=HX).text
    assert "Ativa: apaga o que passa de 12 meses." in live
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'retention.purge'") == 1


def test_parse_last_run_tolera_json_invalido_e_ignora_chaves_desconhecidas() -> None:
    at = datetime(2026, 10, 26, tzinfo=UTC)
    bad = parse_last_run(at, "{nao e json")
    assert bad.rows == () and bad.total == 0 and bad.dry_run is False
    ok = parse_last_run(at, '{"dry_run": false, "counts": {"tickets": 2, "x": 9}}')
    assert ok.rows == (("Tickets", 2),) and ok.total == 2
