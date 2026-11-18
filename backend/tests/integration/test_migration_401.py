"""Migration 401: resposta humana, acks, sugestoes e origem `sugestao` (RH-T01, spec 9.2)."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import asyncpg
import pytest

from app.db import migrate
from tests.brain import factories
from tests.db_support import MIGRATIONS_DIR

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
KINDS = (
    "welcome_template", "optin_reminder", "optin_confirmation", "optin_declined",
    "under_construction", "rate_limit_notice", "media_ack", "opt_out_confirmation",
    "brain_answer", "brain_answer_rate", "brain_notice", "brand_poll", "human_reply",
)  # fmt: skip
ORIGINS = ("manual", "import", "seed", "nielsen", "gmr", "sugestao")


async def _ticket(conn: asyncpg.Connection, user_id: int, number: str = "J-9001") -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.tickets (ticket_number, user_id, kind, brand, message, status, "
            "escalated, urgency, created_at, updated_at) "
            "VALUES ($1, $2, 'question', 'jungle', 'Tem sem lactose?', 'awaiting', true, "
            "'urgente', $3, $3) RETURNING id",
            number, user_id, NOW,
        )
    )  # fmt: skip


async def _outbound(
    conn: asyncpg.Connection,
    user_id: int,
    kind: str,
    *,
    ticket_id: int | None = None,
    token: uuid.UUID | None = None,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
            "status, next_attempt_at, created_at, ticket_id, author, author_email, client_token) "
            "VALUES ($1, '+5500000000002', $2, 'active', 'x', 'pending', $3, $3, $4, "
            "CASE WHEN $4::bigint IS NULL THEN NULL ELSE 'Caio' END, "
            "CASE WHEN $4::bigint IS NULL THEN NULL ELSE 'admin@example.test' END, $5) "
            "RETURNING id",
            user_id, kind, NOW, ticket_id, token,
        )
    )  # fmt: skip


async def _faq_item(conn: asyncpg.Connection, origin: str, question: str = "Pergunta") -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO content.faq_items (brand, category, question, question_key, answer, "
            "bot_responde, origin, origin_ref, created_at, created_by, updated_at, updated_by) "
            "VALUES ('jungle', 'PRODUTO', $2, $2, 'Resposta', true, $1, 'J-9001', "
            "$3, 't', $3, 't') RETURNING id",
            origin, question, NOW,
        )
    )  # fmt: skip


async def _suggestion(
    conn: asyncpg.Connection,
    ticket_id: int | None,
    user_id: int,
    *,
    number: str = "J-9001",
    status: str = "pendente",
    decided_at: datetime | None = None,
    faq_item_id: int | None = None,
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO content.faq_suggestions (ticket_id, ticket_number, user_id, brand, "
            "category, question, answer, status, author, decided_by, decided_at, faq_item_id, "
            "created_at, updated_at) "
            "VALUES ($1, $2, $3, 'jungle', 'PRODUTO', 'Tem sem lactose?', 'Temos sim.', $4, "
            "'Caio', CASE WHEN $5::timestamptz IS NULL THEN NULL ELSE 'Caio' END, $5, $6, $7, $7) "
            "RETURNING id",
            ticket_id, number, user_id, status, decided_at, faq_item_id, NOW,
        )
    )  # fmt: skip


async def test_colunas_novas_da_saida(db_conn: asyncpg.Connection) -> None:
    rows = await db_conn.fetch(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'chat' AND table_name = 'outbound_messages' "
        "AND column_name IN ('ticket_id', 'author', 'author_email', 'client_token', "
        "'delivered_at', 'read_at') ORDER BY column_name"
    )
    assert [(r["column_name"], r["data_type"]) for r in rows] == [
        ("author", "text"),
        ("author_email", "text"),
        ("client_token", "uuid"),
        ("delivered_at", "timestamp with time zone"),
        ("read_at", "timestamp with time zone"),
        ("ticket_id", "bigint"),
    ]


@pytest.mark.parametrize("kind", KINDS)
async def test_aceita_human_reply_e_kinds_antigos(db_conn: asyncpg.Connection, kind: str) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    await _outbound(db_conn, user_id, kind)


async def test_recusa_kind_invalido(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _outbound(db_conn, user_id, "resposta_inexistente")


async def test_um_unico_check_de_kind(db_conn: asyncpg.Connection) -> None:
    names = await db_conn.fetch(
        "SELECT c.conname FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        "WHERE c.conrelid = 'chat.outbound_messages'::regclass AND c.contype = 'c' "
        "AND a.attname = 'kind'"
    )
    assert [r["conname"] for r in names] == ["outbound_messages_kind_check"]


async def test_token_do_cliente_unico(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    ticket_id = await _ticket(db_conn, user_id)
    token = uuid.uuid4()
    await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id, token=token)
    await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id)  # partes sem token
    await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id, token=token)


async def test_ticket_apagado_deixa_saida_e_sugestao_sem_ticket(
    db_conn: asyncpg.Connection,
) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    ticket_id = await _ticket(db_conn, user_id)
    out_id = await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id)
    sug_id = await _suggestion(db_conn, ticket_id, user_id)
    await db_conn.execute("DELETE FROM chat.tickets WHERE id = $1", ticket_id)
    assert await db_conn.fetchval(
        "SELECT ticket_id FROM chat.outbound_messages WHERE id = $1", out_id
    ) is None
    row = await db_conn.fetchrow(
        "SELECT ticket_id, ticket_number FROM content.faq_suggestions WHERE id = $1", sug_id
    )
    assert row is not None and row["ticket_id"] is None and row["ticket_number"] == "J-9001"


@pytest.mark.parametrize("origin", ORIGINS)
async def test_faq_aceita_origem_sugestao(db_conn: asyncpg.Connection, origin: str) -> None:
    await _faq_item(db_conn, origin, question=f"Pergunta {origin}")


async def test_faq_recusa_origem_invalida(db_conn: asyncpg.Connection) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await _faq_item(db_conn, "inbox")


async def test_uma_sugestao_por_ticket(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    ticket_id = await _ticket(db_conn, user_id)
    await _suggestion(db_conn, ticket_id, user_id)
    with pytest.raises(asyncpg.UniqueViolationError):
        await _suggestion(db_conn, ticket_id, user_id)


async def test_sugestao_nasce_pendente(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    sug_id = await _suggestion(db_conn, await _ticket(db_conn, user_id), user_id)
    assert await db_conn.fetchval(
        "SELECT status FROM content.faq_suggestions WHERE id = $1", sug_id
    ) == "pendente"


async def test_sugestao_recusa_status_invalido(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _suggestion(
            db_conn, await _ticket(db_conn, user_id), user_id, status="publicada", decided_at=NOW
        )


@pytest.mark.parametrize(
    ("status", "decided_at"), [("pendente", NOW), ("descartada", None)]
)
async def test_decisao_e_data_andam_juntas(
    db_conn: asyncpg.Connection, status: str, decided_at: datetime | None
) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    with pytest.raises(asyncpg.CheckViolationError):
        await _suggestion(
            db_conn, await _ticket(db_conn, user_id), user_id, status=status, decided_at=decided_at
        )


async def test_aprovada_exige_item_da_faq(db_conn: asyncpg.Connection) -> None:
    user_id = await factories.create_active_user(db_conn, NOW)
    ticket_id = await _ticket(db_conn, user_id)
    with pytest.raises(asyncpg.CheckViolationError):
        await _suggestion(db_conn, ticket_id, user_id, status="aprovada", decided_at=NOW)
    item_id = await _faq_item(db_conn, "sugestao")
    await _suggestion(
        db_conn, ticket_id, user_id, status="aprovada", decided_at=NOW, faq_item_id=item_id
    )


async def test_migration_idempotente(
    db_conn: asyncpg.Connection, owner_conn: asyncpg.Connection
) -> None:
    sql = (MIGRATIONS_DIR / "401_retorno_humano.sql").read_text(encoding="utf-8")
    await owner_conn.execute(sql)
    await owner_conn.execute(sql)
    # Reexecutar a 401 recria o CHECK de `kind` com a lista dela; as posteriores que ampliam a
    # lista (403, brand_access_granted) sao reaplicadas para nao vazar o estado (cadastro.1).
    for later in migrate.discover(MIGRATIONS_DIR):
        if later.number > 401:
            await owner_conn.execute(later.sql)
    user_id = await factories.create_active_user(db_conn, NOW)
    ticket_id = await _ticket(db_conn, user_id)
    await _outbound(db_conn, user_id, "human_reply", ticket_id=ticket_id, token=uuid.uuid4())
    await _faq_item(db_conn, "sugestao")
    await _suggestion(db_conn, ticket_id, user_id)


async def test_migrate_check_sem_pendentes(db_conn: asyncpg.Connection) -> None:
    migrations = migrate.discover(MIGRATIONS_DIR)
    assert any(m.version == "401_retorno_humano" for m in migrations)
    assert await migrate.is_up_to_date(db_conn, MIGRATIONS_DIR)
