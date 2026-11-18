"""send_reply: envio, conclusao, sugestao, bloqueios e idempotencia (retorno-humano.1; RH-T08).

RH-7 (uma transacao), RH-9 (cabecalho e partes), RH-11 (token e complemento), RH-12
(bloqueios), RH-13 (pausa nao entra aqui: e do despacho), RH-16 (sugestao) e logs sem PII.
"""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services import human_reply
from app.services.human_reply import send_reply
from tests.brain import factories

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
ADMIN = "admin@example.test"
QUESTION = "Tem versão sem lactose?"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "channel_provider": "meta",
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
    }
    return Settings(_env_file=None, **{**base, **over})  # type: ignore[arg-type]


async def _ticket(
    conn: asyncpg.Connection,
    *,
    seq: int = 1,
    kind: str = "question",
    message: str | None = QUESTION,
    status: str = "awaiting",
    user_status: str = "active",
    classification: str = "PRODUTO",
) -> tuple[str, int]:
    user_id = await factories.create_active_user(conn, NOW, seq=seq)
    if user_status != "active":
        await _set_status(conn, user_id, user_status)
    number = f"J-{9000 + seq}"
    escalated = status == "awaiting"
    await conn.execute(
        "INSERT INTO chat.tickets (ticket_number, user_id, kind, brand, classification, message, "
        "escalated, status, urgency, created_at, updated_at) "
        "VALUES ($1, $2, $3, 'jungle', $4, $5, $6, $7, $8, $9, $9)",
        number, user_id, kind, classification, message, escalated, status,
        "urgente" if escalated else None, NOW,
    )  # fmt: skip
    return number, user_id


async def _set_status(conn: asyncpg.Connection, user_id: int, status: str) -> None:
    revoked = status == "revoked"
    await conn.execute(
        "UPDATE access.users SET status = $2, revoked_at = $3, revoked_by = $4, "
        "revoked_reason = $5 WHERE id = $1",
        user_id, status, NOW if revoked else None, "caio" if revoked else None,
        "left_company" if revoked else None,
    )  # fmt: skip


async def _send(conn: asyncpg.Connection, number: str, text: str, **over: Any) -> Any:
    args: dict[str, Any] = {
        "author_name": "Caio Franco",
        "author_email": ADMIN,
        "client_token": uuid.uuid4(),
        "settings": make_settings(),
        "clock": FakeClock(NOW),
    }
    args.update(over)
    return await send_reply(conn, number, text, **args)


async def _outbound(conn: asyncpg.Connection) -> list[asyncpg.Record]:
    return await conn.fetch("SELECT * FROM chat.outbound_messages ORDER BY id")


async def test_envia_conclui_sugere_e_audita(db_conn: asyncpg.Connection) -> None:
    number, user_id = await _ticket(db_conn)
    result = await _send(db_conn, number, "  Temos sim, a linha Zero.  ")
    assert (result.status, result.complement, len(result.outbound_ids)) == ("sent", False, 1)
    [out] = await _outbound(db_conn)
    assert out["body"] == "Retorno da equipe (Caio) sobre sua pergunta:\n\nTemos sim, a linha Zero."
    assert (out["kind"], out["status"], out["user_id"]) == ("human_reply", "pending", user_id)
    assert (out["author"], out["author_email"]) == ("Caio Franco", ADMIN)
    assert out["inbound_trace_id"] is None and out["client_token"] is not None
    ticket = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE ticket_number = $1", number)
    assert (ticket["status"], ticket["answered_by"], ticket["answered_at"]) == (
        "answered_human", "Caio Franco", NOW,
    )  # fmt: skip
    sug = await db_conn.fetchrow("SELECT * FROM content.faq_suggestions")
    assert (sug["ticket_number"], sug["question"], sug["answer"], sug["status"]) == (
        number, QUESTION, "Temos sim, a linha Zero.", "pendente",
    )  # fmt: skip
    assert (sug["brand"], sug["category"], sug["author"], sug["user_id"]) == (
        "jungle", "PRODUTO", "Caio Franco", user_id,
    )  # fmt: skip
    audit = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'ticket.reply'")
    assert (audit["actor"], audit["target_id"]) == (ADMIN, number)
    assert "linha Zero" not in str(audit["new_value"]) and "lactose" not in str(audit["old_value"])


async def test_texto_longo_sai_em_partes_na_ordem(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn)
    texto = " ".join(f"Frase {i} explicando o produto com calma." for i in range(60))
    result = await _send(db_conn, number, texto)
    rows = await _outbound(db_conn)
    assert len(rows) == len(result.outbound_ids) > 1
    assert all(len(r["body"]) <= 700 for r in rows)
    assert rows[0]["body"].startswith("Retorno da equipe (Caio) sobre sua pergunta:")
    assert [r["client_token"] is not None for r in rows] == [True] + [False] * (len(rows) - 1)
    assert {r["ticket_id"] for r in rows} == {rows[0]["ticket_id"]}


async def test_sem_nome_cabecalho_sem_parenteses(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn)
    await _send(db_conn, number, "Temos.", author_name=None)
    [out] = await _outbound(db_conn)
    assert out["body"].startswith("Retorno da equipe sobre sua pergunta:\n\n")
    assert out["author"] == ADMIN


@pytest.mark.parametrize(("text", "reason"), [("   ", "empty"), ("x" * 4001, "too_long")])
async def test_texto_invalido_nao_grava_nada(
    db_conn: asyncpg.Connection, text: str, reason: str
) -> None:
    number, _ = await _ticket(db_conn)
    result = await _send(db_conn, number, text)
    assert (result.status, result.reason) == ("invalid", reason)
    assert await _outbound(db_conn) == []


async def test_ticket_inexistente(db_conn: asyncpg.Connection) -> None:
    assert (await _send(db_conn, "J-0404", "Oi")).status == "not_found"


@pytest.mark.parametrize("user_status", ["revoked", "declined", "pending_optin"])
async def test_vendedor_inativo_bloqueia_sem_gravar(
    db_conn: asyncpg.Connection, user_status: str
) -> None:
    number, _ = await _ticket(db_conn, user_status=user_status)
    result = await _send(db_conn, number, "Temos sim.")
    assert (result.status, result.reason) == ("blocked", user_status)
    assert await _outbound(db_conn) == []
    assert await db_conn.fetchval("SELECT status FROM chat.tickets") == "awaiting"
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_suggestions") == 0
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 0


async def test_vendedor_excluido_pela_lgpd(db_conn: asyncpg.Connection) -> None:
    number, user_id = await _ticket(db_conn)
    await _set_status(db_conn, user_id, "revoked")
    await db_conn.execute("UPDATE access.users SET name = 'anon:' || id WHERE id = $1", user_id)
    result = await _send(db_conn, number, "Temos sim.")
    assert (result.status, result.reason) == ("blocked", "deleted")


async def test_mesmo_token_nao_reenvia(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn)
    token = uuid.uuid4()
    assert (await _send(db_conn, number, "Temos.", client_token=token)).status == "sent"
    assert (await _send(db_conn, number, "Temos.", client_token=token)).status == "duplicate"
    assert len(await _outbound(db_conn)) == 1


async def test_segunda_resposta_e_complemento(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn)
    await _send(db_conn, number, "Temos sim.")
    second = await _send(
        db_conn, number, "Esqueci: vem em 6 sabores.", author_name="Bia Lima",
        author_email="bia@example.test", clock=FakeClock(NOW.replace(hour=13)),
    )  # fmt: skip
    assert (second.status, second.complement) == ("sent", True)
    ticket = await db_conn.fetchrow("SELECT answered_by, answered_at FROM chat.tickets")
    assert (ticket["answered_by"], ticket["answered_at"]) == ("Caio Franco", NOW)
    rows = await _outbound(db_conn)
    assert len(rows) == 2 and rows[1]["body"].startswith("Retorno da equipe (Bia)")
    sug = await db_conn.fetchrow("SELECT answer, author FROM content.faq_suggestions")
    assert sug["answer"] == "Temos sim.\n\nEsqueci: vem em 6 sabores."
    assert sug["author"] == "Caio Franco"


async def test_complemento_nao_mexe_em_sugestao_ja_decidida(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn)
    await _send(db_conn, number, "Temos sim.")
    await db_conn.execute(
        "UPDATE content.faq_suggestions SET status = 'descartada', decided_by = 'x', "
        "decided_at = $1",
        NOW,
    )
    await _send(db_conn, number, "Mais uma coisa.")
    assert await db_conn.fetchval("SELECT answer FROM content.faq_suggestions") == "Temos sim."


async def test_midia_nao_gera_sugestao(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn, kind="media", message="[áudio]", classification="MIDIA")
    assert (await _send(db_conn, number, "Ouvimos o áudio: sim.")).status == "sent"
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_suggestions") == 0
    assert await db_conn.fetchval("SELECT status FROM chat.tickets") == "answered_human"


async def test_categoria_fora_da_faq_fica_vazia(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn, classification="NAO_IDENTIFICADO")
    await _send(db_conn, number, "Temos.")
    assert await db_conn.fetchval("SELECT category FROM content.faq_suggestions") is None


async def test_pergunta_apagada_nao_gera_sugestao(db_conn: asyncpg.Connection) -> None:
    number, _ = await _ticket(db_conn, message=None)
    assert (await _send(db_conn, number, "Temos.")).status == "sent"
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_suggestions") == 0


async def test_falha_no_meio_desfaz_tudo(
    db_conn: asyncpg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RH-7: se a auditoria falha, nada fica gravado (fila, ticket, sugestao)."""
    number, _ = await _ticket(db_conn)

    async def boom(*_a: Any, **_k: Any) -> int:
        raise RuntimeError("auditoria fora")

    monkeypatch.setattr(human_reply.audit, "write", boom)
    with pytest.raises(RuntimeError):
        await _send(db_conn, number, "Temos sim.")
    assert await _outbound(db_conn) == []
    assert await db_conn.fetchval("SELECT status FROM chat.tickets") == "awaiting"
    assert await db_conn.fetchval("SELECT count(*) FROM content.faq_suggestions") == 0


async def test_logs_sem_texto_nome_nem_telefone(
    db_conn: asyncpg.Connection, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    number, _ = await _ticket(db_conn)
    await _send(db_conn, number, "Temos a linha Zero.")
    blocked, _ = await _ticket(db_conn, seq=2, user_status="revoked")
    await _send(db_conn, blocked, "Temos.")
    assert "human_reply_queued" in caplog.text and "human_reply_blocked" in caplog.text
    for secret in ("linha Zero", "lactose", "Caio", "Vendedor Cerebro", "+5511"):
        assert secret not in caplog.text
    for record in caplog.records:
        assert "linha Zero" not in str(record.__dict__)


def test_nao_importa_o_painel() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(human_reply))
    modules = [
        n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
    ] + [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    assert not [m for m in modules if m.startswith("app.panel")]
