"""Aba Retencao de Logs (spec painel.1, 4.8; task PN-T62; PN-58).

``GET /dashboard/logs/retencao`` devolve o fragmento ``_logs_retencao.html``, carregado por
HTMX dentro de ``/dashboard/logs``: volume por mes, o que expira nos proximos 30 dias, se a
rotina esta em simulacao (``RETENTION_DRY_RUN``) e a ultima execucao (contagens da auditoria
``retention.purge``). So administradores; somente leitura. Uma conexao do pool por pedido,
liberada antes de renderizar; nenhum dado pessoal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.config import get_settings
from app.panel.auth.sessions import PanelSession
from app.panel.deps import PanelRoute, current_admin, templates_for
from app.panel.services.retention import AUDIT_ACTION, read_volumes

__all__ = ["router"]

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/logs/retencao"
TEMPLATE = "_logs_retencao.html"

_LAST_RUN_SQL = (
    "SELECT at, new_value FROM ops.audit_log WHERE action = $1 ORDER BY at DESC, id DESC LIMIT 1"
)

# Rotulos das tabelas purgadas (so as conhecidas; o resto e ignorado).
COUNT_LABELS: dict[str, str] = {
    "tickets": "Tickets",
    "outbound_messages": "Respostas enviadas",
    "message_trace": "Mensagens",
    "unknown_first_messages": "Primeiras mensagens de desconhecidos",
    "assistant_questions": "Perguntas ao assistente",
    "import_batches": "Lotes de importação",
    "panel_sessions": "Sessões do painel",
}


@dataclass(frozen=True)
class LastRun:
    at: datetime
    dry_run: bool
    cutoff: str
    rows: tuple[tuple[str, int], ...]

    @property
    def total(self) -> int:
        return sum(n for _, n in self.rows)


def parse_last_run(at: datetime, raw: Any) -> LastRun:
    """Monta a ultima execucao a partir do jsonb da auditoria (so contagens e corte)."""
    try:
        data = json.loads(raw) if isinstance(raw, str | bytes) else raw
    except ValueError:
        data = None
    if not isinstance(data, dict):
        data = {}
    counts = data.get("counts")
    counts = counts if isinstance(counts, dict) else {}
    rows = tuple(
        (label, int(counts[key]))
        for key, label in COUNT_LABELS.items()
        if isinstance(counts.get(key), int)
    )
    cutoff = data.get("cutoff")
    return LastRun(
        at=at,
        dry_run=bool(data.get("dry_run")),
        cutoff=cutoff if isinstance(cutoff, str) else "",
        rows=rows,
    )


@router.get(PATH, include_in_schema=False)
async def retencao(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    async with request.app.state.pool.acquire() as conn:
        volumes = await read_volumes(conn, settings)
        last = await conn.fetchrow(_LAST_RUN_SQL, AUDIT_ACTION)
    context = {
        "retention_path": PATH,
        "volumes": volumes,
        "dry_run": bool(settings.retention_dry_run),
        "months_kept": max(1, int(settings.retention_months)),
        "last_run": parse_last_run(last["at"], last["new_value"]) if last else None,
    }
    response = templates_for(request).TemplateResponse(request, TEMPLATE, context)
    response.headers["Vary"] = "HX-Request"
    return response
