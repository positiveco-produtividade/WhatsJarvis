"""identity.resolve: telefone com/sem 9o digito, BSUID, conflito sem reassociacao (AC-8, AC-9)."""

from datetime import UTC, datetime

import asyncpg
import pytest

from app.core.values import UserStatus
from app.services import identity
from app.services.access import decide
from app.services.identity import IdentityKind
from app.services.inbound import Sender

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
# Numeros ficticios. Cadastrado com o 9; a Meta entrega o wa_id sem ele.
WITH_NINE = "5511987654321"
WITHOUT_NINE = "551187654321"


async def add_user(
    conn: asyncpg.Connection, phone: str = "+5511987654321", bsuid: str | None = None
) -> int:
    from app.services.phone import normalize_phone

    num = normalize_phone(phone)
    row = await conn.fetchrow(
        "INSERT INTO access.users (name, phone_e164, phone_key, bsuid, profile, company, ufs, "
        "channels, brands, manager_name, status, status_changed_at, created_at, created_by, "
        "updated_at) VALUES ('Ana', $1, $2, $3, 'vendedor', 'Acme', ARRAY['SP'], ARRAY['varejo'], "
        "ARRAY['X'], 'Gestora', 'pending_optin', $4, $4, 'teste', $4) RETURNING id",
        num.phone_e164, num.match_key, bsuid, NOW,
    )
    assert row is not None
    return int(row["id"])


async def stored_bsuid(conn: asyncpg.Connection, user_id: int) -> str | None:
    value = await conn.fetchval("SELECT bsuid FROM access.users WHERE id = $1", user_id)
    return None if value is None else str(value)


async def test_com_9_digito_resolve_usuario(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    res = await identity.resolve(db_conn, Sender(wa_id=WITH_NINE))
    assert res.kind is IdentityKind.USER and res.user_id == uid
    assert res.state.user is not None and res.state.user.status is UserStatus.PENDING_OPTIN


async def test_sem_9_digito_e_o_mesmo_usuario(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    res = await identity.resolve(db_conn, Sender(wa_id=WITHOUT_NINE))
    assert res.kind is IdentityKind.USER and res.user_id == uid


async def test_cadastro_antigo_sem_9_reconhecido_com_9(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn, phone="+55 11 8765-4321")
    res = await identity.resolve(db_conn, Sender(wa_id=WITH_NINE))
    assert res.user_id == uid


async def test_bsuid_e_associado_ao_usuario_sem_bsuid(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn)
    res = await identity.resolve(db_conn, Sender(wa_id=WITHOUT_NINE, bsuid="BS.1"))
    assert res.kind is IdentityKind.USER and res.bsuid_associated
    assert await stored_bsuid(db_conn, uid) == "BS.1"
    again = await identity.resolve(db_conn, Sender(wa_id=WITH_NINE, bsuid="BS.1"))
    assert again.user_id == uid and not again.bsuid_associated


async def test_so_bsuid_de_usuario_conhecido_resolve(db_conn: asyncpg.Connection) -> None:
    uid = await add_user(db_conn, bsuid="BS.9")
    res = await identity.resolve(db_conn, Sender(bsuid="BS.9"))
    assert res.kind is IdentityKind.USER and res.user_id == uid


async def test_so_bsuid_sem_cadastro_e_desconhecido(db_conn: asyncpg.Connection) -> None:
    await add_user(db_conn)
    res = await identity.resolve(db_conn, Sender(bsuid="BS.NOVO"))
    assert res.kind is IdentityKind.UNKNOWN
    assert res.state.user is None and not res.state.conflict
    assert res.bsuid == "BS.NOVO" and res.phone_key is None


async def test_telefone_sem_cadastro_e_desconhecido_com_chave(db_conn: asyncpg.Connection) -> None:
    res = await identity.resolve(db_conn, Sender(wa_id="5521900000077"))
    assert res.kind is IdentityKind.UNKNOWN and res.phone_key == "552100000077"


async def test_remetente_vazio_ou_wa_id_invalido(db_conn: asyncpg.Connection) -> None:
    assert (await identity.resolve(db_conn, Sender())).kind is IdentityKind.UNKNOWN
    assert (await identity.resolve(db_conn, Sender(wa_id="abc"))).kind is IdentityKind.UNKNOWN


async def test_bsuid_de_outro_usuario_e_conflito_sem_reassociar(
    db_conn: asyncpg.Connection,
) -> None:
    ana = await add_user(db_conn, bsuid="BS.ANA")
    bia = await add_user(db_conn, phone="+5521900000002")
    res = await identity.resolve(db_conn, Sender(wa_id="5521900000002", bsuid="BS.ANA"))
    assert res.kind is IdentityKind.CONFLICT and res.state.conflict
    assert set(res.conflict_user_ids) == {ana, bia}
    assert await stored_bsuid(db_conn, ana) == "BS.ANA"
    assert await stored_bsuid(db_conn, bia) is None
    assert decide(res.state, _text(), NOW).value == "identity_conflict"


async def test_bsuid_guardado_diferente_do_recebido_e_conflito(
    db_conn: asyncpg.Connection,
) -> None:
    uid = await add_user(db_conn, bsuid="BS.A")
    res = await identity.resolve(db_conn, Sender(wa_id=WITH_NINE, bsuid="BS.B"))
    assert res.kind is IdentityKind.CONFLICT and res.conflict_user_ids == (uid,)
    assert await stored_bsuid(db_conn, uid) == "BS.A"


async def test_bsuid_de_usuario_com_telefone_diferente_e_conflito(
    db_conn: asyncpg.Connection,
) -> None:
    uid = await add_user(db_conn, bsuid="BS.A")
    res = await identity.resolve(db_conn, Sender(wa_id="5521900000099", bsuid="BS.A"))
    assert res.kind is IdentityKind.CONFLICT and res.conflict_user_ids == (uid,)


async def test_linha_travada_ate_o_fim_da_transacao(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as setup:
        uid = await add_user(setup)
    async with db_pool.acquire() as c1, db_pool.acquire() as c2:
        tx = c1.transaction()
        await tx.start()
        try:
            await identity.resolve(c1, Sender(wa_id=WITH_NINE))
            locked = await c2.fetchval(
                "SELECT id FROM access.users WHERE id = $1 FOR UPDATE NOWAIT", uid
            ) if False else await _try_nowait(c2, uid)
            assert locked is False
        finally:
            await tx.rollback()


async def _try_nowait(conn: asyncpg.Connection, uid: int) -> bool:
    try:
        async with conn.transaction():
            await conn.fetchval("SELECT id FROM access.users WHERE id = $1 FOR UPDATE NOWAIT", uid)
    except asyncpg.LockNotAvailableError:
        return False
    return True


def _text() -> "InboundMessage":  # noqa: F821
    from app.services.inbound import ContentKind, InboundMessage

    return InboundMessage(
        wa_message_id="wamid.T", sender=Sender(wa_id=WITH_NINE), kind=ContentKind.TEXT, text="oi"
    )
