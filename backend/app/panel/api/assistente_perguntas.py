"""Pagina Perguntas do assistente (spec painel.1; task PN-T58; PN-53, PN-43).

- ``GET /dashboard/assistente-perguntas``: o que a equipe perguntou ao assistente, com a resposta,
  mais os contadores de 30 dias (perguntas e pessoas distintas). So admin; vazio = zeros e texto
  de estado vazio. Uma conexao do pool por pedido; pergunta e resposta nunca vao a log.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from app.panel.assistant import log
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    templates_for,
)
from app.panel.services.formatting import format_short_datetime
from app.panel.services.scope import Scope

__all__ = ["PATH", "QuestionRow", "load_rows", "router"]

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/assistente-perguntas"
LIST_LIMIT = 100
ERROR_TEXT = "Não foi possível responder esta pergunta."


@dataclass(frozen=True, slots=True)
class QuestionRow:
    asked_by: str
    agent: str
    when: str
    question: str
    answer: str
    failed: bool
    error_code: str


def load_rows(records: list[log.QuestionRecord]) -> list[QuestionRow]:
    """Linhas da pagina; falha mostra o texto fixo e o codigo curto, nunca excecao."""
    return [
        QuestionRow(
            asked_by=r.asked_by,
            agent=r.agent,
            when=format_short_datetime(r.asked_at),
            question=r.question,
            answer=(r.answer or "") if r.status == "ok" else ERROR_TEXT,
            failed=r.status != "ok",
            error_code=r.error_code or "",
        )
        for r in records
    ]


@router.get(PATH, include_in_schema=False)
async def assistente_perguntas(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        stats = await log.stats_30d(conn, clock=get_clock(request))
        records = await log.list_recent(conn, limit=LIST_LIMIT)
    context = {
        "active": "perguntas",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "stats": stats,
        "rows": load_rows(records),
        "window_days": log.WINDOW_DAYS,
    }
    return templates_for(request).TemplateResponse(request, "assistente_perguntas.html", context)
