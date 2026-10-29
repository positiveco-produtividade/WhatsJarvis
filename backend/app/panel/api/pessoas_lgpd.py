"""LGPD na ficha da pessoa: exportar e excluir (spec painel.1 4.8; task PN-T40; PN-56, PN-57).

- ``POST /dashboard/pessoas/{id}/exportar``  baixa o JSON da pessoa (``lgpd.export_person``);
                                             ``Cache-Control: no-store`` e nome de arquivo sem PII
- ``GET  /dashboard/pessoas/{id}/excluir``   modal que pede o nome (so mostra; nao escreve nada)
- ``POST /dashboard/pessoas/{id}/excluir``   ``lgpd.delete_person`` SO se o nome digitado for igual
                                             ao cadastrado (conferido aqui, no servidor)

Nenhum GET exporta ou exclui. Todo POST exige ``require_csrf``. Exportar e um formulario comum
(o navegador baixa o arquivo); excluir segue o padrao dos modais: sucesso = 200 sem corpo,
``HX-Reswap: none`` e ``HX-Trigger`` (``pessoasChanged``, ``toast``, ``closeOverlays``) so depois
do COMMIT. Nome, telefone e conteudo de mensagem nunca vao para o log.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    csrf_token_for,
    current_admin,
    get_clock,
    require_csrf,
    templates_for,
)
from app.panel.services import lgpd

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

ITEM_PATH = "/dashboard/pessoas/{user_id:int}"
TEMPLATE = "_pessoa_lgpd.html"
GONE_TEXT = "Esta pessoa não existe mais."
DELETED_TEXT = "Os dados desta pessoa já foram excluídos."
NAME_MISMATCH_TEXT = "O nome digitado não confere com o cadastro. Nada foi excluído."
DELETED_OK_TEXT = "Dados excluídos. A pessoa foi anonimizada e perdeu o acesso."
ADMIN_TEXT = (
    "Dados de administrador não podem ser excluídos pelo painel. "
    "Troque o perfil antes, se for o caso."
)
ADMIN_PROFILE = "administrador"
_NAME_SQL = "SELECT name, profile FROM access.users WHERE id = $1"


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _toast(message: str, kind: str = "ok") -> tuple[str, Any]:
    return ("toast", {"message": message, "kind": kind})


def _done(message: str, kind: str = "ok", *, close: bool = True) -> Response:
    events: list[tuple[str, Any]] = [("pessoasChanged", {}), _toast(message, kind)]
    if close:
        events.append(("closeOverlays", {}))
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(*events)
    return response


def _refused(status: int, message: str, *, reload: bool = False) -> Response:
    """Recusa: o HTMX nao troca (4xx), mas o aviso aparece; sem JS, o texto simples."""
    events: list[tuple[str, Any]] = [_toast(message, "erro")]
    if reload:
        events.insert(0, ("pessoasChanged", {}))
    response = Response(content=message, status_code=status, media_type="text/plain")
    response.headers["HX-Trigger"] = _trigger(*events)
    response.headers["Cache-Control"] = "no-store, private"
    return response


def _render_modal(
    request: Request, admin: PanelSession, user_id: int, name: str, *, error: str | None = None
) -> Response:
    context = {
        "slot": "modal",
        "csrf_token": csrf_token_for(request, admin),
        "person": {"id": user_id, "name": name},
        "error": error,
        "action": f"/dashboard/pessoas/{user_id}/excluir",
    }
    return templates_for(request).TemplateResponse(request, TEMPLATE, context)


async def _name_and_profile(request: Request, user_id: int) -> tuple[str, str] | None:
    async with request.app.state.pool.acquire() as conn:
        row = await conn.fetchrow(_NAME_SQL, user_id)
    return None if row is None else (str(row["name"]), str(row["profile"]))


@router.post(ITEM_PATH + "/exportar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def exportar(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    clock = get_clock(request)
    try:
        async with request.app.state.pool.acquire() as conn:
            data = await lgpd.export_person(conn, user_id, admin.email, clock=clock)
    except lgpd.PersonNotFound:
        return _refused(404, GONE_TEXT, reload=True)
    except lgpd.PersonAlreadyDeleted:
        return _refused(409, DELETED_TEXT, reload=True)
    filename = lgpd.export_filename(user_id, clock.now())
    logger.info("panel_lgpd_exported", extra={"user_id": user_id})
    return Response(
        content=lgpd.export_to_json(data),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, private",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(ITEM_PATH + "/excluir", include_in_schema=False)
async def excluir_form(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    found = await _name_and_profile(request, user_id)
    if found is None:
        return _refused(404, GONE_TEXT, reload=True)
    name, profile = found
    if name == lgpd.anonymous_label(user_id):
        return _refused(409, DELETED_TEXT, reload=True)
    if profile == ADMIN_PROFILE:
        return _refused(403, ADMIN_TEXT)  # decisao do Caio (2026-10-26)
    return _render_modal(request, admin, user_id, name)


@router.post(ITEM_PATH + "/excluir", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def excluir(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    confirm_name: Annotated[str, Form()] = "",
) -> Response:
    """Confere o nome digitado ANTES de qualquer escrita; so responde depois do COMMIT."""
    found = await _name_and_profile(request, user_id)
    if found is None:
        return _refused(404, GONE_TEXT, reload=True)
    name, profile = found
    if profile == ADMIN_PROFILE:
        return _refused(403, ADMIN_TEXT)  # recusa no servidor, antes de qualquer escrita
    if confirm_name != name:
        return _render_modal(request, admin, user_id, name, error=NAME_MISMATCH_TEXT)
    try:
        async with request.app.state.pool.acquire() as conn:
            await lgpd.delete_person(conn, user_id, admin.email, clock=get_clock(request))
    except lgpd.PersonNotFound:
        return _refused(404, GONE_TEXT, reload=True)
    except lgpd.PersonAlreadyDeleted:
        return _done(DELETED_TEXT, "aviso")
    logger.info("panel_lgpd_deleted", extra={"user_id": user_id})
    return _done(DELETED_OK_TEXT)
