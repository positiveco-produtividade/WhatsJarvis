"""Escrita da FAQ: validacao, versoes, concorrencia otimista, desativar, auditoria (PN-T25)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import faq_admin
from app.panel.services import faq_validation as fv
from app.panel.services.faq_admin import FaqInput

pytestmark = pytest.mark.db

ACTOR = "caio@example.test"
_KINDS_SQL = "SELECT change_kind FROM content.faq_item_versions ORDER BY version"
_REV_SQL = "SELECT value::text FROM ops.system_state WHERE key = 'faq_revision'"


@pytest.fixture
async def conn(db_conn: asyncpg.Connection) -> asyncpg.Connection:
    return db_conn


def _data(**over: Any) -> FaqInput:
    base: dict[str, Any] = {
        "brand": "jungle",
        "category": "PRODUTO",
        "question": "Qual o sabor da barra?",
        "answer": "Chocolate.",
        "variations": ("sabor da barra",),
        "bot_responde": True,
    }
    base.update(over)
    return FaqInput(**base)


async def _create(conn: asyncpg.Connection, clock: FakeClock, **over: Any) -> faq_admin.FaqRecord:
    res = await faq_admin.create_item(conn, _data(**over), actor=ACTOR, clock=clock)
    assert res.status == faq_admin.OK and res.item is not None
    return res.item


async def _count(conn: asyncpg.Connection, table: str) -> int:
    return int(await conn.fetchval(f"SELECT count(*) FROM {table}"))  # noqa: S608 - constante


async def _audits(conn: asyncpg.Connection, action: str | None = None) -> list[asyncpg.Record]:
    return await conn.fetch(
        "SELECT * FROM ops.audit_log WHERE target_type = 'faq_item' "
        "AND ($1::text IS NULL OR action = $1) ORDER BY id",
        action,
    )


async def test_criar_grava_item_versao_1_e_auditoria(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock, variations=("a ", "", "  ", "b"))
    assert item.version == 1 and item.active and item.origin == "manual"
    assert item.created_by == ACTOR and item.created_at == clock.now()
    assert item.variations == ("a", "b")
    versions = await conn.fetch("SELECT * FROM content.faq_item_versions")
    assert [(v["item_id"], v["version"], v["change_kind"]) for v in versions] == [
        (item.id, 1, "create")
    ]
    assert json.loads(versions[0]["snapshot"])["question"] == item.question
    audits = await _audits(conn)
    assert [(a["action"], a["actor_type"], a["actor"], a["target_id"]) for a in audits] == [
        ("faq.create", "panel", ACTOR, str(item.id))
    ]
    assert audits[0]["old_value"] is None


async def test_invalido_lista_todos_os_problemas_e_nao_grava(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    res = await faq_admin.create_item(
        conn,
        _data(question="  ", answer=None, variations=("ligue 85 99999-1201",)),
        actor=ACTOR,
        clock=clock,
    )
    assert res.status == faq_admin.INVALID
    codes = {p.code for p in res.problems}
    assert {fv.QUESTION_EMPTY, fv.ANSWER_REQUIRED, fv.CONTACT_PHONE} <= codes
    assert await _count(conn, "content.faq_items") == 0
    assert await _count(conn, "ops.audit_log") == 0


async def test_duplicata_sem_acento_e_caixa_na_marca(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    await _create(conn, clock, question="Qual é o sabor?")
    dup = await faq_admin.create_item(
        conn, _data(question="  qual E o  SABOR? "), actor=ACTOR, clock=clock
    )
    assert dup.status == faq_admin.INVALID and fv.DUPLICATE_QUESTION in {
        p.code for p in dup.problems
    }
    other_brand = await faq_admin.create_item(
        conn, _data(brand="zaya", question="Qual é o sabor?"), actor=ACTOR, clock=clock
    )
    assert other_brand.status == faq_admin.OK


async def test_editar_incrementa_versao_e_guarda_historico(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    clock.advance(seconds=60)
    res = await faq_admin.update_item(
        conn, item.id, _data(answer="Baunilha."), expected_version=1, actor="outro", clock=clock
    )
    assert res.status == faq_admin.OK and res.item is not None
    assert res.item.version == 2 and res.item.answer == "Baunilha."
    assert res.item.updated_by == "outro" and res.item.updated_at == clock.now()
    assert res.item.created_by == ACTOR
    rows = await conn.fetch(
        "SELECT version, change_kind FROM content.faq_item_versions ORDER BY version"
    )
    assert [(r["version"], r["change_kind"]) for r in rows] == [(1, "create"), (2, "update")]
    (_, upd) = await _audits(conn)
    assert upd["action"] == "faq.update"
    assert json.loads(upd["old_value"])["answer"] == "Chocolate."
    assert json.loads(upd["new_value"])["answer"] == "Baunilha."


async def test_editar_invalido_nao_muda_nada(conn: asyncpg.Connection, clock: FakeClock) -> None:
    item = await _create(conn, clock)
    res = await faq_admin.update_item(
        conn, item.id, _data(answer=None), expected_version=1, actor=ACTOR, clock=clock
    )
    assert res.status == faq_admin.INVALID
    assert (await faq_admin.get_item(conn, item.id)) == item
    assert await _count(conn, "content.faq_item_versions") == 1


async def test_editar_proprio_texto_nao_conta_como_duplicata(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    res = await faq_admin.update_item(
        conn, item.id, _data(category="PRECO"), expected_version=1, actor=ACTOR, clock=clock
    )
    assert res.status == faq_admin.OK


async def test_editar_item_inexistente(conn: asyncpg.Connection, clock: FakeClock) -> None:
    res = await faq_admin.update_item(
        conn, 999, _data(), expected_version=1, actor=ACTOR, clock=clock
    )
    assert res.status == faq_admin.NOT_FOUND


async def test_edicao_concorrente_avisa_e_nao_sobrescreve(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    first = await faq_admin.update_item(
        conn, item.id, _data(answer="A"), expected_version=1, actor="admin1", clock=clock
    )
    assert first.ok
    second = await faq_admin.update_item(
        conn, item.id, _data(answer="B"), expected_version=1, actor="admin2", clock=clock
    )
    assert second.status == faq_admin.CONFLICT
    assert second.item is not None and second.item.answer == "A" and second.item.version == 2
    current = await faq_admin.get_item(conn, item.id)
    assert current is not None and current.answer == "A"
    assert await _count(conn, "content.faq_item_versions") == 2
    assert len(await _audits(conn, "faq.update")) == 1


async def test_force_sobrescreve_e_registra_versao(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    await faq_admin.update_item(
        conn, item.id, _data(answer="A"), expected_version=1, actor="admin1", clock=clock
    )
    forced = await faq_admin.update_item(
        conn,
        item.id,
        _data(answer="B"),
        expected_version=1,
        actor="admin2",
        force=True,
        clock=clock,
    )
    assert forced.ok and forced.item is not None
    assert forced.item.answer == "B" and forced.item.version == 3
    assert await _count(conn, "content.faq_item_versions") == 3


async def test_duas_edicoes_simultaneas_so_uma_vence(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    async with db_pool.acquire() as c0:
        item = await _create(c0, clock)

    async def edit(answer: str) -> faq_admin.FaqWriteResult:
        async with db_pool.acquire() as c:
            return await faq_admin.update_item(
                c, item.id, _data(answer=answer), expected_version=1, actor=answer, clock=clock
            )

    results = await asyncio.gather(edit("A"), edit("B"))
    assert sorted(r.status for r in results) == [faq_admin.CONFLICT, faq_admin.OK]
    async with db_pool.acquire() as c:
        current = await faq_admin.get_item(c, item.id)
        assert current is not None and current.version == 2
        assert await _count(c, "content.faq_item_versions") == 2


async def test_desativar_nao_apaga_e_reativar_volta(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    clock.advance(seconds=30)
    off = await faq_admin.deactivate_item(conn, item.id, actor="adm", clock=clock)
    assert off.ok and off.item is not None
    assert not off.item.active and off.item.deactivated_by == "adm"
    assert off.item.deactivated_at == clock.now() and off.item.version == 2
    assert await _count(conn, "content.faq_items") == 1
    active_rows = await conn.fetchval("SELECT count(*) FROM content.faq_items WHERE active")
    assert active_rows == 0

    on = await faq_admin.reactivate_item(conn, item.id, actor="adm2", clock=clock)
    assert on.ok and on.item is not None
    assert on.item.active and on.item.deactivated_at is None and on.item.version == 3
    kinds = [
        r["change_kind"]
        for r in await conn.fetch(
            "SELECT change_kind FROM content.faq_item_versions ORDER BY version"
        )
    ]
    assert kinds == ["create", "deactivate", "reactivate"]
    actions = [a["action"] for a in await _audits(conn)]
    assert actions == ["faq.create", "faq.deactivate", "faq.reactivate"]


async def test_desativar_duas_vezes_e_noop_sem_efeitos(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    item = await _create(conn, clock)
    await faq_admin.deactivate_item(conn, item.id, actor=ACTOR, clock=clock)
    again = await faq_admin.deactivate_item(conn, item.id, actor=ACTOR, clock=clock)
    assert again.status == faq_admin.NOOP
    assert await _count(conn, "content.faq_item_versions") == 2
    assert len(await _audits(conn, "faq.deactivate")) == 1
    assert (await faq_admin.deactivate_item(conn, 999, actor=ACTOR)).status == faq_admin.NOT_FOUND


async def test_reativar_com_duplicata_ativa_e_recusado(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    old = await _create(conn, clock)
    await faq_admin.deactivate_item(conn, old.id, actor=ACTOR, clock=clock)
    await _create(conn, clock)  # mesma pergunta, agora ativa
    res = await faq_admin.reactivate_item(conn, old.id, actor=ACTOR, clock=clock)
    assert res.status == faq_admin.INVALID
    assert fv.DUPLICATE_QUESTION in {p.code for p in res.problems}
    current = await faq_admin.get_item(conn, old.id)
    assert current is not None and not current.active


async def test_edicao_de_item_desativado_nao_checa_duplicata(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    old = await _create(conn, clock)
    await faq_admin.deactivate_item(conn, old.id, actor=ACTOR, clock=clock)
    await _create(conn, clock)
    res = await faq_admin.update_item(
        conn, old.id, _data(answer="Outra."), expected_version=2, actor=ACTOR, clock=clock
    )
    assert res.ok


async def test_revisao_da_faq_sobe_a_cada_escrita(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    async def revision() -> int:
        raw = await conn.fetchval(
            "SELECT value::text FROM ops.system_state WHERE key = 'faq_revision'"
        )
        return int(json.loads(raw)) if raw is not None else 0

    item = await _create(conn, clock)
    r1 = await revision()
    await faq_admin.deactivate_item(conn, item.id, actor=ACTOR, clock=clock)
    assert await revision() > r1


async def test_falha_da_auditoria_desfaz_a_acao(
    conn: asyncpg.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = await _create(conn, clock)

    async def boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("auditoria fora")

    monkeypatch.setattr(faq_admin.audit, "write", boom)
    with pytest.raises(RuntimeError):
        await faq_admin.update_item(
            conn, item.id, _data(answer="X"), expected_version=1, actor=ACTOR, clock=clock
        )
    with pytest.raises(RuntimeError):
        await faq_admin.deactivate_item(conn, item.id, actor=ACTOR, clock=clock)
    with pytest.raises(RuntimeError):
        await faq_admin.create_item(conn, _data(question="Outra?"), actor=ACTOR, clock=clock)
    current = await faq_admin.get_item(conn, item.id)
    assert current == item
    assert await _count(conn, "content.faq_items") == 1
    assert await _count(conn, "content.faq_item_versions") == 1


async def test_origem_e_tipo_de_versao_da_semente(
    conn: asyncpg.Connection, clock: FakeClock
) -> None:
    res = await faq_admin.create_item(
        conn, _data(), actor="importacao-inicial", clock=clock, origin="seed", change_kind="seed"
    )
    assert res.item is not None and res.item.origin == "seed"
    kind = await conn.fetchval("SELECT change_kind FROM content.faq_item_versions")
    assert kind == "seed"
    with pytest.raises(ValueError):
        await faq_admin.create_item(conn, _data(), actor=ACTOR, origin="xyz")


async def test_marca_ou_categoria_invalida(conn: asyncpg.Connection, clock: FakeClock) -> None:
    res = await faq_admin.create_item(
        conn, _data(brand="x", category="MIDIA"), actor=ACTOR, clock=clock
    )
    assert res.status == faq_admin.INVALID
    assert {fv.BAD_BRAND, fv.BAD_CATEGORY} <= {p.code for p in res.problems}
