"""Ficha da pessoa, revogar, reativar e reenviar (spec painel.1 4.5 e 4.7; task PN-T35).

- ``GET  /dashboard/pessoas/{id}``          ficha em modal (unico lugar com o telefone completo)
- ``GET  /dashboard/pessoas/{id}/revogar``  modal com a lista fixa de motivos
- ``POST /dashboard/pessoas/{id}/revogar``  ``users.revoke(..., actor_type="panel")``
- ``POST /dashboard/pessoas/{id}/reativar`` ``users.resend`` (``pending_optin``, aceite novo)
- ``POST /dashboard/pessoas/{id}/reenviar`` ``users.resend`` (mesmo servico, texto de reenvio)

Escrita: uma conexao, ``conn.transaction()`` e a resposta so sai depois do COMMIT; a auditoria
vai na mesma transacao (feita pelo servico). Revogar nao envia nada: o painel nunca toca no
canal (a resposta pendente morre no despacho por ``required_status``). O proprio administrador
(perfil ``administrador``) nao tem botao Revogar e o servidor tambem recusa (403). Sucesso =
200 sem corpo, ``HX-Reswap: none`` e ``HX-Trigger`` (``pessoasChanged``, ``toast``,
``closeOverlays``). Nada de PII (nome, telefone) vai para o log.
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.core.errors import InvalidValue
from app.core.values import RevokeReason
from app.panel.api.pessoas import REVOKE_REASON_LABELS
from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    csrf_token_for,
    current_admin,
    get_clock,
    get_settings,
    require_csrf,
    templates_for,
)
from app.panel.values import brand_label, profile_label
from app.services import users

__all__ = ["OFFERED_REASONS", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

ITEM_PATH = "/dashboard/pessoas/{user_id:int}"
FICHA_TEMPLATE = "_pessoa_ficha.html"
REVOKE_TEMPLATE = "_pessoa_revogar.html"
GONE_TEXT = "Esta pessoa não existe mais."
SELF_TEXT = "Você não pode revogar o próprio acesso."
# "other" existe no banco, mas a tela nao o oferece (spec 4.7).
OFFERED_REASONS: tuple[str, ...] = tuple(
    m.value for m in RevokeReason if m is not RevokeReason.OTHER
)
_REVOKABLE = ("active", "pending_optin")
_REACTIVABLE = ("revoked", "declined")
_EXTRA_PROFILES = {"parceiro_externo": "Parceiro", "administrador": "Administrador"}
_STATUS_TEXT = {
    "active": "Ativa",
    "pending_optin": "Aguardando aceite do aviso",
    "declined": "Recusou o aviso",
    "revoked": "Acesso revogado",
}
_SELECT = (
    "SELECT id, name, phone_e164, profile, company, ufs, channels, brands, manager_name, "
    "valid_until, status, status_changed_at, accepted_at, accepted_notice_version, "
    "declined_at, revoked_at, revoked_by, revoked_reason, created_at, created_by "
    "FROM access.users WHERE id = $1"
)


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
    """Recusa: o HTMX nao troca (4xx), mas o aviso aparece."""
    events: list[tuple[str, Any]] = [_toast(message, "erro")]
    if reload:
        events.insert(0, ("pessoasChanged", {}))
    response = Response(status_code=status)
    response.headers["HX-Trigger"] = _trigger(*events)
    return response


def _is_anonymized(record: asyncpg.Record) -> bool:
    """Dados excluidos pela LGPD: sem telefone, nao ha o que reativar nem reenviar."""
    return str(record["phone_e164"]).startswith(users.ANONYMIZED_PREFIX)


def _is_self(record: asyncpg.Record) -> bool:
    """Registro do proprio administrador (perfil ``administrador``; ``access.users`` nao
    guarda e-mail, entao o perfil e o unico vinculo)."""
    return bool(record["profile"] == "administrador")


async def _load(request: Request, user_id: int) -> asyncpg.Record | None:
    async with request.app.state.pool.acquire() as conn:
        record: asyncpg.Record | None = await conn.fetchrow(_SELECT, user_id)
    return record


def _person(record: asyncpg.Record) -> dict[str, Any]:
    """Dados da ficha prontos para o template (telefone completo, so aqui)."""
    profile = record["profile"]
    status = record["status"]
    reason = record["revoked_reason"]
    return {
        "id": int(record["id"]),
        "name": record["name"],
        "phone": record["phone_e164"],
        "profile": _EXTRA_PROFILES.get(profile) or profile_label(profile),
        "company": record["company"],
        "ufs": ", ".join(record["ufs"]) or "—",
        "channels": ", ".join(record["channels"]) or "—",
        "brands": ", ".join(brand_label(b) for b in record["brands"]) or "—",
        "manager": record["manager_name"],
        "valid_until": record["valid_until"],
        "status": status,
        "status_text": _STATUS_TEXT.get(status, status),
        "status_changed_at": record["status_changed_at"],
        "accepted_at": record["accepted_at"],
        "notice_version": record["accepted_notice_version"],
        "revoked_at": record["revoked_at"],
        "revoked_by": record["revoked_by"],
        "revoked_reason": REVOKE_REASON_LABELS.get(reason, reason) if reason else None,
        "created_at": record["created_at"],
        "created_by": record["created_by"],
        "can_revoke": status in _REVOKABLE and not _is_self(record),
        "can_reactivate": status in _REACTIVABLE and not _is_anonymized(record),
        "can_resend": status == "pending_optin" and not _is_anonymized(record),
        "is_self": _is_self(record),
    }


def _reason_options() -> list[tuple[str, str]]:
    return [(value, REVOKE_REASON_LABELS[value]) for value in OFFERED_REASONS]


def _render_revoke(
    request: Request,
    admin: PanelSession,
    record: asyncpg.Record,
    *,
    error: str | None = None,
    chosen: str = "",
) -> Response:
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "person": _person(record),
        "reasons": _reason_options(),
        "chosen": chosen,
        "error": error,
        "action": f"/dashboard/pessoas/{int(record['id'])}/revogar",
    }
    return templates_for(request).TemplateResponse(request, REVOKE_TEMPLATE, context)


@router.get(ITEM_PATH, include_in_schema=False)
async def ficha(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT, reload=True)
    context = {"csrf_token": csrf_token_for(request, admin), "person": _person(record)}
    return templates_for(request).TemplateResponse(request, FICHA_TEMPLATE, context)


@router.get(ITEM_PATH + "/revogar", include_in_schema=False)
async def revogar_form(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT, reload=True)
    if _is_self(record):
        return _refused(403, SELF_TEXT)
    return _render_revoke(request, admin, record)


@router.post(ITEM_PATH + "/revogar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def revogar(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    reason: Annotated[str, Form()] = "",
) -> Response:
    """Revoga e so responde depois do COMMIT (a saida do ``async with`` da transacao)."""
    async with request.app.state.pool.acquire() as conn:
        outcome = await _revoke_in_transaction(request, conn, user_id, reason, admin)
    if isinstance(outcome, Response):
        return outcome
    logger.info("panel_person_revoked", extra={"user_id": user_id})
    return _done("Acesso revogado. A pessoa está em Retiradas.")


async def _revoke_in_transaction(
    request: Request,
    conn: asyncpg.Connection,
    user_id: int,
    reason: str,
    admin: PanelSession,
) -> Response | None:
    """``None`` = revogou (transacao ja confirmada); ``Response`` = recusa sem escrever."""
    async with conn.transaction():
        record = await conn.fetchrow(_SELECT, user_id)
        if record is None:
            return _refused(404, GONE_TEXT, reload=True)
        if _is_self(record):
            return _refused(403, SELF_TEXT)
        if record["status"] not in _REVOKABLE:
            return _done("A pessoa já não está autorizada.", "aviso")
        if reason not in OFFERED_REASONS:
            return _render_revoke(
                request, admin, record, error="Escolha um motivo da lista.", chosen=""
            )
        await users.revoke(
            conn, user_id, reason, admin.email, actor_type="panel", clock=get_clock(request)
        )
    return None


async def _resend(request: Request, user_id: int, admin: PanelSession, *, ok_text: str) -> Response:
    async with request.app.state.pool.acquire() as conn:
        try:
            async with conn.transaction():
                await users.resend(
                    conn,
                    user_id,
                    admin.email,
                    actor_type="panel",
                    settings=get_settings(request),
                    clock=get_clock(request),
                )
        except users.UserAlreadyActive:
            return _done("A pessoa já está ativa; não há o que reenviar.", "aviso")
        except InvalidValue:
            return _refused(404, GONE_TEXT, reload=True)
    logger.info("panel_person_resent", extra={"user_id": user_id})
    return _done(ok_text)


@router.post(ITEM_PATH + "/reativar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def reativar(
    request: Request, user_id: int, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    return await _resend(
        request,
        user_id,
        admin,
        ok_text="Pessoa reativada. Recebe de novo o aviso de privacidade para aceitar.",
    )


@router.post(ITEM_PATH + "/reenviar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def reenviar(
    request: Request, user_id: int, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    return await _resend(request, user_id, admin, ok_text="Aviso de privacidade reenviado.")
