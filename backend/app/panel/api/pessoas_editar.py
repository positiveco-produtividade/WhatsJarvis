"""Editar a pessoa e trocar o numero pela ficha (cadastro.1; CA-1..CA-13).

- ``GET  /dashboard/pessoas/{id}/editar``         formulario do cadastro preenchido (telefone
                                                   so leitura) com a versao lida (``updated_at``)
- ``POST /dashboard/pessoas/{id}/editar``         ``people_edit.edit_person`` numa transacao;
                                                   avisos de marca liberada vao ao despachante
                                                   depois do COMMIT
- ``GET  /dashboard/pessoas/{id}/trocar-numero``  modal com o numero novo
- ``POST /dashboard/pessoas/{id}/trocar-numero``  ``users.swap_number`` (regra da CLI) e
                                                   ``HX-Redirect`` para a ficha nova

Toda rota exige ``current_admin``; POST exige ``require_csrf``. So ``pending_optin``,
``active`` e ``declined`` editam ou trocam (CA-6). Nada de nome, telefone, empresa ou gestor
no log.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response
from starlette.background import BackgroundTask

from app.core.errors import DuplicatePhone, InvalidValue
from app.panel.api import pessoas_ficha
from app.panel.api.pessoas_cadastro import FIELD_LABELS, FormValues, render_person_form
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
from app.panel.services import people_edit
from app.services import users

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

ITEM_PATH = "/dashboard/pessoas/{user_id:int}"
SWAP_TEMPLATE = "_pessoa_trocar_numero.html"
GONE_TEXT = "Esta pessoa não existe mais."
NOT_EDITABLE_TEXT = "Esta pessoa está com o acesso revogado. Reative antes de editar."
CONFLICT_TEXT = (
    "Outra pessoa alterou este cadastro enquanto você editava. "
    "Os dados abaixo são os atuais: confira e salve de novo."
)
SAVED_TEXT = "Cadastro atualizado."
NOOP_TEXT = "Nada foi alterado."
SWAPPED_TEXT = "Número trocado. O Jarvis manda o aviso de privacidade para o número novo."
SWAP_DUPLICATE_TEXT = (
    "Esse número já está cadastrado (mesmo número, com ou sem o 9º dígito). Nada foi alterado."
)
SWAP_INVALID_TEXT = "Telefone inválido: use o DDD e o número (ex.: +55 85 9XXXX-XXXX)."
_SELECT = (
    "SELECT id, name, phone_e164, profile, company, ufs, channels, brands, manager_name, "
    "status, updated_at FROM access.users WHERE id = $1"
)


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _done(message: str, kind: str = "ok") -> Response:
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(
        ("pessoasChanged", {}), ("toast", {"message": message, "kind": kind}),
        ("closeOverlays", {}),
    )  # fmt: skip
    return response


def _refused(status: int, message: str) -> Response:
    response = Response(status_code=status)
    response.headers["HX-Trigger"] = _trigger(
        ("pessoasChanged", {}), ("toast", {"message": message, "kind": "erro"})
    )
    return response


def _editable(record: asyncpg.Record) -> bool:
    anonymized = str(record["phone_e164"]).startswith(users.ANONYMIZED_PREFIX)
    return record["status"] in users.EDITABLE_STATUSES and not anonymized


def _values(record: Any, phone: str) -> FormValues:
    return FormValues(
        name=record["name"], phone=phone, profile=record["profile"],
        company=record["company"], ufs=tuple(record["ufs"] or ()),
        channels=tuple(record["channels"] or ()), brands=tuple(record["brands"] or ()),
        manager=record["manager_name"],
    )  # fmt: skip


def _version(value: datetime | None) -> str:
    return value.isoformat() if value is not None else ""


def _parse_version(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw.strip())
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


async def _load(request: Request, user_id: int) -> asyncpg.Record | None:
    async with request.app.state.pool.acquire() as conn:
        record: asyncpg.Record | None = await conn.fetchrow(_SELECT, user_id)
    return record


def _edit_form(
    request: Request,
    admin: PanelSession,
    user_id: int,
    values: FormValues,
    version: datetime | None,
    problems: tuple[str, ...] = (),
) -> Response:
    return render_person_form(
        request, admin, values, problems=problems,
        action=f"/dashboard/pessoas/{user_id}/editar", editing=True, version=_version(version),
    )  # fmt: skip


@router.get(ITEM_PATH + "/editar", include_in_schema=False)
async def editar_form(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT)
    if not _editable(record):
        return _refused(409, NOT_EDITABLE_TEXT)
    values = _values(record, record["phone_e164"])
    return _edit_form(request, admin, user_id, values, record["updated_at"])


@router.post(ITEM_PATH + "/editar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def editar(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    nome: Annotated[str, Form()] = "",
    perfil: Annotated[str, Form()] = "",
    empresa: Annotated[str, Form()] = "",
    ufs: Annotated[list[str] | None, Form()] = None,
    canais: Annotated[list[str] | None, Form()] = None,
    marcas: Annotated[list[str] | None, Form()] = None,
    gestor: Annotated[str, Form()] = "",
    versao: Annotated[str, Form()] = "",
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT)
    if not _editable(record):
        return _refused(409, NOT_EDITABLE_TEXT)
    # O telefone nunca vem do formulario (CA-1): a validacao usa o atual.
    values = FormValues(
        name=nome, phone=record["phone_e164"], profile=perfil, company=empresa,
        ufs=tuple(ufs or ()), channels=tuple(canais or ()), brands=tuple(marcas or ()),
        manager=gestor,
    )  # fmt: skip
    expected = _parse_version(versao)
    checked = users.validate_row(values.as_row(), now=get_clock(request).now())
    if isinstance(checked, list):
        problems = tuple(f"{_label(e.field)}: {e.reason}" for e in checked)
        return _edit_form(request, admin, user_id, values, expected, problems)
    async with request.app.state.pool.acquire() as conn:
        outcome = await people_edit.edit_person(
            conn, user_id, checked, expected_updated_at=expected, admin_email=admin.email,
            admin_name=admin.name, settings=get_settings(request), clock=get_clock(request),
        )  # fmt: skip
    status = outcome.result.status
    if status == users.UPDATE_NOT_FOUND:
        return _refused(404, GONE_TEXT)
    if status == users.UPDATE_NOT_EDITABLE:
        return _refused(409, NOT_EDITABLE_TEXT)
    if status == users.UPDATE_CONFLICT:
        current = outcome.result.current or {}
        fresh = FormValues(
            name=current.get("name", ""), phone=record["phone_e164"],
            profile=current.get("profile", ""), company=current.get("company", ""),
            ufs=tuple(current.get("ufs", ())), channels=tuple(current.get("channels", ())),
            brands=tuple(current.get("brands", ())), manager=current.get("manager_name", ""),
        )  # fmt: skip
        return _edit_form(
            request, admin, user_id, fresh, outcome.result.updated_at, (CONFLICT_TEXT,)
        )
    if status == users.UPDATE_NOOP:
        return _done(NOOP_TEXT, "aviso")
    response = _done(SAVED_TEXT)
    dispatcher = getattr(request.app.state, "outbound_dispatcher", None)
    if dispatcher is not None and outcome.outbound_ids:
        response.background = BackgroundTask(dispatcher, outcome.outbound_ids, ())
    return response


def _label(field: str) -> str:
    return FIELD_LABELS.get(field, field)


# --- trocar numero (CA-12, CA-13) ---------------------------------------------------------


def _swap_form(
    request: Request,
    admin: PanelSession,
    record: asyncpg.Record,
    *,
    typed: str = "",
    error: str | None = None,
) -> Response:
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "person": {"id": int(record["id"]), "name": record["name"], "phone": record["phone_e164"]},
        "action": f"/dashboard/pessoas/{int(record['id'])}/trocar-numero",
        "typed": typed,
        "error": error,
    }
    return templates_for(request).TemplateResponse(request, SWAP_TEMPLATE, context)


@router.get(ITEM_PATH + "/trocar-numero", include_in_schema=False)
async def trocar_numero_form(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT)
    if not _editable(record):
        return _refused(409, NOT_EDITABLE_TEXT)
    return _swap_form(request, admin, record)


@router.post(
    ITEM_PATH + "/trocar-numero", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def trocar_numero(
    request: Request,
    user_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    telefone: Annotated[str, Form()] = "",
) -> Response:
    record = await _load(request, user_id)
    if record is None:
        return _refused(404, GONE_TEXT)
    if not _editable(record):
        return _refused(409, NOT_EDITABLE_TEXT)
    try:
        async with request.app.state.pool.acquire() as conn, conn.transaction():
            _, created = await users.swap_number(
                conn, record["phone_e164"], telefone, admin.email, actor_type="panel",
                settings=get_settings(request), clock=get_clock(request),
            )  # fmt: skip
    except DuplicatePhone:
        logger.info("panel_person_swap_duplicate")
        return _swap_form(request, admin, record, typed=telefone, error=SWAP_DUPLICATE_TEXT)
    except InvalidValue:
        logger.info("panel_person_swap_invalid")
        return _swap_form(request, admin, record, typed=telefone, error=SWAP_INVALID_TEXT)
    logger.info("panel_person_number_swapped", extra={"from_id": user_id, "to_id": created.id})
    # O modal passa a mostrar a ficha nova (pending_optin), com o aviso e a lista atualizada.
    response = await pessoas_ficha.ficha(request, created.id, admin)
    response.headers["HX-Trigger"] = _trigger(
        ("pessoasChanged", {}), ("toast", {"message": SWAPPED_TEXT, "kind": "ok"})
    )
    return response
