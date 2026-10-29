"""Pagina Alertas (spec painel.1, 4.5 e 4.9; task PN-T42; PN-44, PN-43, PN-5).

- ``GET  /dashboard/alerts?filtro=todos|nao_vistos|criticos&abrir=<id>``
- ``POST /dashboard/alerts/{id}/ack``  (marcar como visto; auditado)

So aparecem alertas de sistema e seguranca (lista fechada ``ALERT_LABELS``). Escalacao,
midia e numero desconhecido nao sao alertas e nunca entram aqui, mesmo que exista uma linha
antiga com outro ``kind``. A marca em foco filtra por ``payload->>'brand'`` (sem marca no
payload = vale para todas). ``?abrir=<id>`` (link do e-mail) destaca o cartao, mesmo fora do
filtro ou do limite da lista. Os controles de pausa e marcas vem do fragmento
``GET /dashboard/control/painel`` (router de controles), carregado por HTMX e recarregado em
``controlsChanged``; esta pagina nao le o estado deles. Uma conexao do pool por pedido; nada de
texto de mensagem em log.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.services.formatting import format_short_datetime
from app.panel.services.scope import Scope
from app.services import audit

__all__ = ["ALERT_LABELS", "AlertCard", "load_alerts", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/alerts"
TICKETS_PATH = "/dashboard/tickets"
CONTROL_PATH = "/dashboard/control/painel"
LIST_LIMIT = 100
FILTERS: tuple[tuple[str, str], ...] = (
    ("todos", "Todos"),
    ("nao_vistos", "Não vistos"),
    ("criticos", "Críticos"),
)
FILTER_KEYS = frozenset(k for k, _ in FILTERS)
DEFAULT_FILTER = "todos"

# Tipos de sistema e seguranca (PN-44 + welcome_failed/identity_conflict, Q-P5), com o rotulo.
ALERT_LABELS: dict[str, str] = {
    "volume_spike": "Pico de volume",
    "error_rate": "Taxa de erro alta",
    "api_failure": "Falha de API",
    "send_failure": "Falha de API",
    "injection_attempt": "Tentativa de injection",
    "brain_budget": "Orçamento do cérebro",
    "brain_api_failure": "Falha da IA (Gemini)",
    "rate_limit": "Limite de 60 msg/h",
    "welcome_failed": "Falha ao enviar boas-vindas",
    "identity_conflict": "Conflito de identidade",
    "pause": "Pausa manual",
    "resume": "Retomado",
    "brand_off": "Jarvis desligado em uma marca",
    "brand_on": "Jarvis ligado em uma marca",
}
_KINDS = list(ALERT_LABELS)

# Cores de severidade do prototipo (SEV_COLOR).
SEVERITY_CLASSES: dict[str, str] = {
    "critical": "bg-red-100 text-red-800 ring-red-300",
    "high": "bg-orange-100 text-orange-800 ring-orange-300",
    "warn": "bg-amber-100 text-amber-800 ring-amber-300",
    "info": "bg-sky-100 text-sky-500 ring-sky-300",
}

_SELECT = """
SELECT a.id, a.severity, a.kind, a.message, a.payload, a.ticket_number, a.auto_action,
       a.acknowledged_at, a.created_at,
       (a.ticket_number IS NOT NULL AND EXISTS (
          SELECT 1 FROM chat.tickets t WHERE t.ticket_number = a.ticket_number)) AS has_ticket
  FROM ops.alerts a
 WHERE a.kind = ANY($1::text[]) AND {scope}{extra}
"""
_LIST_SQL = _SELECT + " ORDER BY a.created_at DESC, a.id DESC LIMIT " + str(LIST_LIMIT)
_ONE_SQL = _SELECT + " AND a.id = {id_param}"
_UNSEEN_SQL = (
    "SELECT count(*) FROM ops.alerts a "
    "WHERE a.kind = ANY($1::text[]) AND a.acknowledged_at IS NULL AND {scope}"
)
_ACK_SQL = (
    "UPDATE ops.alerts a SET acknowledged_at = $2, acknowledged_by = $3 "
    "WHERE a.id = $4 AND a.acknowledged_at IS NULL AND a.kind = ANY($1::text[]) AND {scope} "
    "RETURNING a.id"
)
_VISIBLE_SQL = "SELECT 1 FROM ops.alerts a WHERE a.id = $2 AND a.kind = ANY($1::text[]) AND {scope}"
_EXTRA = {
    "todos": "",
    "nao_vistos": " AND a.acknowledged_at IS NULL",
    "criticos": " AND a.severity = 'critical'",
}


@dataclass(frozen=True, slots=True)
class AlertCard:
    id: int
    severity: str
    severity_label: str
    severity_cls: str
    kind: str
    label: str
    message: str
    details: str
    ticket_number: str | None
    auto_action: str | None
    seen: bool
    when: str
    highlight: bool


@dataclass(frozen=True, slots=True)
class AlertsView:
    cards: list[AlertCard]
    unseen: int
    filtro: str


def parse_filter(raw: str | None) -> str:
    return raw if raw in FILTER_KEYS else DEFAULT_FILTER


def parse_open_id(raw: str | None) -> int | None:
    """``abrir`` so vale se for inteiro positivo que cabe em bigint."""
    text = (raw or "").strip()
    if not text.isdigit() or len(text) > 18:
        return None
    return int(text) or None


def _details(payload: object) -> str:
    data = json.loads(payload) if isinstance(payload, str | bytes) else payload
    if not data:
        return ""
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, default=str)


def _card(r: asyncpg.Record, highlight_id: int | None) -> AlertCard:
    severity = r["severity"]
    created: datetime = r["created_at"]
    return AlertCard(
        id=int(r["id"]),
        severity=severity,
        severity_label=str(severity).upper(),
        severity_cls=SEVERITY_CLASSES.get(severity, SEVERITY_CLASSES["info"]),
        kind=r["kind"],
        label=ALERT_LABELS.get(r["kind"], r["kind"]),
        message=r["message"],
        details=_details(r["payload"]),
        ticket_number=r["ticket_number"] if r["has_ticket"] else None,
        auto_action=r["auto_action"],
        seen=r["acknowledged_at"] is not None,
        when=format_short_datetime(created),
        highlight=highlight_id is not None and int(r["id"]) == highlight_id,
    )


async def load_alerts(
    conn: asyncpg.Connection, scope: Scope, *, filtro: str, open_id: int | None = None
) -> AlertsView:
    """Lista (mais novos primeiro, ate 100) na marca em foco; ``open_id`` entra se faltar."""
    params: list[object] = [_KINDS]
    scope_sql = scope.sql_alert("a.payload", params)
    rows = await conn.fetch(_LIST_SQL.format(scope=scope_sql, extra=_EXTRA[filtro]), *params)
    cards = [_card(r, open_id) for r in rows]
    if open_id is not None and not any(c.highlight for c in cards):
        extra_params: list[object] = [_KINDS]
        one_scope = scope.sql_alert("a.payload", extra_params)
        extra_params.append(open_id)
        one = await conn.fetchrow(
            _ONE_SQL.format(scope=one_scope, extra="", id_param=f"${len(extra_params)}"),
            *extra_params,
        )
        if one is not None:
            cards.insert(0, _card(one, open_id))
    count_params: list[object] = [_KINDS]
    count_scope = scope.sql_alert("a.payload", count_params)
    unseen = await conn.fetchval(_UNSEEN_SQL.format(scope=count_scope), *count_params)
    return AlertsView(cards, int(unseen or 0), filtro)


async def _render(
    request: Request,
    admin: PanelSession,
    scope: Scope,
    conn: asyncpg.Connection,
    *,
    filtro: str,
    open_id: int | None = None,
    trigger: dict[str, Any] | None = None,
) -> Response:
    view = await load_alerts(conn, scope, filtro=filtro, open_id=open_id)
    context = {
        "active": "alerts",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "alerts": view.cards,
        "unseen": view.unseen,
        "filtro": view.filtro,
        "filters": FILTERS,
        "alerts_path": PATH,
        "tickets_path": TICKETS_PATH,
        "control_path": CONTROL_PATH,
    }
    response = templates_for(request).TemplateResponse(request, "alerts.html", context)
    if trigger:
        response.headers["HX-Trigger"] = json.dumps(trigger)
    return response


def _toast(message: str, kind: str) -> dict[str, Any]:
    return {"toast": {"message": message, "kind": kind}}


@router.get(PATH, include_in_schema=False)
async def alerts(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    filtro: str | None = None,
    abrir: str | None = None,
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        return await _render(
            request,
            admin,
            scope,
            conn,
            filtro=parse_filter(filtro),
            open_id=parse_open_id(abrir),
        )


async def _ack(
    conn: asyncpg.Connection, scope: Scope, alert_id: int, actor: str, now: datetime,
    request: Request,
) -> str:
    """Marca como visto na transacao com a auditoria. Devolve ``ok|already|missing``."""
    async with conn.transaction():
        params: list[object] = [_KINDS, now, actor, alert_id]
        ack_scope = scope.sql_alert("a.payload", params)
        done = await conn.fetchval(_ACK_SQL.format(scope=ack_scope), *params)
        if done is not None:
            await audit.write(
                conn, "panel", actor, "alert.ack", "alert", str(alert_id),
                {"acknowledged": False}, {"acknowledged": True}, clock=get_clock(request),
            )
            return "ok"
        see_params: list[object] = [_KINDS, alert_id]
        see_scope = scope.sql_alert("a.payload", see_params)
        exists = await conn.fetchval(_VISIBLE_SQL.format(scope=see_scope), *see_params)
    return "already" if exists is not None else "missing"


_ACK_TOASTS = {
    "ok": ("Alerta marcado como visto.", "ok"),
    "already": ("Este alerta já estava visto.", "aviso"),
    "missing": ("Alerta não encontrado.", "erro"),
}


@router.post(
    PATH + "/{alert_id}/ack", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def ack(
    request: Request,
    alert_id: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    filtro: Annotated[str, Form()] = DEFAULT_FILTER,
) -> Response:
    clock = get_clock(request)
    number = parse_open_id(alert_id)
    async with request.app.state.pool.acquire() as conn:
        outcome = (
            await _ack(conn, scope, number, admin.email, clock.now(), request)
            if number is not None
            else "missing"
        )
        if outcome == "ok":
            logger.info("panel_alert_acknowledged")
        message, kind = _ACK_TOASTS[outcome]
        return await _render(
            request, admin, scope, conn, filtro=parse_filter(filtro), trigger=_toast(message, kind)
        )
