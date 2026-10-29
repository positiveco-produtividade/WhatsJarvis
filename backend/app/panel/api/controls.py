"""Controles do Jarvis: pausa e marcas (spec painel.1 4.9; task PN-T43; PN-45, PN-47, PN-48, PN-55).

- ``GET  /dashboard/control/painel``        fragmento com o botao de pausa, o aviso e a lista de
                                            marcas (Alertas recarrega em ``controlsChanged``)
- ``GET  /dashboard/control/pausa``         modal de confirmacao, aberto com o estado ATUAL
- ``POST /dashboard/control/pausa``         ``desired=pause|resume``
- ``GET  /dashboard/control/marca/{marca}`` modal de confirmacao da marca
- ``POST /dashboard/control/marca/{marca}`` ``desired=on|off``

Os POST pedem o estado desejado, nunca "alternar": dois POST iguais deixam o mesmo estado e so o
primeiro audita (``Flags.set_bool`` com ``actor_type="panel"``) e chama ``alerts.raise_alert``.
O alerta sai com ``email=False`` (acao do proprio Caio, spec R3/Q17); a entrega ao grupo segue a
chave da equipe. A leitura e a escrita ficam sob ``pg_advisory_xact_lock`` por chave, entao dois
POST simultaneos nao geram dois alertas. A pausa silencia so as respostas: mensagens de
consentimento seguem saindo (``FlagsReplyPolicy``). Sem nome, telefone ou e-mail em alerta ou log.

Sucesso: 200 sem corpo, ``HX-Reswap: none`` e ``HX-Trigger`` (``controlsChanged``, ``toast``,
``closeOverlays``), emitidos depois do COMMIT.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.panel.api.pessoas import _flags
from app.panel.assistant.agents import AGENTS, Agent
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.values import BRAND_LABELS
from app.services import alerts
from app.services.flags import PAUSED_KEY, Flags, brand_key, default_for

__all__ = ["BrandState", "ControlState", "load_state", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

BASE = "/dashboard/control"
PAUSA_TEMPLATE = "_control_pausa.html"
MARCAS_TEMPLATE = "_control_marcas.html"
ALERT_SEVERITY = "info"

_READ_SQL = "SELECT value::text FROM ops.system_state WHERE key = $1"
_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))"


@dataclass(frozen=True)
class BrandState:
    """Uma marca na lista: chave, nome, agente (rosto) e se o Jarvis responde nela."""

    key: str
    label: str
    agent: Agent
    enabled: bool


@dataclass(frozen=True)
class ControlState:
    paused: bool
    brands: tuple[BrandState, ...]

    @property
    def responding(self) -> int:
        return sum(1 for b in self.brands if b.enabled)


async def _read_flag(conn: Any, key: str) -> bool:
    """Valor gravado agora no banco (sem cache), ou o padrao da chave."""
    raw = await conn.fetchval(_READ_SQL, key)
    parsed = json.loads(raw) if raw is not None else None
    return parsed if isinstance(parsed, bool) else default_for(key)


async def load_state(conn: Any) -> ControlState:
    """Estado atual de pausa e marcas, lido do banco por ``conn``."""
    paused = await _read_flag(conn, PAUSED_KEY)
    brands: list[BrandState] = []
    for brand, label in BRAND_LABELS.items():
        enabled = await _read_flag(conn, brand_key(brand))
        brands.append(BrandState(brand, label, AGENTS[brand], enabled))
    return ControlState(paused=paused, brands=tuple(brands))


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _toast(message: str, kind: str = "ok") -> tuple[str, Any]:
    return ("toast", {"message": message, "kind": kind})


def _done(message: str, kind: str = "ok") -> Response:
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(
        ("controlsChanged", {}), _toast(message, kind), ("closeOverlays", {})
    )
    return response


def _render(request: Request, template: str, context: dict[str, Any]) -> str:
    return templates_for(request).get_template(template).render({"request": request, **context})


def _modal_context(request: Request, admin: PanelSession, slot: str) -> dict[str, Any]:
    return {"slot": slot, "csrf_token": csrf_token_for(request, admin)}


def _brand_or_404(brand: str) -> str:
    if brand not in BRAND_LABELS:
        raise HTTPException(status_code=404)
    return brand


async def _change_flag(
    request: Request,
    admin: PanelSession,
    key: str,
    target: bool,
    *,
    kind: str,
    message: str,
    payload: dict[str, Any],
) -> bool:
    """Leva a chave ao estado desejado. True se mudou (e entao auditou e alertou)."""
    flags: Flags = _flags(request)
    settings = getattr(request.app.state, "settings", None)
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(_LOCK_SQL, "panel_control:" + key)
            if await _read_flag(conn, key) == target:
                return False
            await flags.set_bool(conn, key, target, admin.email, actor_type="panel")
            await alerts.raise_alert(
                conn,
                kind,
                ALERT_SEVERITY,
                message,
                payload,
                email=False,
                flags=flags,
                settings=settings,
                clock=get_clock(request),
            )
    return True


@router.get(BASE + "/painel", include_in_schema=False)
async def painel(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        state = await load_state(conn)
    context = {"slot": "bar", "state": state}
    html = _render(request, PAUSA_TEMPLATE, context)
    html += _render(request, MARCAS_TEMPLATE, {**context, "slot": "list"})
    return HTMLResponse(html)


@router.get(BASE + "/pausa", include_in_schema=False)
async def pausa_form(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        paused = await _read_flag(conn, PAUSED_KEY)
    context = {
        **_modal_context(request, admin, "modal"),
        "paused": paused,
        "action": BASE + "/pausa",
    }
    return templates_for(request).TemplateResponse(request, PAUSA_TEMPLATE, context)


@router.post(BASE + "/pausa", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def pausa(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    desired: Annotated[str, Form()] = "",
) -> Response:
    if desired not in ("pause", "resume"):
        return _done("Escolha pausar ou retomar.", "erro")
    pausing = desired == "pause"
    changed = await _change_flag(
        request,
        admin,
        PAUSED_KEY,
        pausing,
        kind="pause" if pausing else "resume",
        message=(
            "Jarvis pausado manualmente pelo painel."
            if pausing
            else "Operação retomada pelo painel."
        ),
        payload={"origin": "panel"},
    )
    logger.info("control_changed", extra={"control": "pause", "value": pausing, "changed": changed})
    if not changed:
        return _done("Já estava assim: nada foi alterado.", "aviso")
    return _done("Jarvis pausado." if pausing else "Operação retomada.")


@router.get(BASE + "/marca/{brand}", include_in_schema=False)
async def marca_form(
    request: Request,
    brand: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    _brand_or_404(brand)
    async with request.app.state.pool.acquire() as conn:
        enabled = await _read_flag(conn, brand_key(brand))
    context = {
        **_modal_context(request, admin, "modal"),
        "brand": BrandState(brand, BRAND_LABELS[brand], AGENTS[brand], enabled),
        "action": f"{BASE}/marca/{brand}",
    }
    return templates_for(request).TemplateResponse(request, MARCAS_TEMPLATE, context)


@router.post(BASE + "/marca/{brand}", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def marca(
    request: Request,
    brand: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    desired: Annotated[str, Form()] = "",
) -> Response:
    _brand_or_404(brand)
    if desired not in ("on", "off"):
        return _done("Escolha ligar ou desligar.", "erro")
    turning_on = desired == "on"
    label = BRAND_LABELS[brand]
    changed = await _change_flag(
        request,
        admin,
        brand_key(brand),
        turning_on,
        kind="brand_on" if turning_on else "brand_off",
        message=f"Jarvis {'ligado' if turning_on else 'desligado'} na marca {label} pelo painel.",
        payload={"brand": brand, "origin": "panel"},
    )
    logger.info(
        "control_changed",
        extra={"control": "brand", "brand": brand, "value": turning_on, "changed": changed},
    )
    if not changed:
        return _done("Já estava assim: nada foi alterado.", "aviso")
    return _done(f"Jarvis {'ligado' if turning_on else 'desligado'} na {label}.")
