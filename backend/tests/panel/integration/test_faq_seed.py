"""Carga inicial das 112 perguntas: `faq seed` e `faq_seed` (PN-T31, PN-30)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app import cli
from app.core.clock import FakeClock
from app.panel.services import faq_validation as fv
from tests.integration.test_cli_users import SETTINGS

pytestmark = pytest.mark.db

CLOCK = FakeClock(datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
GUIA = Path(__file__).resolve().parents[4] / "piloto" / "guia.json"
ACTOR = "importacao-inicial"


async def _go(pool: asyncpg.Pool, argv: list[str]) -> int:
    return await cli.run(argv, pool=pool, settings=SETTINGS, clock=CLOCK, wait_seconds=0)


async def _count(pool: asyncpg.Pool, table: str) -> int:
    return int(await pool.fetchval(f"SELECT count(*) FROM {table}"))  # noqa: S608 - constante


def _write_guia(tmp_path: Path, itens: list[dict[str, str]]) -> Path:
    path = tmp_path / "guia.json"
    path.write_text(json.dumps({"fonte": "teste", "itens": itens}), encoding="utf-8")
    return path


async def test_carga_das_112_e_segunda_rodada_insere_zero(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _go(db_pool, ["faq", "seed", "--file", str(GUIA), "--by", ACTOR]) == 0
    assert "total=112 inseridas=112" in capsys.readouterr().out
    assert await _count(db_pool, "content.faq_items") == 112
    assert await _count(db_pool, "content.faq_item_versions") == 112
    assert await _go(db_pool, ["faq", "seed", "--file", str(GUIA), "--by", ACTOR]) == 0
    assert "inseridas=0 ja existiam=112" in capsys.readouterr().out
    assert await _count(db_pool, "content.faq_items") == 112
    assert await _count(db_pool, "content.faq_item_versions") == 112


async def test_campos_da_carga(db_pool: asyncpg.Pool) -> None:
    await _go(db_pool, ["faq", "seed", "--file", str(GUIA), "--by", ACTOR])
    rows = await db_pool.fetch(
        "SELECT brand, category, origin, active, created_by, bot_responde, answer "
        "FROM content.faq_items"
    )
    assert {r["origin"] for r in rows} == {"seed"}
    assert all(r["active"] and r["created_by"] == ACTOR for r in rows)
    assert {r["category"] for r in rows} <= set(fv.FAQ_CATEGORIES)
    brands = {r["brand"] for r in rows}
    assert brands == {"jungle", "plant_power", "possible", "todas"}
    assert sum(1 for r in rows if r["brand"] == "todas") == 4
    assert sum(1 for r in rows if r["category"] == "ARGUMENTO") == 9
    # a unica resposta com numero de notificacao entra sem resposta e sem bot
    sem_bot = [r for r in rows if not r["bot_responde"]]
    assert len(sem_bot) == 1 and sem_bot[0]["answer"] is None
    versions = await db_pool.fetch("SELECT DISTINCT change_kind FROM content.faq_item_versions")
    assert [v["change_kind"] for v in versions] == ["seed"]


async def test_auditoria_cli_da_carga(db_pool: asyncpg.Pool) -> None:
    await _go(db_pool, ["faq", "seed", "--file", str(GUIA), "--by", ACTOR])
    row = await db_pool.fetchrow(
        "SELECT actor_type, actor, new_value FROM ops.audit_log WHERE action = 'faq.seed'"
    )
    assert row is not None and row["actor_type"] == "cli" and row["actor"] == ACTOR
    assert json.loads(row["new_value"])["inserted"] == 112
    assert await _count(db_pool, "ops.audit_log") == 113
    await _go(db_pool, ["faq", "seed", "--file", str(GUIA), "--by", ACTOR])
    assert await _count(db_pool, "ops.audit_log") == 113  # segunda rodada nao audita


async def test_resposta_vazia_entra_sem_bot_e_desativado_nao_volta(
    db_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    guia = _write_guia(
        tmp_path,
        [
            {"marca": "Possible", "q": "Tema de saúde?", "a": ""},
            {"marca": "Jungle", "q": "Tem sabor?", "a": "Tem."},
        ],
    )
    assert await _go(db_pool, ["faq", "seed", "--file", str(guia)]) == 0
    row = await db_pool.fetchrow(
        "SELECT bot_responde, answer FROM content.faq_items WHERE brand = 'possible'"
    )
    assert row is not None and row["bot_responde"] is False and row["answer"] is None
    await db_pool.execute("UPDATE content.faq_items SET active = false WHERE brand = 'jungle'")
    assert await _go(db_pool, ["faq", "seed", "--file", str(guia)]) == 0
    assert await _count(db_pool, "content.faq_items") == 2


async def test_arquivo_invalido_sai_com_2(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ruim = tmp_path / "ruim.json"
    ruim.write_text("não é json", encoding="utf-8")
    assert await _go(db_pool, ["faq", "seed", "--file", str(ruim)]) == 2
    assert await _go(db_pool, ["faq", "seed", "--file", str(tmp_path / "nao-existe.json")]) == 2
    marca = _write_guia(tmp_path, [{"marca": "Outra", "q": "x?", "a": "y"}])
    assert await _go(db_pool, ["faq", "seed", "--file", str(marca)]) == 2
    assert "marca desconhecida" in capsys.readouterr().err
    assert await _count(db_pool, "content.faq_items") == 0


async def test_item_recusado_desfaz_tudo(db_pool: asyncpg.Pool, tmp_path: Path) -> None:
    longa = "p" * (fv.QUESTION_MAX_LENGTH + 1)
    guia = _write_guia(
        tmp_path,
        [
            {"marca": "Jungle", "q": "Boa pergunta?", "a": "Boa."},
            {"marca": "Jungle", "q": longa, "a": "Boa."},
        ],
    )
    assert await _go(db_pool, ["faq", "seed", "--file", str(guia)]) == 1
    assert await _count(db_pool, "content.faq_items") == 0
    assert await _count(db_pool, "ops.audit_log") == 0
