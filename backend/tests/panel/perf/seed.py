"""Semente sintetica de desempenho do painel (PN-T64). So para o banco de TESTE.

2.000 usuarios, 20 mil mensagens por dia por 30 dias (600 mil linhas em
`chat.message_trace`), 200 mil tickets e uma FAQ grande. Carga em massa por COPY
(`copy_records_to_table`). Dados falsos, sem PII real. A limpeza e o TRUNCATE de
`tests.db_support._truncate_all`, feita pelo teste.
"""
from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta

import asyncpg

from tests.db_support import assert_test_database
from tests.panel.factories import normalize_question

USERS = 2_000
MESSAGES_PER_DAY = 20_000
DAYS = 30
TICKETS = 200_000
FAQ_ITEMS = 2_000

_BRANDS = ("plant_power", "a_tal_da_castanha")
_CLASSES = ("PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA")
_STATUSES = ("answered_bot", "answered_bot", "answered_human", "awaiting", "send_error")
_FAQ_BRANDS = ("todas", *_BRANDS)


def _users(now: datetime) -> Iterator[tuple[object, ...]]:
    for i in range(1, USERS + 1):
        key = f"5511{i:09d}"
        brands = [_BRANDS[i % 2]] if i % 3 else list(_BRANDS)
        yield (
            f"Usuario Perf {i:04d}", f"+{key}", key, "vendedor", f"Empresa {i % 50}",
            ["SP"], ["farma"], brands, "Gestora Teste", "active", now, now, "v1",
            now, "perf", now,
        )


def _messages(now: datetime) -> Iterator[tuple[object, ...]]:
    step = timedelta(days=DAYS) / (MESSAGES_PER_DAY * DAYS)
    start = now - timedelta(days=DAYS)
    for i in range(MESSAGES_PER_DAY * DAYS):
        at = start + step * i
        yield (f"wamid.perf.{i}", at, "text", i % USERS + 1, "allowed", "answered",
               f"mensagem de teste {i}", "bot", at + timedelta(seconds=2), 2000)


def _tickets(now: datetime) -> Iterator[tuple[object, ...]]:
    span = timedelta(days=DAYS) / TICKETS
    start = now - timedelta(days=DAYS)
    for i in range(TICKETS):
        at = start + span * i
        status = _STATUSES[i % len(_STATUSES)]
        done = status in ("answered_bot", "answered_human")
        esc = status == "awaiting" and i % 4 == 0
        text = f"Pergunta de teste {i}"
        yield (f"J-{i + 1:06d}", i % USERS + 1, "question", _BRANDS[i % 2],
               _CLASSES[i % len(_CLASSES)], text, text, esc, status,
               "urgente" if esc else None,
               {"answered_bot": "bot", "answered_human": "Admin Teste"}.get(status),
               at + timedelta(minutes=5) if done else None, at, at)


def _faq(now: datetime) -> Iterator[tuple[object, ...]]:
    for i in range(FAQ_ITEMS):
        q = f"Pergunta de FAQ numero {i}?"
        yield (_FAQ_BRANDS[i % 3], _CLASSES[i % 5], q, normalize_question(q),
               f"Resposta {i}.", [f"variacao {i}"], True, True, "seed", 1, now, "perf", now, "perf")


async def seed_perf(dsn: str, now: datetime) -> None:
    """Carrega a semente inteira no banco de teste (recusa banco que nao termine em `_test`)."""
    assert_test_database(dsn)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.copy_records_to_table(
            "users", schema_name="access", records=_users(now), columns=[
                "name", "phone_e164", "phone_key", "profile", "company", "ufs", "channels",
                "brands", "manager_name", "status", "status_changed_at", "accepted_at",
                "accepted_notice_version", "created_at", "created_by", "updated_at"])
        await conn.copy_records_to_table(
            "message_trace", schema_name="chat", records=_messages(now), columns=[
                "wa_message_id", "received_at", "msg_type", "user_id", "access_decision",
                "decision", "body", "reply_kind", "reply_sent_at", "latency_ms"])
        await conn.copy_records_to_table(
            "tickets", schema_name="chat", records=_tickets(now), columns=[
                "ticket_number", "user_id", "kind", "brand", "classification", "message",
                "summary", "escalated", "status", "urgency", "answered_by", "answered_at",
                "created_at", "updated_at"])
        await conn.copy_records_to_table(
            "faq_items", schema_name="content", records=_faq(now), columns=[
                "brand", "category", "question", "question_key", "answer", "variations",
                "bot_responde", "active", "origin", "version", "created_at", "created_by",
                "updated_at", "updated_by"])
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
