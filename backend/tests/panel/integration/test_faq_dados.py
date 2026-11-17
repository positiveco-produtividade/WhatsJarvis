"""Carga das perguntas Nielsen e GMR na FAQ: `faq dados` e `faq_dados`."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest

from app import cli
from app.core.clock import FakeClock
from tests.integration.test_cli_users import SETTINGS

pytestmark = pytest.mark.db

CLOCK = FakeClock(datetime(2026, 11, 16, 12, 0, tzinfo=UTC))
ACTOR = "carga-dados"

NIELSEN = {
    "linhas": [
        {
            "cat": "bebidas vegetais",
            "marca": "A Tal da Castanha",
            "lugar": "Brasil",
            "periodo": "set 26",
            "periodoAnt": "set 25",
            "sh": 37.8,
            "shAnt": 41.2,
            "pos": 1,
            "lojas": 7961,
        },
        {
            "cat": "isotônicos",
            "marca": "Jungle",
            "lugar": "Brasil",
            "periodo": "set 26",
            "periodoAnt": "set 25",
            "sh": 3.2,
            "shAnt": 2.7,
            "pos": 3,
            "lojas": 6508,
        },
        {  # estado: fica de fora (so recorte nacional)
            "cat": "isotônicos",
            "marca": "Jungle",
            "lugar": "CE",
            "periodo": "set 26",
            "periodoAnt": "set 25",
            "sh": 45.7,
            "shAnt": 39.0,
            "pos": 1,
            "lojas": 339,
        },
    ]
}
GMR = {
    "periodo": "2026-07 a 2026-09",
    "brasil": [{"marca": "Possible", "top": ["A", "B", "C"]}],
}


def _dir(tmp_path: Path, nielsen: object = NIELSEN, gmr: object = GMR) -> Path:
    (tmp_path / "nielsen-resumo.json").write_text(json.dumps(nielsen), encoding="utf-8")
    (tmp_path / "gmr-resumo.json").write_text(json.dumps(gmr), encoding="utf-8")
    return tmp_path


async def _go(pool: asyncpg.Pool, argv: list[str]) -> int:
    return await cli.run(argv, pool=pool, settings=SETTINGS, clock=CLOCK, wait_seconds=0)


async def test_carga_e_segunda_rodada_insere_zero(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    folder = str(_dir(tmp_path))
    assert await _go(db_pool, ["faq", "dados", "--dir", folder, "--by", ACTOR]) == 0
    assert (
        "total=4 inseridas=4 ja existiam=0 atualizadas=0 desativadas=0 nielsen=3 gmr=1"
        in capsys.readouterr().out
    )
    assert await _go(db_pool, ["faq", "dados", "--dir", folder, "--by", ACTOR]) == 0
    assert "inseridas=0 ja existiam=4" in capsys.readouterr().out
    assert int(await db_pool.fetchval("SELECT count(*) FROM content.faq_items")) == 4


async def test_campos_selo_e_auditoria(db_pool: asyncpg.Pool, tmp_path: Path) -> None:
    await _go(db_pool, ["faq", "dados", "--dir", str(_dir(tmp_path)), "--by", ACTOR])
    rows = await db_pool.fetch(
        "SELECT brand, category, origin, origin_ref, bot_responde, active, answer "
        "FROM content.faq_items ORDER BY id"
    )
    assert [(r["brand"], r["origin"], r["origin_ref"]) for r in rows] == [
        ("a_tal_da_castanha", "nielsen", "set 26"),
        ("jungle", "nielsen", "set 26"),
        ("jungle", "nielsen", "set 26"),
        ("possible", "gmr", "jul/26 a set/26"),
    ]
    assert all(r["category"] == "ARGUMENTO" and r["bot_responde"] and r["active"] for r in rows)
    assert all("(Fonte: " in r["answer"] for r in rows)
    kinds = await db_pool.fetch("SELECT DISTINCT change_kind FROM content.faq_item_versions")
    assert [k["change_kind"] for k in kinds] == ["import"]
    audit = await db_pool.fetchrow(
        "SELECT actor_type, actor, new_value FROM ops.audit_log WHERE action = 'faq.dados'"
    )
    assert audit is not None and audit["actor_type"] == "cli" and audit["actor"] == ACTOR
    assert json.loads(audit["new_value"])["inserted"] == 4


async def test_pasta_sem_resumo_nao_grava(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _go(db_pool, ["faq", "dados", "--dir", str(tmp_path)]) == cli.EXIT_INVALID
    assert "nenhum resumo" in capsys.readouterr().err
    assert int(await db_pool.fetchval("SELECT count(*) FROM content.faq_items")) == 0


async def test_arquivo_invalido_nao_grava(db_pool: asyncpg.Pool, tmp_path: Path) -> None:
    folder = str(_dir(tmp_path, nielsen={"linhas": [{"marca": "Outra"}]}))
    assert await _go(db_pool, ["faq", "dados", "--dir", folder]) == cli.EXIT_INVALID
    assert int(await db_pool.fetchval("SELECT count(*) FROM content.faq_items")) == 0


async def _active(pool: asyncpg.Pool) -> list[tuple[str, str]]:
    rows = await pool.fetch(
        "SELECT origin, question FROM content.faq_items WHERE active ORDER BY id"
    )
    return [(r["origin"], r["question"]) for r in rows]


async def test_nova_carga_desativa_o_que_saiu_da_base(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    await _go(db_pool, ["faq", "dados", "--dir", str(_dir(first)), "--by", ACTOR])
    capsys.readouterr()
    only_jungle = {"linhas": [NIELSEN["linhas"][1]]}
    folder = str(_dir(second, nielsen=only_jungle))
    assert await _go(db_pool, ["faq", "dados", "--dir", folder, "--by", ACTOR]) == 0
    assert "inseridas=0 ja existiam=3 atualizadas=0 desativadas=1" in capsys.readouterr().out
    active = await _active(db_pool)
    assert ("nielsen", "Quem lidera bebidas vegetais no Brasil?") not in active
    assert len(active) == 3
    inactive = await db_pool.fetchval(
        "SELECT count(*) FROM content.faq_items WHERE NOT active AND deactivated_by = $1", ACTOR
    )
    assert inactive == 1  # desativada, nao apagada


async def test_carga_so_da_gmr_nao_mexe_na_nielsen_nem_no_guia(
    db_pool: asyncpg.Pool, tmp_path: Path
) -> None:
    await _go(db_pool, ["faq", "dados", "--dir", str(_dir(tmp_path)), "--by", ACTOR])
    await db_pool.execute(
        "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
        "bot_responde, origin, created_at, created_by, updated_at, updated_by) VALUES "
        "('todas', 'PRODUTO', 'Pergunta do Guia', 'pergunta do guia', 'Resposta', true, "
        "'seed', now(), 'teste', now(), 'teste')"
    )
    gmr_only = tmp_path / "gmr"
    gmr_only.mkdir()
    other = {"periodo": "2026-10 a 2026-12", "brasil": [{"marca": "Jungle", "top": ["X"]}]}
    (gmr_only / "gmr-resumo.json").write_text(json.dumps(other), encoding="utf-8")
    assert await _go(db_pool, ["faq", "dados", "--dir", str(gmr_only), "--by", ACTOR]) == 0
    origins = [origin for origin, _ in await _active(db_pool)]
    assert origins.count("nielsen") == 3 and origins.count("seed") == 1
    assert origins.count("gmr") == 1  # a do Possible saiu, entrou a do Jungle


async def test_texto_ou_periodo_novo_atualiza_com_nova_versao(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = tmp_path / "a", tmp_path / "b"
    first.mkdir()
    second.mkdir()
    await _go(db_pool, ["faq", "dados", "--dir", str(_dir(first)), "--by", ACTOR])
    capsys.readouterr()
    newer = {"periodo": "2026-10 a 2026-12", "brasil": GMR["brasil"]}
    assert await _go(db_pool, ["faq", "dados", "--dir", str(_dir(second, gmr=newer))]) == 0
    assert "inseridas=0 ja existiam=3 atualizadas=1 desativadas=0" in capsys.readouterr().out
    row = await db_pool.fetchrow(
        "SELECT origin_ref, version, answer FROM content.faq_items WHERE origin = 'gmr'"
    )
    assert row is not None and row["origin_ref"] == "out/26 a dez/26" and row["version"] == 2
    assert "out/26 a dez/26" in row["answer"]
