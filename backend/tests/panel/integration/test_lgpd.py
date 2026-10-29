"""LGPD exportar e excluir (PN-56, PN-57) em Postgres real."""
from __future__ import annotations

import json
from datetime import timedelta

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import lgpd, people_read
from tests.panel import factories

pytestmark = pytest.mark.db

NAME = "Zuleica Quaresma"
OTHER = "Outro Vendedor"


async def _attempt(
    conn: asyncpg.Connection, clock: FakeClock, phone_key: str, user_id: int | None
) -> int:
    attempt_id = int(
        await conn.fetchval(
            "INSERT INTO access.access_attempts (phone_key, user_id, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type) "
            "VALUES ($1, $2, 'unknown', 1, $3, $3, 'text') RETURNING id",
            phone_key, user_id, clock.now(),
        )  # fmt: skip
    )
    await conn.execute(
        "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at) "
        "VALUES ($1, 'oi sou novo', $2)",
        attempt_id, clock.now(),
    )  # fmt: skip
    return attempt_id


async def _trace(
    conn: asyncpg.Connection, clock: FakeClock, wa: str, *, user_id: int | None,
    attempt_id: int | None = None, body: str = "texto da mensagem", age_days: int = 1,
) -> int:  # fmt: skip
    return int(
        await conn.fetchval(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "attempt_id, access_decision, decision, body) "
            "VALUES ($1, $2, 'text', $3, $4, 'allowed', 'answered', $5) RETURNING id",
            wa, clock.now() - timedelta(days=age_days), user_id, attempt_id, body,
        )  # fmt: skip
    )


async def _outbound(conn: asyncpg.Connection, clock: FakeClock, user_id: int, phone: str) -> None:
    await conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "template, status, next_attempt_at, created_at) "
        "VALUES ($1, $2, 'under_construction', 'active', 'resposta ao vendedor', "
        "'{\"a\": 1}'::jsonb, 'sent', $3, $3)",
        user_id, phone, clock.now(),
    )  # fmt: skip


async def _scenario(conn: asyncpg.Connection, clock: FakeClock) -> tuple[int, int]:
    uid = await factories.insert_user(
        conn, name=NAME, seq=70, brands=[factories.BRAND_A], now=clock.now()
    )
    other = await factories.insert_user(
        conn, name=OTHER, seq=71, brands=[factories.BRAND_A], now=clock.now()
    )
    phone = await conn.fetchval("SELECT phone_key FROM access.users WHERE id = $1", uid)
    att = await _attempt(conn, clock, phone, None)
    await _trace(conn, clock, "wa-1", user_id=uid, body="pergunta sobre preco")
    await _trace(conn, clock, "wa-2", user_id=None, attempt_id=att, body="oi sou novo")
    await _trace(conn, clock, "wa-old", user_id=uid, body="velha demais", age_days=400)
    await _trace(conn, clock, "wa-3", user_id=other, body="mensagem do outro")
    await _outbound(conn, clock, uid, f"+{phone}")
    await _outbound(conn, clock, other, "+5511999999999")
    await factories.insert_ticket(
        conn, number=701, user_id=uid, status="answered_bot", created_at=clock.now(),
        brand=factories.BRAND_A, message="ticket dela",
    )  # fmt: skip
    await factories.insert_ticket(
        conn, number=702, user_id=other, status="awaiting", created_at=clock.now(),
        brand=factories.BRAND_A, message="ticket do outro",
    )  # fmt: skip
    await conn.execute("UPDATE chat.tickets SET summary = 'resumo' WHERE user_id = $1", uid)
    return uid, other


async def test_export_so_dados_da_pessoa_e_audita_sem_pii(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    uid, _ = await _scenario(db_conn, clock)
    data = await lgpd.export_person(db_conn, uid, "admin@x.com", clock=clock)
    text = lgpd.export_to_json(data).decode()
    assert data["person"]["name"] == NAME
    bodies = {m["body"] for m in data["messages"]}
    assert bodies == {"pergunta sobre preco", "oi sou novo"}  # sem a de 400 dias nem a do outro
    assert [t["message"] for t in data["tickets"]] == ["ticket dela"]
    assert len(data["replies"]) == 1 and data["replies"][0]["body"] == "resposta ao vendedor"
    assert len(data["access_attempts"]) == 1
    assert OTHER not in text and "ticket do outro" not in text and "mensagem do outro" not in text
    row = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'lgpd.export'")
    assert row["actor_type"] == "panel" and row["actor"] == "admin@x.com"
    assert row["target_id"] == str(uid)
    logged = (row["old_value"] or "") + (row["new_value"] or "") + row["target_id"]
    assert NAME not in logged and "pergunta" not in logged
    assert json.loads(row["new_value"])["counts"]["messages"] == 2
    assert lgpd.export_filename(uid, clock.now()).startswith("dados-pessoa-")
    assert NAME not in lgpd.export_filename(uid, clock.now())


async def test_export_de_pessoa_revogada(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    uid = await factories.insert_user(
        db_conn, name=NAME, seq=72, brands=[factories.BRAND_A], now=clock.now()
    )
    await db_conn.execute(
        "UPDATE access.users SET status = 'revoked', revoked_at = $2, revoked_by = 'x', "
        "revoked_reason = 'left_company' WHERE id = $1",
        uid, clock.now(),
    )  # fmt: skip
    data = await lgpd.export_person(db_conn, uid, "admin", clock=clock)
    assert data["person"]["status"] == "revoked"


async def test_export_inexistente(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    with pytest.raises(lgpd.PersonNotFound):
        await lgpd.export_person(db_conn, 999999, "admin", clock=clock)


async def test_delete_anonimiza_apaga_e_audita_contagens(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    uid, other = await _scenario(db_conn, clock)
    phone = await db_conn.fetchval("SELECT phone_key FROM access.users WHERE id = $1", uid)
    res = await lgpd.delete_person(db_conn, uid, "admin@x.com", clock=clock)
    label = lgpd.anonymous_label(uid)

    user = await db_conn.fetchrow("SELECT * FROM access.users WHERE id = $1", uid)
    assert (user["name"], user["phone_e164"], user["phone_key"], user["bsuid"]) == (label,) * 4
    assert user["status"] == "revoked" and user["revoked_by"] == "admin@x.com"
    assert user["search_key"].startswith("anon:")
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.message_trace WHERE body IS NOT NULL AND "
        "(user_id = $1 OR attempt_id IS NOT NULL)", uid
    ) == 0  # fmt: skip
    assert await db_conn.fetchval("SELECT count(*) FROM chat.message_trace") == 4  # linhas ficam
    assert await db_conn.fetchval("SELECT count(*) FROM access.unknown_first_messages") == 0
    ob = await db_conn.fetchrow("SELECT * FROM chat.outbound_messages WHERE user_id = $1", uid)
    assert ob["body"] is None and ob["template"] is None and phone not in ob["to_phone"]
    tk = await db_conn.fetchrow("SELECT * FROM chat.tickets WHERE user_id = $1", uid)
    assert tk["message"] is None and tk["summary"] is None
    att = await db_conn.fetchrow("SELECT * FROM access.access_attempts")
    assert att["phone_key"] != phone and att["bsuid"] is None
    # a outra pessoa nao muda
    assert await db_conn.fetchval("SELECT name FROM access.users WHERE id = $1", other) == OTHER
    assert await db_conn.fetchval(
        "SELECT body FROM chat.message_trace WHERE user_id = $1", other
    ) == "mensagem do outro"  # fmt: skip
    assert await db_conn.fetchval(
        "SELECT message FROM chat.tickets WHERE user_id = $1", other
    ) == "ticket do outro"  # fmt: skip

    assert res.counts["message_trace_bodies"] == 3
    assert res.counts["unknown_first_messages"] == 1
    assert res.counts["tickets"] == 1 and res.counts["outbound_messages"] == 1
    row = await db_conn.fetchrow("SELECT * FROM ops.audit_log WHERE action = 'lgpd.delete'")
    assert json.loads(row["new_value"])["counts"] == res.counts
    assert row["target_id"] == str(uid)
    everything = " ".join(
        str(v) for r in await db_conn.fetch("SELECT * FROM ops.audit_log") for v in r.values()
    )
    assert NAME not in everything and phone not in everything
    revoked = await db_conn.fetch("SELECT * FROM ops.audit_log WHERE action = 'user.revoked'")
    assert len(revoked) == 1


async def test_some_das_buscas(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    uid, _ = await _scenario(db_conn, clock)
    phone = await db_conn.fetchval("SELECT phone_key FROM access.users WHERE id = $1", uid)
    before = await people_read.people_page(db_conn, "autorizadas", q="zuleica")
    assert before.total == 1
    await lgpd.delete_person(db_conn, uid, "admin", clock=clock)
    for tab in ("autorizadas", "retiradas"):
        for q in ("zuleica", "quaresma", phone, "Empresa Teste Zuleica"):
            page = await people_read.people_page(db_conn, tab, q=q)  # type: ignore[arg-type]
            assert page.total == 0, (tab, q)


async def test_assistente_melhor_esforco(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    uid, _ = await _scenario(db_conn, clock)
    for question, answer in (("quem e Zuleica Quaresma?", "ela e vendedora"), ("outra", "ok")):
        await db_conn.execute(
            "INSERT INTO ops.assistant_questions (asked_at, asked_by, agent, question, answer, "
            "status) VALUES ($1, 'a', 'g', $2, $3, 'ok')",
            clock.now(), question, answer,
        )  # fmt: skip
    res = await lgpd.delete_person(db_conn, uid, "admin", clock=clock)
    assert res.counts["assistant_questions"] == 1
    rows = await db_conn.fetch("SELECT question, answer FROM ops.assistant_questions ORDER BY id")
    assert rows[0]["question"] == lgpd.REMOVED_TEXT and rows[0]["answer"] == lgpd.REMOVED_TEXT
    assert rows[1]["question"] == "outra"


async def test_delete_e_uma_transacao_e_nao_mexe_na_auditoria(
    db_conn: asyncpg.Connection, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    uid, _ = await _scenario(db_conn, clock)

    async def boom(*_a: object, **_k: object) -> int:
        raise RuntimeError("falha na auditoria")

    monkeypatch.setattr(lgpd.audit, "write", boom)
    # users.revoke usa o mesmo modulo audit: a falha acontece depois de escrever o cadastro
    with pytest.raises(RuntimeError):
        await lgpd.delete_person(db_conn, uid, "admin", clock=clock)
    user = await db_conn.fetchrow("SELECT name, status FROM access.users WHERE id = $1", uid)
    assert user["name"] == NAME and user["status"] == "active"
    assert await db_conn.fetchval(
        "SELECT count(*) FROM chat.message_trace WHERE body IS NOT NULL"
    ) == 4  # fmt: skip
    assert await db_conn.fetchval("SELECT count(*) FROM access.unknown_first_messages") == 1


async def test_delete_duas_vezes_e_inexistente(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    uid, _ = await _scenario(db_conn, clock)
    await lgpd.delete_person(db_conn, uid, "admin", clock=clock)
    with pytest.raises(lgpd.PersonAlreadyDeleted):
        await lgpd.delete_person(db_conn, uid, "admin", clock=clock)
    with pytest.raises(lgpd.PersonAlreadyDeleted):
        await lgpd.export_person(db_conn, uid, "admin", clock=clock)
    with pytest.raises(lgpd.PersonNotFound):
        await lgpd.delete_person(db_conn, 999999, "admin", clock=clock)
    deletes = await db_conn.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'lgpd.delete'"
    )
    assert deletes == 1
