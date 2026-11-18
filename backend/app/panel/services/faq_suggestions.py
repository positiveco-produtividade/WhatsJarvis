"""Sugestoes de itens da FAQ a partir das respostas da equipe (retorno-humano.1; RH-T10).

As sugestoes nascem em ``app.services.human_reply`` (RH-16). Aqui ficam a leitura para
Relatorios (RH-17), a aprovacao (chamada na mesma transacao que cria o item da FAQ, RH-18) e o
descarte (RH-19), sempre com auditoria. A marca em foco filtra a lista; sugestao sem marca
aparece em todas. Nada de texto vai para log.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.clock import Clock, SystemClock
from app.panel.services.scope import Scope
from app.services import audit

__all__ = [
    "FILTER_ALL",
    "FILTER_PENDING",
    "LIST_LIMIT",
    "Suggestion",
    "approve",
    "count_pending",
    "discard",
    "get_suggestion",
    "list_suggestions",
    "parse_filter",
]

FILTER_PENDING = "pendentes"
FILTER_ALL = "todas"
LIST_LIMIT = 100
_ACTOR_TYPE = "panel"
_TARGET = "faq_suggestion"

_COLUMNS = (
    "id, ticket_number, brand, category, question, answer, status, author, decided_by, "
    "decided_at, faq_item_id, created_at"
)
_BRAND_FILTER = "($1::text IS NULL OR brand IS NULL OR brand = $1)"
_LIST_PENDING = (
    f"SELECT {_COLUMNS} FROM content.faq_suggestions WHERE status = 'pendente' "
    f"AND {_BRAND_FILTER} ORDER BY created_at DESC, id DESC LIMIT $2"
)
_LIST_ALL = (
    f"SELECT {_COLUMNS} FROM content.faq_suggestions WHERE {_BRAND_FILTER} "
    "ORDER BY created_at DESC, id DESC LIMIT $2"
)
_COUNT_PENDING = (
    f"SELECT count(*) FROM content.faq_suggestions WHERE status = 'pendente' AND {_BRAND_FILTER}"
)
_GET = f"SELECT {_COLUMNS} FROM content.faq_suggestions WHERE id = $1"
_DECIDE = (
    "UPDATE content.faq_suggestions SET status = $2, decided_by = $3, decided_at = $4, "
    "faq_item_id = $5, updated_at = $4 WHERE id = $1 AND status = 'pendente' "
    "RETURNING ticket_number"
)


@dataclass(frozen=True, slots=True)
class Suggestion:
    id: int
    ticket_number: str
    brand: str | None
    category: str | None
    question: str
    answer: str
    status: str
    author: str
    decided_by: str | None
    decided_at: datetime | None
    faq_item_id: int | None
    created_at: datetime

    @property
    def pending(self) -> bool:
        return self.status == "pendente"


def _suggestion(row: Any) -> Suggestion:
    return Suggestion(
        id=int(row["id"]),
        ticket_number=row["ticket_number"],
        brand=row["brand"],
        category=row["category"],
        question=row["question"] or "",
        answer=row["answer"] or "",
        status=row["status"],
        author=row["author"],
        decided_by=row["decided_by"],
        decided_at=row["decided_at"],
        faq_item_id=row["faq_item_id"],
        created_at=row["created_at"],
    )


def parse_filter(value: str | None) -> str:
    """``todas`` ou, para qualquer outro valor, ``pendentes``."""
    return FILTER_ALL if value == FILTER_ALL else FILTER_PENDING


async def list_suggestions(
    conn: Any, scope: Scope, *, status: str = FILTER_PENDING, limit: int = LIST_LIMIT
) -> list[Suggestion]:
    """Mais recentes primeiro (RH-17)."""
    query = _LIST_ALL if status == FILTER_ALL else _LIST_PENDING
    return [_suggestion(r) for r in await conn.fetch(query, scope.brand, limit)]


async def count_pending(conn: Any, scope: Scope) -> int:
    return int(await conn.fetchval(_COUNT_PENDING, scope.brand))


async def get_suggestion(conn: Any, suggestion_id: int) -> Suggestion | None:
    row = await conn.fetchrow(_GET, suggestion_id)
    return _suggestion(row) if row is not None else None


async def _decide(
    conn: Any,
    suggestion_id: int,
    status: str,
    *,
    actor: str,
    item_id: int | None,
    clock: Clock | None,
) -> bool:
    now = (clock or SystemClock()).now()
    async with conn.transaction():
        number = await conn.fetchval(_DECIDE, suggestion_id, status, actor, now, item_id)
        if number is None:
            return False
        await audit.write(
            conn,
            _ACTOR_TYPE,
            actor,
            "faq_suggestion.approve" if status == "aprovada" else "faq_suggestion.discard",
            _TARGET,
            str(suggestion_id),
            {"status": "pendente", "ticket": number},
            {"status": status, "faq_item_id": item_id},
            clock=clock,
        )
    return True


async def approve(
    conn: Any, suggestion_id: int, item_id: int, *, actor: str, clock: Clock | None = None
) -> bool:
    """Marca como aprovada ligada ao item criado (RH-18). False se ja nao estava pendente."""
    return await _decide(
        conn, suggestion_id, "aprovada", actor=actor, item_id=item_id, clock=clock
    )


async def discard(
    conn: Any, suggestion_id: int, *, actor: str, clock: Clock | None = None
) -> bool:
    """Descarta (RH-19). False se ja nao estava pendente."""
    return await _decide(
        conn, suggestion_id, "descartada", actor=actor, item_id=None, clock=clock
    )
