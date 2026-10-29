"""Aba Auditoria de Logs: leitura, filtros, sem PII e falha de auditoria (PN-55; PN-T59)."""
# ruff: noqa: F811
from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.panel.services.audit_read import (
    PAGE_SIZE,
    AuditFilters,
    audit_page,
    mask_phone_like,
    parse_filters,
    safe_text,
)
from app.services import audit
from tests.panel.integration.test_access import (  # noqa: F401
    NOW,
    Env,
    env,
    login,
    sql,
)

pytestmark = pytest.mark.db

URL = "/dashboard/logs/auditoria"
HX = {"HX-Request": "true"}
CELL = 'font-mono text-xs">bulk.op'
PHONE = "+55 85 99999-1201"


async def _seed(dsn: str, rows: list[tuple[str, str, str, Any, Any]]) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        for i, (action, ttype, tid, old, new) in enumerate(rows):
            clock = FakeClock(NOW - timedelta(minutes=len(rows) - i))
            await audit.write(
                conn, "panel", "admin@example.test", action, ttype, tid, old, new, clock=clock
            )
    finally:
        await conn.close()


def seed(env: Env, rows: list[tuple[str, str, str, Any, Any]]) -> None:
    asyncio.run(_seed(env.dsn, rows))


def test_vazio_mostra_estado_vazio_e_exige_admin(env: Env) -> None:
    with env.client() as c:
        assert c.get(URL).status_code == 302
        login(c)
        r = c.get(URL, headers=HX)
    assert r.status_code == 200
    assert "Nenhum registro de auditoria encontrado." in r.text


def test_lista_registros_do_mais_novo_ao_mais_antigo(env: Env) -> None:
    seed(env, [
        ("faq.update", "faq", "7", {"status": "draft"}, {"status": "active"}),
        ("alert.ack", "alert", "9", {"acknowledged": False}, {"acknowledged": True}),
    ])
    with env.client() as c:
        login(c)
        r = c.get(URL, headers=HX)
    assert r.status_code == 200
    assert r.text.index("alert.ack") < r.text.index("faq.update")
    assert "acknowledged: não" in r.text and "acknowledged: sim" in r.text
    assert "admin@example.test" in r.text


def test_filtra_por_acao_e_por_alvo(env: Env) -> None:
    seed(env, [
        ("faq.update", "faq", "7", None, {"status": "active"}),
        ("alert.ack", "alert", "9", None, {"acknowledged": True}),
        ("alert.ack", "alert", "10", None, {"acknowledged": True}),
    ])
    with env.client() as c:
        login(c)
        by_action = c.get(URL, params={"acao": "faq.update"}, headers=HX).text
        by_target = c.get(URL, params={"alvo": "alert"}, headers=HX).text
        by_id = c.get(URL, params={"alvo": "alert", "alvo_id": "10"}, headers=HX).text
    assert "faq.update" in by_action and "alert.ack" not in by_action.split("<tbody>")[1]
    assert "faq.update" not in by_target.split("<tbody>")[1] and "alert.ack" in by_target
    body = by_id.split("<tbody>")[1]
    assert ">10<" in body and ">9<" not in body


def test_sem_pii_na_tela(env: Env) -> None:
    seed(env, [
        (
            "person.revoke", "person", "1",
            {"status": "active", "phone": PHONE, "name": "Maria Secreta"},
            {"status": "revoked", "note": f"ligou para {PHONE}"},
        ),
        ("x.y", "person", "5585999991201", None, None),
    ])
    with env.client() as c:
        login(c)
        text = c.get(URL, headers=HX).text
    assert "99999-1201" not in text and "5585999991201" not in text
    assert "Maria Secreta" not in text
    assert "[oculto]" in text and "status: revoked" in text


def test_sanitizacao_pura() -> None:
    assert mask_phone_like("tel 5585999991201 ok") == "tel +55 85 9••••-1201 ok"
    assert mask_phone_like("ticket 123456") == "ticket 123456"
    assert safe_text(None) == "—"
    assert safe_text('{"body": "oi", "n": 3}') == "body: [oculto], n: 3"
    assert len(safe_text({"status": "x" * 1000})) <= 240


def test_parse_filters_normaliza() -> None:
    f = parse_filters(action=" a ", target_type=None, target_id="x" * 500, page="abc")
    assert f == AuditFilters(action="a", target_type="", target_id="x" * 80, page=1)
    assert parse_filters(page="-4").page == 1


def test_paginacao_no_servidor(env: Env) -> None:
    rows = [("bulk.op", "bulk", str(i), None, None) for i in range(PAGE_SIZE + 5)]
    seed(env, rows)
    with env.client() as c:
        login(c)
        first = c.get(URL, headers=HX).text
        last = c.get(URL, params={"page": "99"}, headers=HX).text
    assert first.count(CELL) == PAGE_SIZE and "Próxima" in first
    assert last.count(CELL) == 5 and "Anterior" in last


def test_audit_page_dos_registros_do_banco(env: Env) -> None:
    seed(env, [("a.b", "t", "1", None, None)])

    async def run() -> int:
        conn = await asyncpg.connect(env.dsn)
        try:
            return (await audit_page(conn, AuditFilters(action="a.b"))).total
        finally:
            await conn.close()

    assert asyncio.run(run()) == 1


def test_falha_de_auditoria_aborta_a_escrita_e_registro_e_imutavel(env: Env) -> None:
    async def run() -> None:
        conn = await asyncpg.connect(env.dsn)
        try:
            with pytest.raises(InvalidValue):
                async with conn.transaction():
                    await audit.write(
                        conn, "panel", "a", "ok.action", "t", "1", clock=FakeClock(NOW)
                    )
                    await audit.write(conn, "invalido", "a", "bad.action", "t", "2")
            row_id = await audit.write(conn, "panel", "a", "keep", "t", "3", clock=FakeClock(NOW))
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("UPDATE ops.audit_log SET actor = 'x' WHERE id = $1", row_id)
            with pytest.raises(asyncpg.PostgresError):
                await conn.execute("DELETE FROM ops.audit_log WHERE id = $1", row_id)
        finally:
            await conn.close()

    asyncio.run(run())
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'ok.action'") == 0
    assert sql(env.dsn, "SELECT count(*) FROM ops.audit_log WHERE action = 'keep'") == 1
