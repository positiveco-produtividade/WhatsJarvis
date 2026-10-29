"""Escrita de tickets do painel (PN-16, PN-17, PN-18, PN-21, PN-63, PN-55).

Unico caminho de escrita do painel em ``chat.tickets`` (a leitura e por ``ops.v_tickets``).
Concluir **nunca envia nada ao vendedor**: so grava o status ``answered_human`` com autor
e data. A concorrencia e resolvida no banco: ``UPDATE ... WHERE status = 'awaiting'``,
entao de dois ``concluir`` simultaneos so um altera a linha. Cada acao grava a auditoria
na mesma transacao da mudanca (falha na auditoria desfaz a acao).

As funcoes recebem uma conexao ja adquirida pelo chamador e abrem a propria transacao
(savepoint se o chamador ja tiver uma); nunca pedem uma segunda conexao ao pool.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app.core.clock import Clock, SystemClock
from app.core.errors import JarvisError, ValidationFailure
from app.panel.values import CLASSIFICATIONS, URGENCIES
from app.services import audit

__all__ = [
    "AUTHOR_MAX_LENGTH",
    "ConcludeResult",
    "TicketNotFound",
    "conclude",
    "move_to_concluded",
    "reclassify",
    "set_urgency",
]

AUTHOR_MAX_LENGTH = 80
ACTOR_TYPE = "panel"
TARGET_TYPE = "ticket"

_CONCLUDE = (
    "UPDATE chat.tickets SET status = 'answered_human', answered_by = $2, answered_at = $3, "
    "updated_at = $3 WHERE id = $1 AND status = 'awaiting' RETURNING ticket_number"
)
_CURRENT = (
    "SELECT ticket_number, status, answered_by, answered_at, classification, urgency "
    "FROM chat.tickets WHERE id = $1"
)
_SET_CLASSIFICATION = "UPDATE chat.tickets SET classification = $2, updated_at = $3 WHERE id = $1"
_SET_URGENCY = "UPDATE chat.tickets SET urgency = $2, updated_at = $3 WHERE id = $1"


class TicketNotFound(JarvisError):
    """Ticket inexistente."""

    def __init__(self, ticket_id: int) -> None:
        super().__init__(f"ticket {ticket_id} nao encontrado")
        self.ticket_id = ticket_id


@dataclass(frozen=True, slots=True)
class ConcludeResult:
    """Resultado de concluir. ``concluded=False`` = ja estava concluido (no-op com aviso).

    Nesse caso ``by``/``at`` dizem quem e quando ("Ja concluido por X em hh:mm") e
    ``status`` e o status atual do ticket.
    """

    concluded: bool
    ticket_number: str
    status: str
    by: str | None
    at: datetime | None


def _clean_author(author: str | None) -> str:
    name = " ".join((author or "").split())
    if not name:
        raise ValidationFailure("author", "informe quem concluiu")
    if len(name) > AUTHOR_MAX_LENGTH:
        raise ValidationFailure("author", f"maximo de {AUTHOR_MAX_LENGTH} caracteres")
    return name


def _clean_actor(actor: str | None) -> str:
    name = (actor or "").strip()
    if not name:
        raise ValidationFailure("actor", "autor da acao ausente")
    return name


async def _current(
    conn: asyncpg.Connection, ticket_id: int, *, lock: bool = False
) -> asyncpg.Record:
    row = await conn.fetchrow(_CURRENT + (" FOR UPDATE" if lock else ""), ticket_id)
    if row is None:
        raise TicketNotFound(ticket_id)
    return row


async def conclude(
    conn: asyncpg.Connection,
    ticket_id: int,
    author: str | None,
    *,
    actor: str,
    clock: Clock | None = None,
) -> ConcludeResult:
    """Conclui um ticket aguardando (PN-16/PN-17). Nada e enviado ao vendedor.

    ``author`` = "Quem concluiu" (obrigatorio); ``actor`` = admin autenticado, vai para
    a auditoria. Se outro ja concluiu (ou o ticket nao esta aguardando), nao altera nada
    e devolve ``concluded=False`` com o autor/hora existentes.
    """
    name = _clean_author(author)
    who = _clean_actor(actor)
    now = (clock or SystemClock()).now()
    async with conn.transaction():
        number = await conn.fetchval(_CONCLUDE, ticket_id, name, now)
        if number is None:
            row = await _current(conn, ticket_id)
            return ConcludeResult(
                False, row["ticket_number"], row["status"], row["answered_by"], row["answered_at"]
            )
        await audit.write(
            conn,
            ACTOR_TYPE,
            who,
            "ticket.conclude",
            TARGET_TYPE,
            number,
            {"status": "awaiting"},
            {"status": "answered_human", "answered_by": name},
            clock=clock,
        )
    return ConcludeResult(True, number, "answered_human", name, now)


async def move_to_concluded(
    conn: asyncpg.Connection,
    ticket_id: int,
    author: str | None,
    *,
    actor: str,
    clock: Clock | None = None,
) -> ConcludeResult:
    """Mover para Concluida do quadro Escalados (PN-21): mesmo efeito de ``conclude``.

    Ticket ja concluido = no-op com aviso (``concluded=False``).
    """
    return await conclude(conn, ticket_id, author, actor=actor, clock=clock)


async def reclassify(
    conn: asyncpg.Connection,
    ticket_id: int,
    classification: str,
    *,
    actor: str,
    clock: Clock | None = None,
) -> str:
    """Troca a classificacao (PN-18); devolve a antiga. O numero do ticket nao muda."""
    if classification not in CLASSIFICATIONS:
        raise ValidationFailure("classification", "valor nao aceito")
    who = _clean_actor(actor)
    now = (clock or SystemClock()).now()
    async with conn.transaction():
        row = await _current(conn, ticket_id, lock=True)
        old: str = row["classification"]
        if old == classification:
            raise ValidationFailure("classification", "escolha uma classificacao diferente")
        await conn.execute(_SET_CLASSIFICATION, ticket_id, classification, now)
        await audit.write(
            conn,
            ACTOR_TYPE,
            who,
            "ticket.reclassify",
            TARGET_TYPE,
            row["ticket_number"],
            {"classification": old},
            {"classification": classification},
            clock=clock,
        )
    return old


async def set_urgency(
    conn: asyncpg.Connection,
    ticket_id: int,
    urgency: str,
    *,
    actor: str,
    clock: Clock | None = None,
) -> str | None:
    """Troca a urgencia (PN-63); devolve a antiga. O ticket continua em Escalados."""
    if urgency not in URGENCIES:
        raise ValidationFailure("urgency", "valor nao aceito")
    who = _clean_actor(actor)
    now = (clock or SystemClock()).now()
    async with conn.transaction():
        row = await _current(conn, ticket_id, lock=True)
        old: str | None = row["urgency"]
        if old == urgency:
            return old
        await conn.execute(_SET_URGENCY, ticket_id, urgency, now)
        await audit.write(
            conn,
            ACTOR_TYPE,
            who,
            "ticket.urgency",
            TARGET_TYPE,
            row["ticket_number"],
            {"urgency": old},
            {"urgency": urgency},
            clock=clock,
        )
    return old
