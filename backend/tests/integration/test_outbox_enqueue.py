"""outbox.enqueue: destino, required_status por tipo, kind invalido, transacao (AC-17/21/23/30)."""
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import OutboundKind
from app.services import outbox

NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)

EXPECTED = {
    "welcome_template": "pending_optin",
    "optin_reminder": "pending_optin",
    "optin_declined": "declined",
    "optin_confirmation": "active",
    "under_construction": "active",
    "rate_limit_notice": "active",
    "media_ack": "active",
    "opt_out_confirmation": "active",
    "brain_answer": "active",
    "brain_answer_rate": "active",
    "brain_notice": "active",
    "brand_poll": "active",  # enquete da marca da conversa (canal-waha.1)
    "human_reply": "active",  # resposta da equipe pelo painel (retorno-humano.1)
}


@dataclass(frozen=True)
class Target:
    id: int
    phone_e164: str


async def add_user(conn: asyncpg.Connection) -> Target:
    phone = "+5511900000001"
    user_id: int = await conn.fetchval(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
        "brands, manager_name, status, status_changed_at, created_at, created_by, updated_at) "
        "VALUES ('Fulana', $1, '551100000001', 'vendedor', 'Acme', '{SP}', '{farma}', "
        "'{jungle}', 'Gestora', 'pending_optin', $2, $2, 'teste', $2) RETURNING id",
        phone,
        NOW,
    )
    return Target(id=user_id, phone_e164=phone)


async def fetch(conn: asyncpg.Connection, row_id: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", row_id)
    assert row is not None
    return row


def test_mapa_cobre_todos_os_kinds() -> None:
    assert {k.value for k in outbox.REQUIRED_STATUS} == {k.value for k in OutboundKind}
    assert {k.value: v.value for k, v in outbox.REQUIRED_STATUS.items()} == EXPECTED


@pytest.mark.db
async def test_boas_vindas_como_modelo_e_destino_e_phone_e164(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    tpl: dict[str, Any] = {"name": "boas_vindas", "lang": "pt_BR", "params": ["Fulana"]}
    clock = FakeClock(NOW)
    row_id = await outbox.enqueue(
        db_conn, user, OutboundKind.WELCOME_TEMPLATE, template=tpl, clock=clock
    )
    row = await fetch(db_conn, row_id)
    assert row["to_phone"] == user.phone_e164
    assert row["user_id"] == user.id
    assert row["kind"] == "welcome_template"
    assert row["required_status"] == "pending_optin"
    assert row["body"] is None
    assert row["status"] == "pending" and row["attempts"] == 0
    assert row["next_attempt_at"] == NOW and row["created_at"] == NOW
    assert row["claimed_at"] is None and row["sent_at"] is None
    name = await db_conn.fetchval(
        "SELECT template->>'name' FROM chat.outbound_messages WHERE id = $1", row_id
    )
    assert name == "boas_vindas"


@pytest.mark.db
@pytest.mark.parametrize(("kind", "required"), sorted(EXPECTED.items()))
async def test_required_status_por_tipo(
    db_conn: asyncpg.Connection, kind: str, required: str
) -> None:
    user = await add_user(db_conn)
    row_id = await outbox.enqueue(db_conn, user, kind, body="oi")
    row = await fetch(db_conn, row_id)
    assert row["kind"] == kind
    assert row["required_status"] == required
    assert row["body"] == "oi"


@pytest.mark.db
async def test_vinculos_opcionais(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    notice_id = await db_conn.fetchval(
        "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, issued_by) "
        "VALUES ($1, 'open', 'v1', $2, 'teste') RETURNING id",
        user.id,
        NOW,
    )
    row_id = await outbox.enqueue(
        db_conn, user, OutboundKind.OPTIN_REMINDER, body="x", optin_request_id=notice_id
    )
    row = await fetch(db_conn, row_id)
    assert row["optin_request_id"] == notice_id and row["inbound_trace_id"] is None


@pytest.mark.db
async def test_kind_invalido_recusado_sem_gravar(db_conn: asyncpg.Connection) -> None:
    user = await add_user(db_conn)
    with pytest.raises(InvalidValue):
        await outbox.enqueue(db_conn, user, "promo", body="x")
    assert await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


@pytest.mark.db
async def test_roda_na_transacao_do_chamador(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        with pytest.raises(RuntimeError):
            async with conn.transaction():
                await outbox.enqueue(conn, user, OutboundKind.MEDIA_ACK, body="x")
                raise RuntimeError("rollback")
        assert await conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == 0


async def add_ticket(conn: asyncpg.Connection, user: Target) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.tickets (ticket_number, user_id, kind, status, created_at, "
            "updated_at) VALUES ('J-0777', $1, 'question', 'awaiting', $2, $2) RETURNING id",
            user.id,
            NOW,
        )
    )


@pytest.mark.db
async def test_resposta_humana_grava_ticket_autor_e_token(db_conn: asyncpg.Connection) -> None:
    """RH-7/RH-11: a resposta da equipe leva ticket, autor e token; as outras saidas nao."""
    user = await add_user(db_conn)
    ticket_id = await add_ticket(db_conn, user)
    token = uuid.uuid4()
    row_id = await outbox.enqueue(
        db_conn,
        user,
        OutboundKind.HUMAN_REPLY,
        body="Retorno da equipe (Caio) sobre sua pergunta: Temos sim.",
        ticket_id=ticket_id,
        author="Caio",
        author_email="admin@example.test",
        client_token=token,
        clock=FakeClock(NOW),
    )
    row = await fetch(db_conn, row_id)
    assert (row["kind"], row["required_status"]) == ("human_reply", "active")
    assert (row["ticket_id"], row["author"], row["author_email"], row["client_token"]) == (
        ticket_id,
        "Caio",
        "admin@example.test",
        token,
    )
    assert row["delivered_at"] is None and row["read_at"] is None
    other = await fetch(
        db_conn, await outbox.enqueue(db_conn, user, OutboundKind.MEDIA_ACK, body="ok")
    )
    assert (other["ticket_id"], other["author"], other["client_token"]) == (None, None, None)


@pytest.mark.db
async def test_token_repetido_e_recusado(db_conn: asyncpg.Connection) -> None:
    """RH-11: o mesmo token (clique duplo) nao grava uma segunda resposta."""
    user = await add_user(db_conn)
    ticket_id = await add_ticket(db_conn, user)
    token = uuid.uuid4()
    await outbox.enqueue(
        db_conn, user, OutboundKind.HUMAN_REPLY, body="a", ticket_id=ticket_id, client_token=token
    )
    with pytest.raises(asyncpg.UniqueViolationError):
        await outbox.enqueue(
            db_conn,
            user,
            OutboundKind.HUMAN_REPLY,
            body="a",
            ticket_id=ticket_id,
            client_token=token,
        )
