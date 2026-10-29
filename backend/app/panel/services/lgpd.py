"""LGPD: exportar e excluir (anonimizar) os dados de uma pessoa (PN-56, PN-57, PN-55).

*Exportar* devolve so dados da propria pessoa (cadastro, aceites, mensagens dentro da
retencao, respostas, tickets, tentativas) e grava ``lgpd.export`` sem PII.

*Excluir* (Q7: anonimizar) roda em UMA transacao: revoga pelo servico de PF, anonimiza o
cadastro (``anon:<id>``; a busca deixa de achar a pessoa porque ``search_key`` e gerada
do nome anonimizado), apaga os corpos de mensagem, resumos e a primeira mensagem de
desconhecido, anonimiza as tentativas e grava ``lgpd.delete`` com contagens.
``ops.audit_log`` e imutavel (trigger): este modulo so INSERE nela, nunca altera.

Recebe uma conexao ja adquirida (nunca pede outra ao pool). Mensagens de erro e logs
nao carregam PII nem texto de mensagem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from app.core.clock import Clock, SystemClock
from app.core.errors import JarvisError
from app.services import audit, users

__all__ = [
    "ACTION_DELETE",
    "ACTION_EXPORT",
    "REMOVED_TEXT",
    "DeleteResult",
    "PersonAlreadyDeleted",
    "PersonNotFound",
    "anonymous_label",
    "delete_person",
    "export_filename",
    "export_person",
    "export_to_json",
]

logger = logging.getLogger("jarvis.panel.lgpd")

ACTOR_TYPE = "panel"
TARGET_TYPE = "user"
ACTION_EXPORT = "lgpd.export"
ACTION_DELETE = "lgpd.delete"
REVOKE_REASON = "other"
REMOVED_TEXT = "[removido a pedido do titular]"
DEFAULT_RETENTION_MONTHS = 12
_MIN_NAME_LENGTH = 3
_ANON_PREFIX = "anon:"

_LOCK_USER = (
    "SELECT id, name, phone_key, bsuid, status FROM access.users WHERE id = $1 FOR UPDATE"
)
_USER_EXPORT = (
    "SELECT id, name, phone_e164, profile, company, ufs, channels, brands, manager_name, "
    "valid_until, status, status_changed_at, accepted_at, accepted_notice_version, "
    "declined_at, revoked_at, revoked_by, revoked_reason, proactive_opt_out, "
    "proactive_opt_out_at, created_at, created_by, updated_at "
    "FROM access.users WHERE id = $1"
)
_OPTINS = (
    "SELECT id, status, notice_version, issued_at, responded_at FROM access.optin_requests "
    "WHERE user_id = $1 ORDER BY issued_at, id"
)
_MESSAGES = (
    "SELECT received_at, msg_type, body, decision, reply_kind, reply_sent_at, ticket_number "
    "FROM chat.message_trace WHERE (user_id = $1 OR attempt_id = ANY($2::bigint[])) "
    "AND received_at >= $3 ORDER BY received_at, id"
)
_REPLIES = (
    "SELECT created_at, sent_at, kind, status, body FROM chat.outbound_messages "
    "WHERE user_id = $1 AND created_at >= $2 ORDER BY created_at, id"
)
_TICKETS = (
    "SELECT ticket_number, kind, brand, classification, message, summary, status, "
    "answered_by, answered_at, created_at FROM chat.tickets "
    "WHERE user_id = $1 AND created_at >= $2 ORDER BY created_at, id"
)
_ATTEMPTS_OF_USER = (
    "SELECT id FROM access.access_attempts WHERE user_id = $1 "
    "OR (phone_key IS NOT NULL AND phone_key = $2) "
    "OR (bsuid IS NOT NULL AND bsuid = $3) ORDER BY id"
)
_ATTEMPTS_EXPORT = (
    "SELECT reason, attempt_count, first_attempt_at, last_attempt_at, first_msg_type, "
    "dismissed_at FROM access.access_attempts WHERE id = ANY($1::bigint[]) ORDER BY id"
)


class PersonNotFound(JarvisError):
    """Pessoa inexistente."""

    def __init__(self, user_id: int) -> None:
        super().__init__(f"pessoa {user_id} nao encontrada")
        self.user_id = user_id


class PersonAlreadyDeleted(JarvisError):
    """A pessoa ja foi anonimizada: nao ha mais o que exportar ou excluir."""

    def __init__(self, user_id: int) -> None:
        super().__init__(f"pessoa {user_id} ja foi excluida")
        self.user_id = user_id


@dataclass(frozen=True, slots=True)
class DeleteResult:
    """Contagens do que a exclusao tocou (as mesmas vao para a auditoria)."""

    user_id: int
    counts: dict[str, int]


def anonymous_label(user_id: int) -> str:
    """Valor que substitui nome, telefone e bsuid da pessoa excluida."""
    return f"{_ANON_PREFIX}{user_id}"


def _is_anonymized(name: str) -> bool:
    return name.startswith(_ANON_PREFIX)


def _floor(now: datetime, months: int) -> datetime:
    return now - timedelta(days=round(months * 30.4375))


def _plain(record: asyncpg.Record) -> dict[str, Any]:
    """Linha vira dict JSON-ready (datas em ISO-8601)."""
    out: dict[str, Any] = {}
    for key, value in dict(record).items():
        out[key] = value.isoformat() if isinstance(value, datetime) else value
    return out


def _count(status: str) -> int:
    """Le o contador de 'UPDATE n' / 'DELETE n' do asyncpg."""
    return int(status.rsplit(" ", 1)[-1])


async def _attempt_ids(conn: asyncpg.Connection, row: asyncpg.Record) -> list[int]:
    rows = await conn.fetch(_ATTEMPTS_OF_USER, row["id"], row["phone_key"], row["bsuid"])
    return [int(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# Exportar (PN-56)
# ---------------------------------------------------------------------------


async def export_person(
    conn: asyncpg.Connection,
    user_id: int,
    actor: str,
    *,
    clock: Clock | None = None,
    retention_months: int = DEFAULT_RETENTION_MONTHS,
) -> dict[str, Any]:
    """Dados da pessoa (e so dela) em um dict JSON-ready; audita ``lgpd.export`` sem PII."""
    clock = clock or SystemClock()
    now = clock.now()
    floor = _floor(now, retention_months)
    async with conn.transaction():
        locked = await conn.fetchrow(_LOCK_USER, user_id)
        if locked is None:
            raise PersonNotFound(user_id)
        if _is_anonymized(locked["name"]):
            raise PersonAlreadyDeleted(user_id)
        attempt_ids = await _attempt_ids(conn, locked)
        data: dict[str, Any] = {
            "generated_at": now.isoformat(),
            "person": _plain(await conn.fetchrow(_USER_EXPORT, user_id)),
            "acceptances": [_plain(r) for r in await conn.fetch(_OPTINS, user_id)],
            "messages": [
                _plain(r) for r in await conn.fetch(_MESSAGES, user_id, attempt_ids, floor)
            ],
            "replies": [_plain(r) for r in await conn.fetch(_REPLIES, user_id, floor)],
            "tickets": [_plain(r) for r in await conn.fetch(_TICKETS, user_id, floor)],
            "access_attempts": [_plain(r) for r in await conn.fetch(_ATTEMPTS_EXPORT, attempt_ids)],
        }
        counts = {k: len(v) for k, v in data.items() if isinstance(v, list)}
        await audit.write(
            conn, ACTOR_TYPE, actor, ACTION_EXPORT, TARGET_TYPE, str(user_id),
            old=None, new={"counts": counts}, clock=clock,
        )  # fmt: skip
    logger.info("lgpd_export", extra={"user_id": user_id, "counts": counts})
    return data


def export_to_json(data: dict[str, Any]) -> bytes:
    """Serializa a exportacao como JSON UTF-8 legivel."""
    return json.dumps(data, ensure_ascii=False, indent=2, default=str).encode("utf-8")


def export_filename(user_id: int, now: datetime) -> str:
    """Nome do anexo, sem nome nem telefone da pessoa."""
    return f"dados-pessoa-{user_id}-{now.astimezone(UTC):%Y%m%d}.json"


# ---------------------------------------------------------------------------
# Excluir = anonimizar (PN-57, Q7)
# ---------------------------------------------------------------------------


async def _anonymize_user(conn: asyncpg.Connection, user_id: int, now: datetime) -> None:
    label = anonymous_label(user_id)
    await conn.execute(
        "UPDATE access.users SET name = $2, phone_e164 = $2, phone_key = $2, bsuid = $2, "
        "company = $3, manager_name = $3, updated_at = $4 WHERE id = $1",
        user_id, label, "removido", now,
    )  # fmt: skip


async def _clear_content(
    conn: asyncpg.Connection, user_id: int, attempt_ids: list[int]
) -> dict[str, int]:
    """Apaga corpos, resumos e a primeira mensagem de desconhecido."""
    counts: dict[str, int] = {}
    counts["message_trace_bodies"] = _count(
        await conn.execute(
            "UPDATE chat.message_trace SET body = NULL WHERE body IS NOT NULL "
            "AND (user_id = $1 OR attempt_id = ANY($2::bigint[]))",
            user_id, attempt_ids,
        )  # fmt: skip
    )
    # to_phone e NOT NULL no esquema de PF: recebe o marcador anonimo em vez de NULL.
    counts["outbound_messages"] = _count(
        await conn.execute(
            "UPDATE chat.outbound_messages SET to_phone = $2, body = NULL, template = NULL "
            "WHERE user_id = $1",
            user_id, anonymous_label(user_id),
        )  # fmt: skip
    )
    counts["tickets"] = _count(
        await conn.execute(
            "UPDATE chat.tickets SET message = NULL, summary = NULL WHERE user_id = $1",
            user_id,
        )  # fmt: skip
    )
    counts["unknown_first_messages"] = _count(
        await conn.execute(
            "DELETE FROM access.unknown_first_messages WHERE attempt_id = ANY($1::bigint[])",
            attempt_ids,
        )  # fmt: skip
    )
    counts["access_attempts"] = _count(
        await conn.execute(
            "UPDATE access.access_attempts SET phone_key = 'anon:a' || id::text, bsuid = NULL "
            "WHERE id = ANY($1::bigint[])",
            attempt_ids,
        )  # fmt: skip
    )
    return counts


async def _scrub_assistant(conn: asyncpg.Connection, name: str, phone_key: str) -> int:
    """Melhor esforco: perguntas/respostas do assistente que citam o nome ou o telefone."""
    needles = [n.lower() for n in (name, phone_key) if len(n) >= _MIN_NAME_LENGTH]
    if not needles:
        return 0
    return _count(
        await conn.execute(
            "UPDATE ops.assistant_questions SET question = $2, "
            "answer = CASE WHEN answer IS NULL THEN NULL ELSE $2 END "
            "WHERE EXISTS (SELECT 1 FROM unnest($1::text[]) AS n(v) "
            "WHERE strpos(lower(question), n.v) > 0 "
            "OR strpos(lower(coalesce(answer, '')), n.v) > 0)",
            needles, REMOVED_TEXT,
        )  # fmt: skip
    )


async def delete_person(
    conn: asyncpg.Connection,
    user_id: int,
    actor: str,
    *,
    clock: Clock | None = None,
) -> DeleteResult:
    """Anonimiza a pessoa numa unica transacao. Irreversivel; audita ``lgpd.delete``."""
    clock = clock or SystemClock()
    now = clock.now()
    async with conn.transaction():
        locked = await conn.fetchrow(_LOCK_USER, user_id)
        if locked is None:
            raise PersonNotFound(user_id)
        if _is_anonymized(locked["name"]):
            raise PersonAlreadyDeleted(user_id)
        await users.revoke(conn, user_id, REVOKE_REASON, actor, actor_type=ACTOR_TYPE, clock=clock)
        attempt_ids = await _attempt_ids(conn, locked)
        counts = await _clear_content(conn, user_id, attempt_ids)
        counts["assistant_questions"] = await _scrub_assistant(
            conn, locked["name"], locked["phone_key"]
        )
        await _anonymize_user(conn, user_id, now)
        await audit.write(
            conn, ACTOR_TYPE, actor, ACTION_DELETE, TARGET_TYPE, str(user_id),
            old=None, new={"counts": counts}, clock=clock,
        )  # fmt: skip
    logger.info("lgpd_delete", extra={"user_id": user_id, "counts": counts})
    return DeleteResult(user_id=user_id, counts=counts)
