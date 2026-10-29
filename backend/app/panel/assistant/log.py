"""Registro de perguntas do assistente (PN-53; spec painel.1, 4.10).

Grava em ``ops.assistant_questions`` toda resposta entregue (``ok``) ou falha (``error``) e
le a lista da pagina Perguntas com os contadores de 30 dias. Quem pergunta e o admin
(e-mail do painel); nada de telefone ou corpo de mensagem de cliente entra aqui. Em erro
so se grava um codigo curto (``error_code``), nunca o texto da excecao. Nunca vai a log.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from app.core.clock import Clock, SystemClock

__all__ = [
    "WINDOW_DAYS",
    "QuestionRecord",
    "QuestionStats",
    "list_recent",
    "record_answer",
    "record_error",
    "stats_30d",
]

WINDOW_DAYS = 30
MAX_QUESTION_CHARS = 4000
_CODE_RE = re.compile(r"[^a-z0-9_]+")
_COLS = (
    "id, asked_at, asked_by, agent, question, answer, status, tools_used, "
    "tokens_in, tokens_out, latency_ms, error_code"
)


@dataclass(frozen=True, slots=True)
class QuestionRecord:
    id: int
    asked_at: datetime
    asked_by: str
    agent: str
    question: str
    answer: str | None
    status: str
    tools_used: tuple[str, ...]
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class QuestionStats:
    questions: int
    people: int


def _now(clock: Clock | None) -> datetime:
    return (clock or SystemClock()).now()


def _record(row: asyncpg.Record) -> QuestionRecord:
    data = dict(row)
    data["tools_used"] = tuple(data["tools_used"] or ())
    return QuestionRecord(**data)


def _clean_code(code: str) -> str:
    """Codigo curto e seguro (minusculas, digitos, ``_``); nunca texto livre de excecao."""
    return _CODE_RE.sub("_", code.strip().lower())[:40].strip("_") or "unknown"


async def _insert(
    conn: asyncpg.Connection,
    *,
    asked_by: str,
    agent: str,
    question: str,
    answer: str | None,
    status: str,
    tools_used: Sequence[str],
    tokens_in: int | None,
    tokens_out: int | None,
    latency_ms: int | None,
    error_code: str | None,
    clock: Clock | None,
) -> QuestionRecord:
    row = await conn.fetchrow(
        f"""INSERT INTO ops.assistant_questions
              (asked_at, asked_by, agent, question, answer, status, tools_used,
               tokens_in, tokens_out, latency_ms, error_code)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING {_COLS}""",  # noqa: S608 - colunas constantes
        _now(clock),
        asked_by,
        agent,
        question[:MAX_QUESTION_CHARS],
        answer,
        status,
        list(tools_used),
        tokens_in,
        tokens_out,
        latency_ms,
        error_code,
    )
    if row is None:  # pragma: no cover - INSERT ... RETURNING sempre devolve
        raise RuntimeError("falha ao registrar a pergunta")
    return _record(row)


async def record_answer(
    conn: asyncpg.Connection,
    *,
    asked_by: str,
    agent: str,
    question: str,
    answer: str,
    tools_used: Sequence[str] = (),
    tokens_in: int | None = None,
    tokens_out: int | None = None,
    latency_ms: int | None = None,
    clock: Clock | None = None,
) -> QuestionRecord:
    """Registra uma resposta entregue (``status='ok'``)."""
    return await _insert(
        conn,
        asked_by=asked_by,
        agent=agent,
        question=question,
        answer=answer,
        status="ok",
        tools_used=tools_used,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
        error_code=None,
        clock=clock,
    )


async def record_error(
    conn: asyncpg.Connection,
    *,
    asked_by: str,
    agent: str,
    question: str,
    error_code: str,
    tools_used: Sequence[str] = (),
    latency_ms: int | None = None,
    clock: Clock | None = None,
) -> QuestionRecord:
    """Registra uma falha (``status='error'``, sem resposta, so o codigo curto)."""
    return await _insert(
        conn,
        asked_by=asked_by,
        agent=agent,
        question=question,
        answer=None,
        status="error",
        tools_used=tools_used,
        tokens_in=None,
        tokens_out=None,
        latency_ms=latency_ms,
        error_code=_clean_code(error_code),
        clock=clock,
    )


async def list_recent(
    conn: asyncpg.Connection, *, limit: int = 50, offset: int = 0
) -> list[QuestionRecord]:
    """Mais recentes primeiro (``asked_at DESC``, usa ``assistant_q_idx``)."""
    rows = await conn.fetch(
        f"""SELECT {_COLS} FROM ops.assistant_questions
             ORDER BY asked_at DESC, id DESC LIMIT $1 OFFSET $2""",  # noqa: S608
        max(1, min(limit, 200)),
        max(0, offset),
    )
    return [_record(r) for r in rows]


async def stats_30d(conn: asyncpg.Connection, *, clock: Clock | None = None) -> QuestionStats:
    """Perguntas e pessoas distintas nos ultimos 30 dias (vazio = zeros)."""
    since = _now(clock) - timedelta(days=WINDOW_DAYS)
    row = await conn.fetchrow(
        """SELECT count(*)::int AS questions, count(DISTINCT asked_by)::int AS people
             FROM ops.assistant_questions WHERE asked_at >= $1""",
        since,
    )
    if row is None:  # pragma: no cover - agregado sempre devolve uma linha
        return QuestionStats(0, 0)
    return QuestionStats(questions=row["questions"], people=row["people"])
