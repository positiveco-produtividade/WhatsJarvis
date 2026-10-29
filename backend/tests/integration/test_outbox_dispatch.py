"""outbox.dispatch: claim, status relido, politica, tentativas e rastro (AC-22/23/30/33, R4)."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.core.errors import InvalidValue
from app.core.values import OutboundKind, OutboundStatus
from app.services import outbox
from app.services.channel import ReplyButton, TemplateMessage
from app.services.flags import Flags
from app.services.reply_policy import AllowAllPolicy, FlagsReplyPolicy, PolicyDecision
from tests.fakes import FakeChannel

RECEIVED = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
NOW = RECEIVED + timedelta(milliseconds=1500)
PHONE = "+5511900000001"


@dataclass(frozen=True)
class Target:
    id: int
    phone_e164: str


class DenyPolicy:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    async def allow(
        self, conn: Any, user: Any, kind: str, brand: str | None = None
    ) -> PolicyDecision:
        return PolicyDecision(ok=False, reason=self._reason)


async def add_user(conn: asyncpg.Connection, status: str = "active") -> Target:
    user_id: int = await conn.fetchval(
        "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
        "brands, manager_name, status, status_changed_at, created_at, created_by, updated_at, "
        "accepted_at, accepted_notice_version, revoked_at, revoked_by, revoked_reason) "
        "VALUES ('Fulana', $1, '551100000001', 'vendedor', 'Acme', '{SP}', '{farma}', "
        "'{jungle}', 'Gestora', $3::text, $2::timestamptz, $2::timestamptz, 'teste', "
        "$2::timestamptz, "
        "CASE WHEN $3::text IN ('active','revoked') THEN $2::timestamptz END, "
        "CASE WHEN $3::text IN ('active','revoked') THEN 'v1' END, "
        "CASE WHEN $3::text = 'revoked' THEN $2::timestamptz END, "
        "CASE WHEN $3::text = 'revoked' THEN 'caio' END, "
        "CASE WHEN $3::text = 'revoked' THEN 'left_company' END) RETURNING id",
        PHONE,
        RECEIVED,
        status,
    )
    return Target(id=user_id, phone_e164=PHONE)


async def add_trace(conn: asyncpg.Connection, user: Target) -> int:
    trace_id: int = await conn.fetchval(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
        "access_decision, decision, reply_kind) "
        "VALUES ('wamid.IN1', $1, 'text', $2, 'allowed', 'allowed', 'pending') RETURNING id",
        RECEIVED,
        user.id,
    )
    return trace_id


async def add_notice(conn: asyncpg.Connection, user: Target) -> int:
    notice_id: int = await conn.fetchval(
        "INSERT INTO access.optin_requests (user_id, status, notice_version, issued_at, issued_by) "
        "VALUES ($1, 'open', 'v1', $2, 'teste') RETURNING id",
        user.id,
        RECEIVED,
    )
    return notice_id


async def row_of(conn: asyncpg.Connection, outbound_id: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE id = $1", outbound_id)
    assert row is not None
    return row


async def trace_of(conn: asyncpg.Connection, trace_id: int) -> asyncpg.Record:
    row = await conn.fetchrow("SELECT * FROM chat.message_trace WHERE id = $1", trace_id)
    assert row is not None
    return row


async def queue_text(
    conn: asyncpg.Connection,
    user: Target,
    trace_id: int | None = None,
    kind: OutboundKind = OutboundKind.UNDER_CONSTRUCTION,
) -> int:
    return await outbox.enqueue(
        conn, user, kind, body="Em construcao", inbound_trace_id=trace_id, clock=FakeClock(RECEIVED)
    )


async def run(pool: asyncpg.Pool, outbound_id: int, channel: FakeChannel, **kw: Any) -> Any:
    return await outbox.dispatch(pool, outbound_id, channel=channel, clock=FakeClock(NOW), **kw)


@pytest.mark.db
async def test_envio_ok_atualiza_fila_e_rastro(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        channel = FakeChannel()
        assert await run(db_pool, out_id, channel) is OutboundStatus.SENT
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
    assert [(c.method, c.to, c.body) for c in channel.calls] == [
        ("send_text", PHONE, "Em construcao")
    ]
    assert row["status"] == "sent" and row["attempts"] == 1
    assert row["wa_message_id"] == "wamid.FAKE1" and row["sent_at"] == NOW
    assert tr["send_result"] == "sent" and tr["reply_wa_message_id"] == "wamid.FAKE1"
    assert tr["reply_sent_at"] == NOW and tr["latency_ms"] == 1500
    assert tr["decision"] == "allowed"


@pytest.mark.db
async def test_boas_vindas_vai_como_modelo_com_tuplas(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, "pending_optin")
        notice_id = await add_notice(conn, user)
        template = {
            "name": "boas_vindas",
            "language": "pt_BR",
            "body_params": ["Fulana"],
            "quick_reply_payloads": [f"OPTIN_ACCEPT:{notice_id}", f"OPTIN_DECLINE:{notice_id}"],
        }
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.WELCOME_TEMPLATE, template=template, optin_request_id=notice_id
        )
        channel = FakeChannel()
        assert await run(db_pool, out_id, channel) is OutboundStatus.SENT
    (call,) = channel.calls
    assert call.method == "send_template" and call.to == PHONE
    assert call.template == TemplateMessage(
        name="boas_vindas",
        language="pt_BR",
        body_params=("Fulana",),
        quick_reply_payloads=(f"OPTIN_ACCEPT:{notice_id}", f"OPTIN_DECLINE:{notice_id}"),
    )
    assert isinstance(call.template.body_params, tuple)


@pytest.mark.db
async def test_nome_do_modelo_vazio_falha_alto(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, "pending_optin")
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.WELCOME_TEMPLATE, template={"name": "", "language": "pt_BR"}
        )
        channel = FakeChannel()
        with pytest.raises(InvalidValue):
            await run(db_pool, out_id, channel)
        row = await row_of(conn, out_id)
    assert channel.calls == []
    assert row["status"] == "failed" and row["error_code"] == "invalid_content"


@pytest.mark.db
async def test_lembrete_vai_com_botoes_do_aviso(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, "pending_optin")
        notice_id = await add_notice(conn, user)
        out_id = await outbox.enqueue(
            conn, user, OutboundKind.OPTIN_REMINDER, body="Lembrete", optin_request_id=notice_id
        )
        channel = FakeChannel()
        assert await run(db_pool, out_id, channel) is OutboundStatus.SENT
    (call,) = channel.calls
    assert call.method == "send_reply_buttons" and call.body == "Lembrete"
    assert call.buttons == (
        ReplyButton(f"OPTIN_ACCEPT:{notice_id}", "Aceito"),
        ReplyButton(f"OPTIN_DECLINE:{notice_id}", "Não aceito"),
    )


@pytest.mark.db
async def test_revogacao_entre_enfileirar_e_enviar_descarta(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        await conn.execute(
            "UPDATE access.users SET status = 'revoked', revoked_at = $1, "
            "revoked_by = 'caio', revoked_reason = 'left_company' WHERE id = $2",
            RECEIVED,
            user.id,
        )
        channel = FakeChannel()
        assert await run(db_pool, out_id, channel) is OutboundStatus.DISCARDED
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
    assert channel.calls == []
    assert row["status"] == "discarded" and row["discard_reason"] == "revoked_before_send"
    assert tr["decision"] == "revoked_before_send" and tr["send_result"] == "discarded"


@pytest.mark.db
async def test_status_diferente_do_exigido_vira_status_changed(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn, "pending_optin")
        out_id = await queue_text(conn, user)  # exige active
        channel = FakeChannel()
        assert await run(db_pool, out_id, channel) is OutboundStatus.DISCARDED
        row = await row_of(conn, out_id)
    assert channel.calls == []
    assert row["discard_reason"] == "status_changed"


@pytest.mark.db
@pytest.mark.parametrize("reason", ["paused", "brand_disabled"])
async def test_politica_nega_descarta_sem_enviar_nem_alertar(
    db_pool: asyncpg.Pool, reason: str
) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        channel = FakeChannel()
        status = await run(db_pool, out_id, channel, policy=DenyPolicy(reason))
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
        alerts = await conn.fetchval("SELECT count(*) FROM ops.alerts")
    assert status is OutboundStatus.DISCARDED and channel.calls == []
    assert row["status"] == "discarded" and row["discard_reason"] == reason
    assert tr["send_result"] == "discarded" and tr["decision"] == "allowed"
    assert alerts == 0


@pytest.mark.db
async def test_pausa_real_nega_conversa_e_isenta_consentimento(db_pool: asyncpg.Pool) -> None:
    flags = Flags(db_pool, FakeClock(RECEIVED))
    async with db_pool.acquire() as conn:
        await flags.set_bool(conn, "paused", True, "caio")
        user = await add_user(conn)
        conversa = await queue_text(conn, user)
        consent = await queue_text(conn, user, kind=OutboundKind.OPTIN_CONFIRMATION)
        channel = FakeChannel()
        policy = FlagsReplyPolicy(flags)
        assert await run(db_pool, conversa, channel, policy=policy) is OutboundStatus.DISCARDED
        assert await run(db_pool, consent, channel, policy=policy) is OutboundStatus.SENT
        row = await row_of(conn, conversa)
    assert row["discard_reason"] == "paused" and len(channel.calls) == 1


@pytest.mark.db
async def test_set_reply_policy_vale_como_padrao(db_pool: asyncpg.Pool) -> None:
    outbox.set_reply_policy(DenyPolicy("paused"))
    try:
        async with db_pool.acquire() as conn:
            user = await add_user(conn)
            out_id = await queue_text(conn, user)
            channel = FakeChannel()
            assert await run(db_pool, out_id, channel) is OutboundStatus.DISCARDED
    finally:
        outbox.set_reply_policy(AllowAllPolicy())
    assert channel.calls == []


@pytest.mark.db
async def test_timeout_ambiguo_nao_reenvia(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        channel = FakeChannel()
        channel.fail_next("ambiguous")
        assert await run(db_pool, out_id, channel) is OutboundStatus.UNKNOWN
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
    assert len(channel.calls) == 1
    assert row["status"] == "unknown" and row["attempts"] == 1
    assert tr["send_result"] == "unknown" and tr["decision"] == "allowed"


@pytest.mark.db
async def test_transitorio_reenvia_uma_vez_e_conclui(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        out_id = await queue_text(conn, user)
        channel = FakeChannel()
        channel.fail_next("transient", code="429")
        assert await run(db_pool, out_id, channel) is OutboundStatus.SENT
        row = await row_of(conn, out_id)
    assert len(channel.calls) == 2 and row["attempts"] == 2 and row["status"] == "sent"


@pytest.mark.db
async def test_transitorio_duas_vezes_falha_sem_terceira(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        channel = FakeChannel()
        channel.fail_always("transient", code="503")
        assert await run(db_pool, out_id, channel) is OutboundStatus.FAILED
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
    assert len(channel.calls) == 2 and row["attempts"] == 2
    assert row["status"] == "failed" and row["error_code"] == "503"
    assert tr["decision"] == "send_failed" and tr["send_result"] == "failed"


@pytest.mark.db
async def test_4xx_definitivo_marca_send_failed_sem_retentativa(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        trace_id = await add_trace(conn, user)
        out_id = await queue_text(conn, user, trace_id)
        channel = FakeChannel()
        channel.fail_next("definitive", code="131047")
        assert await run(db_pool, out_id, channel) is OutboundStatus.FAILED
        row = await row_of(conn, out_id)
        tr = await trace_of(conn, trace_id)
    assert len(channel.calls) == 1 and row["attempts"] == 1
    assert row["status"] == "failed" and row["error_code"] == "131047"
    assert tr["decision"] == "send_failed" and tr["send_error"] == "131047"


@pytest.mark.db
async def test_claim_duplicado_envia_uma_vez(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        user = await add_user(conn)
        out_id = await queue_text(conn, user)
    channel = FakeChannel()
    results = await asyncio.gather(*(run(db_pool, out_id, channel) for _ in range(4)))
    assert sorted(str(r) for r in results) == sorted(["sent", "None", "None", "None"])
    assert len(channel.calls) == 1


@pytest.mark.db
async def test_saida_inexistente_ou_ja_tratada_devolve_none(db_pool: asyncpg.Pool) -> None:
    channel = FakeChannel()
    assert await run(db_pool, 999, channel) is None
    assert channel.calls == []
