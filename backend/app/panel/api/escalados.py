"""Pagina Escalados: quadro por classificacao (spec painel.1; task PN-T23; PN-19..21, PN-63).

- ``GET  /dashboard/escalados`` (``midias=1`` mostra o quadro de midias; padrao = sem midia)
- ``POST /dashboard/escalados/{n}/mover``    ``destino=concluida`` (``move_to_concluded``)
- ``POST /dashboard/escalados/{n}/urgencia`` urgencia editavel no cartao (``set_urgency``)

Escalado NAO e alerta: esta tela so le ``ops.v_tickets`` e nunca chama ``raise_alert`` nem envia
e-mail. O botao "Midias (N)" respeita a marca em foco (midia sem marca aparece em todas).
A frase do resumo das 08:05 so aparece com a chave ``team_group_enabled`` ligada. Os POSTs
devolvem a pagina inteira (HTMX troca so ``#escalados-board`` via ``hx-select``) com
``HX-Trigger`` de toast. Nenhum texto de mensagem vai para log; uma conexao do pool por pedido.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.core.errors import ValidationFailure
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    get_settings,
    require_csrf,
    templates_for,
)
from app.panel.services import tickets_admin
from app.panel.services.formatting import format_time, to_sp
from app.panel.services.scope import MEDIA_KIND, Scope
from app.panel.values import URGENCIES, URGENCY_LABELS
from app.services.flags import Flags

__all__ = ["BoardColumn", "BoardCard", "COLUMNS", "load_board", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/escalados"
TICKETS_PATH = "/dashboard/tickets"
INBOX_PATH = "/dashboard/inbox"
BOARD_LIMIT = 500
TRUTHY = frozenset({"1", "true", "on", "sim"})
TICKET_NUMBER = re.compile(r"J-\d{4,}")
FALLBACK_COLUMN = "NAO_IDENTIFICADO"
DEST_CONCLUDED = "concluida"

# (classificacao, rotulo, fundo, bolinha, contador, texto da coluna vazia) - do prototipo.
COLUMNS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("PRECO", "Preço", "bg-amber-50/60", "bg-amber-500", "bg-amber-100 text-amber-700",
     "Nenhuma dúvida de preço na fila"),
    ("PRODUTO", "Produto", "bg-sky-50/60", "bg-sky-500", "bg-sky-100 text-sky-500",
     "Nenhuma dúvida de produto esperando"),
    ("ARGUMENTO", "Argumento", "bg-violet-50/60", "bg-violet-500", "bg-violet-100 text-violet-700",
     "Nenhum pedido de argumento"),
    ("MATERIAL", "Material", "bg-teal-50/60", "bg-teal-500", "bg-teal-100 text-teal-700",
     "Nenhum pedido de material"),
    ("CAMPANHA", "Campanha", "bg-red-50/60", "bg-red-500", "bg-red-100 text-red-700",
     "Nenhuma dúvida de campanha"),
    (FALLBACK_COLUMN, "Sem classificação", "bg-ink-50/60", "bg-ink-400",
     "bg-ink-100 text-ink-700", "Nada sem classificação"),
)
_COLUMN_KEYS = frozenset(c[0] for c in COLUMNS)

_BOARD_SQL = """
SELECT t.ticket_number, t.kind, t.classification, t.urgency, t.message, t.summary, t.brand,
       t.created_at, t.user_id, u.name AS user_name
  FROM ops.v_tickets t
  JOIN access.users u ON u.id = t.user_id
 WHERE t.status = 'awaiting' AND t.escalated AND {scope}
 ORDER BY CASE t.urgency WHEN 'muito_urgente' THEN 0 WHEN 'urgente' THEN 1 ELSE 2 END,
          t.created_at, t.id
 LIMIT {limit}
"""
_TICKET_ID = "SELECT id FROM chat.tickets WHERE ticket_number = $1"


@dataclass(frozen=True, slots=True)
class BoardCard:
    number: str
    is_media: bool
    classification: str
    urgency: str
    message: str
    summary: str
    brand: str | None
    created_at: datetime
    user_id: int
    user_name: str


@dataclass(frozen=True, slots=True)
class BoardColumn:
    key: str
    label: str
    bg: str
    dot: str
    count_cls: str
    empty: str
    cards: list[BoardCard]


@dataclass(frozen=True, slots=True)
class Board:
    columns: list[BoardColumn]
    media_count: int
    media_view: bool
    total: int


def _column_key(classification: str) -> str:
    """Classificacao sem coluna propria (ex.: MIDIA) cai em Sem classificacao: nada se perde."""
    return classification if classification in _COLUMN_KEYS else FALLBACK_COLUMN


def _card(r: asyncpg.Record) -> BoardCard:
    return BoardCard(
        number=r["ticket_number"],
        is_media=r["kind"] == MEDIA_KIND,
        classification=r["classification"],
        urgency=r["urgency"] or "pouco_urgente",
        message=r["message"] or "",
        summary=r["summary"] or "",
        brand=r["brand"],
        created_at=r["created_at"],
        user_id=int(r["user_id"]),
        user_name=r["user_name"],
    )


def build_board(cards: list[BoardCard], *, media_view: bool) -> Board:
    """Agrupa por classificacao; ``Midias (N)`` conta as de midia, o quadro mostra um lado."""
    media_count = sum(1 for c in cards if c.is_media)
    shown = [c for c in cards if c.is_media == media_view]
    columns = [
        BoardColumn(
            key, label, bg, dot, count_cls, empty,
            [c for c in shown if _column_key(c.classification) == key],
        )
        for key, label, bg, dot, count_cls, empty in COLUMNS
    ]
    return Board(columns, media_count, media_view, len(shown))


async def load_board(conn: asyncpg.Connection, scope: Scope, *, media_view: bool) -> Board:
    """Escalados aguardando, na marca em foco (consulta unica; ordem urgencia > mais antigo)."""
    params: list[object] = []
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    sql = _BOARD_SQL.format(scope=scope_sql, limit=BOARD_LIMIT)
    records = await conn.fetch(sql, *params)
    return build_board([_card(r) for r in records], media_view=media_view)


async def _team_group_on(request: Request, conn: asyncpg.Connection) -> bool:
    state = request.app.state
    flags: Flags | None = getattr(state, "flags", None)
    if flags is None:
        flags = Flags(state.pool, team_group_initial=get_settings(request).team_group_enabled)
    return await flags.team_group_enabled(conn=conn)


async def _render(
    request: Request,
    admin: PanelSession,
    scope: Scope,
    conn: asyncpg.Connection,
    *,
    media_view: bool,
    trigger: dict[str, Any] | None = None,
) -> Response:
    board = await load_board(conn, scope, media_view=media_view)
    context = {
        "active": "escalados",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "board": board,
        "scope_all": scope.is_all,
        "team_group_on": await _team_group_on(request, conn),
        "urgency_options": [(u, URGENCY_LABELS[u]) for u in URGENCIES],
        "board_path": PATH,
        "tickets_path": TICKETS_PATH,
        "inbox_path": INBOX_PATH,
    }
    response = templates_for(request).TemplateResponse(request, "escalados.html", context)
    if trigger:
        response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def _toast(message: str, kind: str) -> dict[str, Any]:
    return {"toast": {"message": message, "kind": kind}}


def _wants_media(raw: str | None) -> bool:
    return (raw or "").strip().lower() in TRUTHY


async def _ticket_id(conn: asyncpg.Connection, number: str) -> int | None:
    if not TICKET_NUMBER.fullmatch(number):
        return None
    found = await conn.fetchval(_TICKET_ID, number)
    return int(found) if found is not None else None


def _concluded_text(number: str, result: tickets_admin.ConcludeResult, now: datetime) -> str:
    if result.concluded:
        return f"Ticket {number} concluído. Nenhuma mensagem foi enviada."
    if result.status == "answered_human" and result.at is not None:
        same_day = to_sp(result.at).date() == to_sp(now).date()
        when = format_time(result.at) if same_day else to_sp(result.at).strftime("%d/%m %H:%M")
        return f"Já concluído por {result.by} em {when}."
    return f"O ticket {number} não está aguardando atendimento."


@router.get(PATH, include_in_schema=False)
async def escalados(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    midias: str | None = None,
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        return await _render(request, admin, scope, conn, media_view=_wants_media(midias))


@router.post(
    PATH + "/{number}/mover", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def mover(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    destino: Annotated[str, Form()] = "",
    midias: Annotated[str, Form()] = "",
) -> Response:
    clock = get_clock(request)
    trigger: dict[str, Any] | None = None
    async with request.app.state.pool.acquire() as conn:
        if destino == DEST_CONCLUDED:
            ticket_id = await _ticket_id(conn, number)
            if ticket_id is None:
                trigger = _toast("Ticket não encontrado.", "erro")
            else:
                author = admin.name.strip() or admin.email
                result = await tickets_admin.move_to_concluded(
                    conn, ticket_id, author, actor=admin.email, clock=clock
                )
                if result.concluded:
                    logger.info("panel_escalado_concluded")
                trigger = _toast(
                    _concluded_text(number, result, clock.now()),
                    "ok" if result.concluded else "aviso",
                )
        return await _render(
            request, admin, scope, conn, media_view=_wants_media(midias), trigger=trigger
        )


@router.post(
    PATH + "/{number}/urgencia", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def urgencia(
    request: Request,
    number: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    urgency: Annotated[str, Form()] = "",
    midias: Annotated[str, Form()] = "",
) -> Response:
    trigger: dict[str, Any]
    async with request.app.state.pool.acquire() as conn:
        ticket_id = await _ticket_id(conn, number)
        if ticket_id is None:
            trigger = _toast("Ticket não encontrado.", "erro")
        else:
            try:
                old = await tickets_admin.set_urgency(
                    conn, ticket_id, urgency, actor=admin.email, clock=get_clock(request)
                )
            except ValidationFailure:
                trigger = _toast("Urgência: valor não aceito.", "erro")
            else:
                if old != urgency:
                    logger.info("panel_escalado_urgency_changed")
                trigger = _toast(
                    f"Urgência de {number} definida como {URGENCY_LABELS[urgency]}.", "ok"
                )
        return await _render(
            request, admin, scope, conn, media_view=_wants_media(midias), trigger=trigger
        )
