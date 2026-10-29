"""Importacao de pessoas: lote, savepoint por linha e apagamento da PII (PN-T37, PN-40)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.panel.services import import_batches as ib
from app.panel.services import people_import as pi
from app.services import users

pytestmark = pytest.mark.db

ACTOR = "caio@example.test"
HEADER = "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor"
LINES = [
    "Ana Souza,+5585991231201,vendedor,Loja X,CE|PE,atacarejo;farma,jungle,Bia Lima",
    "Beto Dias,+5511987654321,vendedor,Loja Y,SP,atacarejo,jungle,Bia Lima",
    "Cida,123,vendedor,Loja Z,SP,atacarejo,jungle,Bia Lima",
    "Ana Repetida,+558591231201,vendedor,Loja X,CE,atacarejo,jungle,Bia Lima",
]


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr("postgresql://x"),
        whatsapp_app_secret=SecretStr("s"),
        whatsapp_verify_token=SecretStr("v"),
        whatsapp_access_token=SecretStr("t"),
        whatsapp_phone_number_id="1",
        welcome_template_name="boas_vindas_jarvis",
    )


def _csv(tmp_path: Path, lines: list[str]) -> Path:
    path = tmp_path / "pessoas.csv"
    path.write_text("\n".join([HEADER, *lines]) + "\n", encoding="utf-8")
    return path


async def _preview(
    conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path, lines: list[str] | None = None
) -> ib.BatchRecord:
    path = _csv(tmp_path, LINES if lines is None else lines)
    up = await pi.load_upload(path.read_bytes(), path.name)
    return await pi.create_preview(conn, up, filename=str(path), actor=ACTOR, clock=clock)


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))  # noqa: S608 - constante


async def _register(conn: asyncpg.Connection, clock: FakeClock, phone: str) -> None:
    row = {
        "nome": "Ja Existe",
        "telefone": phone,
        "perfil": "vendedor",
        "empresa": "E",
        "ufs": "CE",
        "canais": "atacarejo",
        "marcas": "jungle",
        "gestor": "G",
    }
    data = users.validate_row(row, now=clock.now())
    assert isinstance(data, users.ValidatedUser)
    await users.register(conn, data, actor="x", enqueue_welcome=False, clock=clock)


async def _commit(conn: asyncpg.Connection, clock: FakeClock, batch_id: Any) -> Any:
    return await pi.commit_import(
        conn, batch_id, actor=ACTOR, settings=make_settings(), clock=clock
    )


async def test_preview_classifies_and_registers_nothing(
    db_conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    await _register(db_conn, clock, "+5511987654321")
    before = await _count(db_conn, "access.users")
    batch = await _preview(db_conn, clock, tmp_path)
    assert batch.kind == "people" and batch.status == ib.PREVIEW
    assert batch.filename == "pessoas.csv"
    assert [r["situacao"] for r in batch.rows] == [
        pi.ENTRA,
        pi.FICA_DE_FORA,
        pi.ERRO,
        pi.FICA_DE_FORA,
    ]
    assert batch.summary["entra"] == 1 and batch.summary["total"] == 4
    assert "Ana" not in str(batch.summary)
    assert await _count(db_conn, "access.users") == before


async def test_commit_saves_only_valid_reports_rest_and_wipes_rows(
    db_conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    batch = await _preview(db_conn, clock, tmp_path)
    res = await _commit(db_conn, clock, batch.id)
    assert res is not None
    assert (res.imported, res.skipped, res.invalid) == (2, 1, 1)
    assert [(r.linha, r.situacao) for r in res.not_imported] == [(4, pi.ERRO), (5, pi.FICA_DE_FORA)]
    rows = await db_conn.fetch("SELECT name, status FROM access.users ORDER BY id")
    assert [r["name"] for r in rows] == ["Ana Souza", "Beto Dias"]
    assert {r["status"] for r in rows} == {"pending_optin"}
    assert await _count(db_conn, "chat.outbound_messages") == 2
    after = await ib.get_batch(db_conn, batch.id, clock=clock)
    assert after is not None and after.status == ib.COMMITTED and after.rows == []
    audit = await db_conn.fetchval(
        "SELECT new_value::text FROM ops.audit_log WHERE action = 'people.import'"
    )
    assert "Ana" not in audit and "1201" not in audit


async def test_second_commit_does_not_duplicate(
    db_conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    batch = await _preview(db_conn, clock, tmp_path)
    assert await _commit(db_conn, clock, batch.id) is not None
    assert await _commit(db_conn, clock, batch.id) is None
    assert await _count(db_conn, "access.users") == 2


async def test_duplicate_registered_after_preview_does_not_abort_batch(
    db_conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    batch = await _preview(db_conn, clock, tmp_path, LINES[:2])
    await _register(db_conn, clock, "+5585991231201")  # entrou depois da conferencia
    res = await _commit(db_conn, clock, batch.id)
    assert res is not None and (res.imported, res.skipped) == (1, 1)
    names = [r["name"] for r in await db_conn.fetch("SELECT name FROM access.users ORDER BY id")]
    assert names == ["Ja Existe", "Beto Dias"]


async def test_cancel_and_expiry_wipe_rows(
    db_conn: asyncpg.Connection, clock: FakeClock, tmp_path: Path
) -> None:
    b1 = await _preview(db_conn, clock, tmp_path)
    assert await ib.cancel_batch(db_conn, b1.id, clock=clock)
    b2 = await _preview(db_conn, clock, tmp_path)
    clock.advance(hours=2)
    assert await _commit(db_conn, clock, b2.id) is None
    for b in (b1, b2):
        got = await ib.get_batch(db_conn, b.id, clock=clock)
        assert got is not None and got.rows == []
    assert await _count(db_conn, "access.users") == 0
