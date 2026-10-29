"""Flags (R4): padroes, escrita audita e invalida cache, TTL 30 s, chaves, atomicidade."""

import json

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidFlagKey, InvalidValue
from app.core.values import BRANDS
from app.services import audit
from app.services.flags import Flags

pytestmark = pytest.mark.db


async def _set(
    flags: Flags, pool: asyncpg.Pool, key: str, value: bool, actor: str = "caio"
) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, key, value, actor)


async def test_padroes(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    assert await flags.team_group_enabled() is False
    assert await flags.paused() is False
    for brand in BRANDS:
        assert await flags.brand_enabled(brand) is True


async def test_team_group_combina_settings_e_system_state(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock(), team_group_initial=True)
    assert await flags.team_group_enabled() is True
    await _set(flags, db_pool, "team_group_enabled", False)
    assert await flags.team_group_enabled() is False


async def test_set_bool_grava_audita_e_invalida_cache(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    assert await flags.get_bool("paused", False) is False  # popula cache
    await _set(flags, db_pool, "paused", True, actor="caio")
    assert await flags.get_bool("paused", False) is True  # sem avancar o relogio

    async with db_pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT value::text AS v, updated_by FROM ops.system_state WHERE key = 'paused'"
        )
        log = await conn.fetchrow(
            "SELECT actor_type, actor, action, target_type, target_id, "
            "old_value::text AS o, new_value::text AS n FROM ops.audit_log"
        )
    assert state is not None and json.loads(state["v"]) is True and state["updated_by"] == "caio"
    assert log is not None
    assert (log["actor"], log["target_type"], log["target_id"]) == ("caio", "flag", "paused")
    assert json.loads(log["o"]) is False and json.loads(log["n"]) is True


async def test_brand_enabled_false_e_audit_old_true(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    await _set(flags, db_pool, "brand_enabled:zaya", False)
    assert await flags.brand_enabled("zaya") is False
    assert await flags.brand_enabled("jungle") is True
    async with db_pool.acquire() as conn:
        old = await conn.fetchval("SELECT old_value::text FROM ops.audit_log")
    assert json.loads(old) is True


async def test_ttl_de_30s_com_relogio_falso(db_pool: asyncpg.Pool) -> None:
    clock = FakeClock()
    flags = Flags(db_pool, clock)
    assert await flags.get_bool("paused", False) is False
    # outro processo altera direto no banco: este cache ainda nao ve
    await db_pool.execute(
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
        "VALUES ('paused', 'true'::jsonb, now(), 'outro')"
    )
    clock.advance(seconds=29)
    assert await flags.get_bool("paused", False) is False
    clock.advance(seconds=1)
    assert await flags.get_bool("paused", False) is True


async def test_cache_evita_nova_consulta(db_pool: asyncpg.Pool) -> None:
    class CountingPool:
        calls = 0

        async def fetchval(self, *args: object) -> object:
            CountingPool.calls += 1
            return await db_pool.fetchval(*args)  # type: ignore[arg-type]

    flags = Flags(CountingPool(), FakeClock())
    for _ in range(3):
        await flags.get_bool("paused", False)
    assert CountingPool.calls == 1


async def test_leitura_com_conn_nao_toca_o_pool_e_respeita_cache(db_pool: asyncpg.Pool) -> None:
    class ForbiddenPool:
        async def fetchval(self, *args: object) -> object:
            raise AssertionError("o pool nao deve ser usado quando conn e dado")

    class CountingConn:
        calls = 0

        async def fetchval(self, *args: object) -> object:
            CountingConn.calls += 1
            return await db_pool.fetchval(*args)  # type: ignore[arg-type]

    flags = Flags(ForbiddenPool(), FakeClock(), team_group_initial=True)
    conn = CountingConn()
    for _ in range(3):
        assert await flags.team_group_enabled(conn=conn) is True
        assert await flags.paused(conn=conn) is False
        assert await flags.brand_enabled("jungle", conn=conn) is True
    assert CountingConn.calls == 3  # uma leitura por chave; o resto vem do cache


async def test_leitura_com_conn_ve_valor_gravado_e_expira_pelo_ttl(db_pool: asyncpg.Pool) -> None:
    clock = FakeClock()
    flags = Flags(db_pool, clock)
    async with db_pool.acquire() as conn:
        assert await flags.paused(conn=conn) is False
        await conn.execute(
            "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
            "VALUES ('paused', 'true'::jsonb, now(), 'x')"
        )
        assert await flags.paused(conn=conn) is False  # ainda no cache
        clock.advance(seconds=31)
        assert await flags.paused(conn=conn) is True


@pytest.mark.parametrize("key", ["foo", "brand_enabled:outra", "brand_enabled:", "PAUSED", ""])
async def test_chave_invalida_recusada_sem_gravar(db_pool: asyncpg.Pool, key: str) -> None:
    flags = Flags(db_pool, FakeClock())
    async with db_pool.acquire() as conn:
        with pytest.raises(InvalidFlagKey):
            await flags.set_bool(conn, key, True, "caio")
        assert await conn.fetchval("SELECT count(*) FROM ops.system_state") == 0
        assert await conn.fetchval("SELECT count(*) FROM ops.audit_log") == 0


async def test_acao_aborta_se_auditoria_falhar(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(*args: object, **kwargs: object) -> int:
        raise RuntimeError("auditoria indisponivel")

    monkeypatch.setattr(audit, "write", boom)
    flags = Flags(db_pool, FakeClock())
    with pytest.raises(RuntimeError):
        await _set(flags, db_pool, "paused", True)
    assert await db_pool.fetchval("SELECT count(*) FROM ops.system_state") == 0
    assert await flags.get_bool("paused", False) is False


async def test_actor_type_panel_chega_ao_audit_log(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    async with db_pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, "paused", True, "painel-user", actor_type="panel")
    assert await db_pool.fetchval("SELECT actor_type FROM ops.audit_log") == "panel"


async def test_actor_type_invalido_aborta_set_bool(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    with pytest.raises(InvalidValue):
        async with db_pool.acquire() as conn, conn.transaction():
            await flags.set_bool(conn, "paused", True, "x", actor_type="robot")
    assert await db_pool.fetchval("SELECT count(*) FROM ops.system_state") == 0


async def test_unknown_first_text_display_aceita_com_padrao_false(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    assert await flags.get_bool("unknown_first_text_display", False) is False
    async with db_pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, "unknown_first_text_display", True, "p@x.test", "panel")
    assert await flags.get_bool("unknown_first_text_display", False) is True
    row = await db_pool.fetchrow(
        "SELECT actor_type, old_value::text AS o, new_value::text AS n FROM ops.audit_log"
    )
    assert row is not None
    assert (row["actor_type"], row["o"], row["n"]) == ("panel", "false", "true")
