"""Pedindo acesso: ignorar e chave da primeira mensagem (spec painel.1 4.8; task PN-T36).

- ``POST /dashboard/pessoas/pedidos/{id}/ignorar``  ``attempts.dismiss`` (a tentativa volta se o
                                                    numero escrever de novo; a contagem fica)
- ``GET  /dashboard/pessoas/pedidos/exibicao``      modal de confirmacao (ligar ou desligar)
- ``POST /dashboard/pessoas/pedidos/exibicao``      ``Flags.set_bool(..., actor_type="panel")``
                                                    de ``unknown_first_text_display``, com
                                                    auditoria na mesma transacao

Toda rota exige ``current_admin`` (so administradores entram no painel); os POST exigem
``require_csrf``. Sucesso: 200 sem corpo, ``HX-Reswap: none`` e ``HX-Trigger``
(``pessoasChanged``, ``toast``, ``closeOverlays``) e so depois do COMMIT. Nao ha ``raise_alert``
nem e-mail por numero desconhecido. Nenhum telefone nem texto de mensagem vai para o log.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response

from app.panel.api.pessoas import _flags
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.services.people_read import UNKNOWN_FIRST_TEXT_KEY
from app.services import attempts

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

BASE = "/dashboard/pessoas/pedidos"
TEMPLATE = "_pessoas_pedidos.html"
GONE_TEXT = "Este pedido já foi tratado."


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _toast(message: str, kind: str = "ok") -> tuple[str, Any]:
    return ("toast", {"message": message, "kind": kind})


def _done(message: str, kind: str = "ok", *, close: bool = False) -> Response:
    events: list[tuple[str, Any]] = [("pessoasChanged", {}), _toast(message, kind)]
    if close:
        events.append(("closeOverlays", {}))
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(*events)
    return response


@router.post(
    BASE + "/{attempt_id:int}/ignorar",
    include_in_schema=False,
    dependencies=[Depends(require_csrf)],
)
async def ignorar(
    request: Request,
    attempt_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            done = await attempts.dismiss(conn, attempt_id, admin.email, clock=get_clock(request))
    if not done:
        return _done(GONE_TEXT, "aviso")
    logger.info("panel_attempt_dismissed", extra={"attempt_id": attempt_id})
    return _done("Pedido ignorado. Se o número escrever de novo, ele volta para a lista.")


@router.get(BASE + "/exibicao", include_in_schema=False)
async def exibicao_form(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        current = await _flags(request).get_bool(UNKNOWN_FIRST_TEXT_KEY, False, conn=conn)
    context = {
        "slot": "modal",
        "csrf_token": csrf_token_for(request, admin),
        "text_display": current,
        "action": BASE + "/exibicao",
    }
    return templates_for(request).TemplateResponse(request, TEMPLATE, context)


@router.post(BASE + "/exibicao", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def exibicao(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    value: Annotated[str, Form()] = "",
) -> Response:
    if value not in ("on", "off"):
        return _done("Escolha ligar ou desligar.", "erro", close=True)
    target = value == "on"
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction():
            await _flags(request).set_bool(
                conn, UNKNOWN_FIRST_TEXT_KEY, target, admin.email, actor_type="panel"
            )
    logger.info("panel_unknown_text_display_set", extra={"value": target})
    text = (
        "Primeira mensagem ligada: o texto aparece só nesta aba."
        if target
        else "Primeira mensagem desligada: só tipo, contagem e datas."
    )
    return _done(text, close=True)
