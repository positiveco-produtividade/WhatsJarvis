"""Cadastrar pessoa e autorizar pedido (spec painel.1 4.5 e 4.8; task PN-T34; PN-32, PN-33, PN-36).

- ``GET  /dashboard/pessoas/nova``   modal do cadastro; ``?pedido=<attempt_id>`` preenche o
                                     telefone a partir de ``access.access_attempts``
- ``POST /dashboard/pessoas/nova``   valida os 8 campos (``users.validate_row``) e grava por
                                     ``users.register`` (boas-vindas na fila de saida; o envio e
                                     dos workers de PF, nunca aqui). Com ``pedido`` valido, a
                                     tentativa vira resolvida (``user_id`` preenchido) na mesma
                                     transacao do cadastro

Toda rota exige ``current_admin``; o POST exige ``require_csrf``. Problemas (campo nomeado,
telefone incompleto ou duplicado, inclusive pelo 9o digito) voltam como o proprio formulario
(200) para o HTMX trocar o corpo do modal. Sucesso: 200 sem corpo, ``HX-Reswap: none`` e
``HX-Trigger`` (``pessoasChanged``, ``toast``, ``closeOverlays``). A conexao do pool e
adquirida uma vez por pedido. Nenhum telefone, nome nem texto de mensagem vai para o log; a
primeira mensagem do desconhecido nao e lida aqui (so ``people_read.pending_requests``).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.core.errors import DuplicatePhone
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
from app.panel.values import (
    BRAND_LABELS,
    BRANDS,
    CHANNEL_LABELS,
    CHANNELS,
    PROFILE_LABELS,
    PROFILES,
    UFS,
)
from app.services import audit, users

__all__ = ["FormValues", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/pessoas/nova"
TEMPLATE = "_pessoa_form.html"
DEFAULT_COMPANY = "Positive (3C)"
DUPLICATE_TEXT = (
    "Telefone: esse número já está cadastrado (mesmo número, com ou sem o 9º dígito). "
    "Se a pessoa foi retirada, abra a ficha na aba Retiradas e use Reativar."
)
SUCCESS_TEXT = (
    "Pessoa cadastrada. O Jarvis envia a mensagem de boas-vindas; "
    "o acesso libera depois do Aceito."
)
ACTION_AUTHORIZED = "access_attempt.authorized"
_FIELD_LABELS: dict[str, str] = {
    users.KEY_NAME: "Nome",
    users.KEY_PHONE: "Telefone",
    users.KEY_PROFILE: "Perfil",
    users.KEY_COMPANY: "Empresa",
    users.KEY_UFS: "UFs",
    users.KEY_CHANNELS: "Canais",
    users.KEY_BRANDS: "Marcas",
    users.KEY_MANAGER: "Gestor responsável",
}
_ATTEMPT_PREFILL = "SELECT phone_key, attempt_count FROM access.access_attempts WHERE id = $1"
_RESOLVE_ATTEMPT = (
    "UPDATE access.access_attempts SET user_id = $2 "
    "WHERE id = $1 AND user_id IS NULL RETURNING id"
)


@dataclass(frozen=True, slots=True)
class FormValues:
    """Campos como o usuario os digitou (voltam intactos quando ha problemas)."""

    name: str = ""
    phone: str = ""
    profile: str = PROFILES[0]
    company: str = DEFAULT_COMPANY
    ufs: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    brands: tuple[str, ...] = ()
    manager: str = ""

    def as_row(self) -> Mapping[str, Any]:
        return {
            users.KEY_NAME: self.name,
            users.KEY_PHONE: self.phone,
            users.KEY_PROFILE: self.profile,
            users.KEY_COMPANY: self.company,
            users.KEY_UFS: list(self.ufs),
            users.KEY_CHANNELS: list(self.channels),
            users.KEY_BRANDS: list(self.brands),
            users.KEY_MANAGER: self.manager,
        }


@dataclass(frozen=True, slots=True)
class Pedido:
    """Tentativa de acesso que o administrador esta autorizando."""

    id: int
    attempts: int


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _done() -> Response:
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(
        ("pessoasChanged", {}),
        ("toast", {"message": SUCCESS_TEXT, "kind": "ok"}),
        ("closeOverlays", {}),
    )
    return response


def _parse_pedido(raw: str | None) -> int | None:
    """Id da tentativa; qualquer coisa que nao seja inteiro positivo e ignorada."""
    text = (raw or "").strip()
    if not text.isascii() or not text.isdigit():
        return None
    value = int(text)
    return value if 0 < value < 2**63 else None


async def _load_pedido(
    conn: asyncpg.Connection, attempt_id: int
) -> tuple[Pedido, str | None] | None:
    row = await conn.fetchrow(_ATTEMPT_PREFILL, attempt_id)
    if row is None:
        return None
    return Pedido(attempt_id, int(row["attempt_count"])), row["phone_key"]


def _render_form(
    request: Request,
    admin: PanelSession,
    values: FormValues,
    *,
    pedido: Pedido | None = None,
    problems: tuple[str, ...] = (),
) -> Response:
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "values": values,
        "action": PATH,
        "errors": list(problems),
        "pedido": pedido,
        "profile_options": [(p, PROFILE_LABELS.get(p, p)) for p in PROFILES],
        "uf_options": [(u, u) for u in UFS],
        "channel_options": [(c, CHANNEL_LABELS.get(c, c)) for c in CHANNELS],
        "brand_options": [(b, BRAND_LABELS.get(b, b)) for b in BRANDS],
    }
    return templates_for(request).TemplateResponse(request, TEMPLATE, context)


def _field_problems(errors: list[users.FieldError]) -> tuple[str, ...]:
    return tuple(f"{_FIELD_LABELS.get(e.field, e.field)}: {e.reason}" for e in errors)


async def _save(
    conn: asyncpg.Connection,
    request: Request,
    admin: PanelSession,
    data: users.ValidatedUser,
    attempt_id: int | None,
) -> None:
    """Cadastra (com boas-vindas na fila) e resolve o pedido numa unica transacao."""
    clock = get_clock(request)
    async with conn.transaction():
        user = await users.register(
            conn,
            data,
            actor=admin.email,
            actor_type="panel",
            enqueue_welcome=True,
            settings=get_settings(request),
            clock=clock,
        )
        if attempt_id is None:
            return
        resolved = await conn.fetchval(_RESOLVE_ATTEMPT, attempt_id, user.id)
        if resolved is not None:
            await audit.write(
                conn, "panel", admin.email, ACTION_AUTHORIZED, "access_attempt",
                str(attempt_id), new={"user_id": user.id}, clock=clock,
            )  # fmt: skip


@router.get(PATH, include_in_schema=False)
async def nova(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    pedido: str | None = None,
) -> Response:
    values = FormValues(brands=tuple(BRANDS))
    shown: Pedido | None = None
    attempt_id = _parse_pedido(pedido)
    if attempt_id is not None:
        async with request.app.state.pool.acquire() as conn:
            loaded = await _load_pedido(conn, attempt_id)
        if loaded is not None:
            shown, phone_key = loaded
            values = FormValues(phone=f"+{phone_key}" if phone_key else "", brands=tuple(BRANDS))
    return _render_form(request, admin, values, pedido=shown)


@router.post(PATH, include_in_schema=False, dependencies=[Depends(require_csrf)])
async def criar(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    nome: Annotated[str, Form()] = "",
    telefone: Annotated[str, Form()] = "",
    perfil: Annotated[str, Form()] = "",
    empresa: Annotated[str, Form()] = "",
    ufs: Annotated[list[str] | None, Form()] = None,
    canais: Annotated[list[str] | None, Form()] = None,
    marcas: Annotated[list[str] | None, Form()] = None,
    gestor: Annotated[str, Form()] = "",
    pedido: Annotated[str, Form()] = "",
) -> Response:
    values = FormValues(
        name=nome, phone=telefone, profile=perfil, company=empresa,
        ufs=tuple(ufs or ()), channels=tuple(canais or ()), brands=tuple(marcas or ()),
        manager=gestor,
    )  # fmt: skip
    attempt_id = _parse_pedido(pedido)
    shown = Pedido(attempt_id, 0) if attempt_id is not None else None
    checked = users.validate_row(values.as_row(), now=get_clock(request).now())
    if isinstance(checked, list):
        return _render_form(request, admin, values, pedido=shown, problems=_field_problems(checked))
    try:
        async with request.app.state.pool.acquire() as conn:
            await _save(conn, request, admin, checked, attempt_id)
    except DuplicatePhone:
        logger.info("panel_person_duplicate_phone")
        return _render_form(request, admin, values, pedido=shown, problems=(DUPLICATE_TEXT,))
    logger.info("panel_person_registered", extra={"from_request": attempt_id is not None})
    return _done()
