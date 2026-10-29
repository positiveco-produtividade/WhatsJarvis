"""ReplyPolicy (R4): pausa x opt-in, marca desligada, escrita de flag vale na hora."""

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.values import ReplyKind
from app.services.flags import Flags, brand_key
from app.services.reply_policy import AllowAllPolicy, FlagsReplyPolicy

pytestmark = pytest.mark.db

PAUSED_KINDS = [
    ReplyKind.UNDER_CONSTRUCTION,
    ReplyKind.MEDIA_ACK,
    ReplyKind.RATE_LIMIT_NOTICE,
    ReplyKind.OPT_OUT_CONFIRMATION,
]
EXEMPT_KINDS = [
    ReplyKind.WELCOME_TEMPLATE,
    ReplyKind.OPTIN_REMINDER,
    ReplyKind.OPTIN_CONFIRMATION,
    ReplyKind.OPTIN_DECLINED,
]


async def _set(flags: Flags, pool: asyncpg.Pool, key: str, value: bool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, key, value, "caio")


async def test_sem_flags_permite_tudo(db_pool: asyncpg.Pool) -> None:
    policy = FlagsReplyPolicy(Flags(db_pool, FakeClock()))
    for kind in ReplyKind:
        decision = await policy.allow(None, None, kind.value, "jungle")
        assert decision.ok is True


@pytest.mark.parametrize("kind", PAUSED_KINDS)
async def test_pausa_nega_respostas_conversacionais(
    db_pool: asyncpg.Pool, kind: ReplyKind
) -> None:
    flags = Flags(db_pool, FakeClock())
    policy = FlagsReplyPolicy(flags)
    await _set(flags, db_pool, "paused", True)
    decision = await policy.allow(None, None, kind.value)
    assert (decision.ok, decision.reason) == (False, "paused")


@pytest.mark.parametrize("kind", EXEMPT_KINDS)
async def test_pausa_nao_silencia_consentimento(db_pool: asyncpg.Pool, kind: ReplyKind) -> None:
    flags = Flags(db_pool, FakeClock())
    policy = FlagsReplyPolicy(flags)
    await _set(flags, db_pool, "paused", True)
    assert (await policy.allow(None, None, kind.value)).ok is True


async def test_marca_desligada_nega_so_com_marca(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    policy = FlagsReplyPolicy(flags)
    await _set(flags, db_pool, brand_key("zaya"), False)

    denied = await policy.allow(None, None, ReplyKind.UNDER_CONSTRUCTION.value, "zaya")
    assert (denied.ok, denied.reason) == (False, "brand_disabled")
    assert (await policy.allow(None, None, ReplyKind.UNDER_CONSTRUCTION.value, "jungle")).ok
    assert (await policy.allow(None, None, ReplyKind.UNDER_CONSTRUCTION.value)).ok
    assert await policy.brand_allows("zaya") is False
    assert await policy.brand_allows("jungle") is True


async def test_pausa_tem_precedencia_sobre_marca(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    policy = FlagsReplyPolicy(flags)
    await _set(flags, db_pool, "paused", True)
    await _set(flags, db_pool, brand_key("zaya"), False)
    decision = await policy.allow(None, None, ReplyKind.MEDIA_ACK.value, "zaya")
    assert decision.reason == "paused"


async def test_escrita_de_flag_vale_no_mesmo_processo(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock())
    policy = FlagsReplyPolicy(flags)
    kind = ReplyKind.RATE_LIMIT_NOTICE.value
    assert (await policy.allow(None, None, kind)).ok is True  # popula o cache
    await _set(flags, db_pool, "paused", True)
    assert (await policy.allow(None, None, kind)).ok is False
    await _set(flags, db_pool, "paused", False)
    assert (await policy.allow(None, None, kind)).ok is True


async def test_allow_all_permite(db_pool: asyncpg.Pool) -> None:
    decision = await AllowAllPolicy().allow(None, None, ReplyKind.MEDIA_ACK.value, "zaya")
    assert decision.ok is True
