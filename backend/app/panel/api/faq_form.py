"""Formulario da FAQ e acoes (spec painel.1 4.5 e 4.7; task PN-T27; PN-23, PN-24, PN-26, PN-4).

- ``GET  /dashboard/faq/nova``              modal vazio; ``?ticket=J-nnnn`` preenche pelo ticket
                                            (nunca por texto na URL)
- ``POST /dashboard/faq/nova``              cria
- ``GET  /dashboard/faq/{id}/editar``       modal com o item
- ``POST /dashboard/faq/{id}/editar``       salva; versao diferente = aviso com o texto atual,
                                            so ``force=1`` sobrescreve
- ``POST /dashboard/faq/{id}/desativar``    tira do ar sem apagar
- ``POST /dashboard/faq/{id}/reativar``     volta ao ar

Toda rota exige ``current_admin``; todo POST exige ``require_csrf``. Sucesso devolve 200 sem
corpo, com ``HX-Reswap: none`` e ``HX-Trigger`` (``faqChanged`` recarrega a lista, ``toast``
avisa, ``closeOverlays`` fecha o modal). Problemas de validacao e conflito voltam como o proprio
formulario (200), para o HTMX trocar o corpo do modal. A conexao do pool e adquirida uma vez por
pedido. Nada de texto da FAQ vai para o log.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Form, Request, Response

from app.panel.api.ticket_drawer import TICKET_NUMBER
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
from app.panel.services import faq_admin
from app.panel.services.faq_validation import ALL_BRANDS_KEY, FAQ_CATEGORIES, QUESTION_MAX_LENGTH
from app.panel.services.scope import Scope
from app.panel.values import (
    ALL_BRANDS_LABEL,
    BRAND_LABELS,
    BRANDS,
    classification_label,
)

__all__ = ["FormValues", "router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

TEMPLATE = "_faq_form.html"
NEW_PATH = "/dashboard/faq/nova"
ITEM_PATH = "/dashboard/faq/{item_id}"
GONE_TEXT = "Esta pergunta não existe mais."
_TICKET_PREFILL = (
    "SELECT brand, classification, message FROM ops.v_tickets WHERE ticket_number = $1"
)


@dataclass(frozen=True, slots=True)
class FormValues:
    """Campos do formulario como o usuario os digitou (volta intacto quando ha problemas)."""

    brand: str
    category: str
    question: str = ""
    answer: str = ""
    variations: tuple[str, ...] = ()
    bot_responde: bool = True

    def as_input(self) -> faq_admin.FaqInput:
        return faq_admin.FaqInput(
            brand=self.brand,
            category=self.category,
            question=self.question,
            answer=self.answer,
            variations=self.variations,
            bot_responde=self.bot_responde,
        )


def _values_of(item: faq_admin.FaqRecord) -> FormValues:
    return FormValues(
        brand=item.brand,
        category=item.category,
        question=item.question,
        answer=item.answer or "",
        variations=item.variations,
        bot_responde=item.bot_responde,
    )


def _form_values(
    brand: str,
    category: str,
    question: str,
    answer: str,
    variations: list[str] | None,
    bot_responde: str | None,
) -> FormValues:
    return FormValues(
        brand=brand,
        category=category,
        question=question,
        answer=answer,
        variations=tuple(variations or ()),
        bot_responde=bot_responde is not None,
    )


def _brand_options() -> list[tuple[str, str]]:
    return [*BRAND_LABELS.items(), (ALL_BRANDS_KEY, ALL_BRANDS_LABEL)]


def _trigger(*events: tuple[str, Any]) -> str:
    return json.dumps(dict(events))


def _toast(message: str, kind: str = "ok") -> tuple[str, Any]:
    return ("toast", {"message": message, "kind": kind})


def _done(message: str, kind: str = "ok", *, close: bool = False) -> Response:
    """Sucesso sem corpo: nao troca nada, recarrega a lista e avisa (e fecha o modal)."""
    events: list[tuple[str, Any]] = [("faqChanged", {}), _toast(message, kind)]
    if close:
        events.append(("closeOverlays", {}))
    response = Response(status_code=200)
    response.headers["HX-Reswap"] = "none"
    response.headers["HX-Trigger"] = _trigger(*events)
    return response


def _gone() -> Response:
    """Item inexistente: 404 (o HTMX nao troca) + aviso e lista atualizada."""
    response = Response(status_code=404)
    response.headers["HX-Trigger"] = _trigger(("faqChanged", {}), _toast(GONE_TEXT, "erro"))
    return response


def _render_form(
    request: Request,
    admin: PanelSession,
    values: FormValues,
    *,
    item_id: int | None = None,
    item: faq_admin.FaqRecord | None = None,
    version: int | None = None,
    problems: tuple[str, ...] = (),
    conflict: faq_admin.FaqRecord | None = None,
    from_ticket: str | None = None,
) -> Response:
    """Fragmento do formulario para ``#modal-body``."""
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "values": values,
        "editing": item_id is not None,
        "version": version,
        "action": ITEM_PATH.format(item_id=item_id) + "/editar" if item_id else NEW_PATH,
        "errors": list(problems),
        "conflict": conflict,
        "from_ticket": from_ticket,
        "was_all_brands": item is not None and item.brand == ALL_BRANDS_KEY,
        "brand_options": _brand_options(),
        "category_options": [(c, classification_label(c)) for c in FAQ_CATEGORIES],
        "question_max": QUESTION_MAX_LENGTH,
    }
    return templates_for(request).TemplateResponse(request, TEMPLATE, context)


def _problem_texts(result: faq_admin.FaqWriteResult) -> tuple[str, ...]:
    return tuple(p.message for p in result.problems)


async def _prefill(conn: asyncpg.Connection, number: str, scope: Scope) -> FormValues | None:
    """Valores iniciais a partir de um ticket real; ``None`` se o numero nao existir."""
    if not TICKET_NUMBER.fullmatch(number):
        return None
    row = await conn.fetchrow(_TICKET_PREFILL, number)
    if row is None:
        return None
    category = row["classification"]
    return FormValues(
        brand=row["brand"] if row["brand"] in BRANDS else (scope.brand or BRANDS[0]),
        category=category if category in FAQ_CATEGORIES else FAQ_CATEGORIES[0],
        question=(row["message"] or "").strip()[:QUESTION_MAX_LENGTH],
    )


@router.get(NEW_PATH, include_in_schema=False)
async def nova(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    ticket: str | None = None,
) -> Response:
    values = FormValues(brand=scope.brand or BRANDS[0], category=FAQ_CATEGORIES[0])
    from_ticket = None
    if ticket:
        async with request.app.state.pool.acquire() as conn:
            prefilled = await _prefill(conn, ticket, scope)
        if prefilled is not None:
            values, from_ticket = prefilled, ticket
    return _render_form(request, admin, values, from_ticket=from_ticket)


@router.post(NEW_PATH, include_in_schema=False, dependencies=[Depends(require_csrf)])
async def criar(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    brand: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    question: Annotated[str, Form()] = "",
    answer: Annotated[str, Form()] = "",
    variations: Annotated[list[str] | None, Form()] = None,
    bot_responde: Annotated[str | None, Form()] = None,
) -> Response:
    values = _form_values(brand, category, question, answer, variations, bot_responde)
    async with request.app.state.pool.acquire() as conn:
        result = await faq_admin.create_item(
            conn, values.as_input(), actor=admin.email, clock=get_clock(request)
        )
    if result.ok and result.item is not None:
        logger.info("panel_faq_created")
        return _done("Pergunta criada. O Jarvis já passa a usá-la.", close=True)
    return _render_form(request, admin, values, problems=_problem_texts(result))


@router.get(ITEM_PATH + "/editar", include_in_schema=False)
async def editar(
    request: Request,
    item_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        item = await faq_admin.get_item(conn, item_id)
    if item is None:
        return _gone()
    return _render_form(
        request, admin, _values_of(item), item_id=item.id, item=item, version=item.version
    )


@router.post(
    ITEM_PATH + "/editar", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def salvar(
    request: Request,
    item_id: int,
    admin: Annotated[PanelSession, Depends(current_admin)],
    version: Annotated[int, Form()],
    brand: Annotated[str, Form()] = "",
    category: Annotated[str, Form()] = "",
    question: Annotated[str, Form()] = "",
    answer: Annotated[str, Form()] = "",
    variations: Annotated[list[str] | None, Form()] = None,
    bot_responde: Annotated[str | None, Form()] = None,
    force: Annotated[str | None, Form()] = None,
) -> Response:
    values = _form_values(brand, category, question, answer, variations, bot_responde)
    async with request.app.state.pool.acquire() as conn:
        result = await faq_admin.update_item(
            conn,
            item_id,
            values.as_input(),
            expected_version=version,
            actor=admin.email,
            force=force == "1",
            clock=get_clock(request),
        )
    if result.status == faq_admin.NOT_FOUND:
        return _gone()
    if result.ok:
        logger.info("panel_faq_updated", extra={"forced": force == "1"})
        return _done("Pergunta salva. O Jarvis já passa a usar a resposta nova.", close=True)
    conflict = result.item if result.status == faq_admin.CONFLICT else None
    if conflict is not None:
        logger.info("panel_faq_conflict")
    shown = conflict or result.item
    return _render_form(
        request,
        admin,
        values,
        item_id=item_id,
        item=shown,
        version=version,
        problems=_problem_texts(result),
        conflict=conflict,
    )


async def _toggle(
    request: Request, item_id: int, admin: PanelSession, *, activate: bool
) -> Response:
    clock = get_clock(request)
    async with request.app.state.pool.acquire() as conn:
        if activate:
            result = await faq_admin.reactivate_item(conn, item_id, actor=admin.email, clock=clock)
        else:
            result = await faq_admin.deactivate_item(conn, item_id, actor=admin.email, clock=clock)
    if result.status == faq_admin.NOT_FOUND:
        return _gone()
    if result.status == faq_admin.INVALID:
        message = " ".join(_problem_texts(result))
        return _done(f"Não reativei: {message}", "erro")
    if result.status == faq_admin.NOOP:
        state = "ativa" if activate else "desativada"
        return _done(f"A pergunta já estava {state}.", "aviso")
    logger.info("panel_faq_reactivated" if activate else "panel_faq_deactivated")
    if activate:
        return _done("Pergunta reativada. O Jarvis volta a usá-la.")
    return _done("Pergunta desativada. Está em Desativadas e pode ser reativada.")


@router.post(
    ITEM_PATH + "/desativar", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def desativar(
    request: Request, item_id: int, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    return await _toggle(request, item_id, admin, activate=False)


@router.post(
    ITEM_PATH + "/reativar", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def reativar(
    request: Request, item_id: int, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    return await _toggle(request, item_id, admin, activate=True)
