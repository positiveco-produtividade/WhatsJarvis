"""Importar planilha da FAQ: telas e rotas (PN-27, PN-28, PN-29, PN-4; task PN-T30; spec 4.5/4.7).

- ``GET  /dashboard/faq/importar``                  modal (marca + arquivo + link do modelo)
- ``GET  /dashboard/faq/importar/modelo``           planilha modelo .xlsx
- ``POST /dashboard/faq/importar``                  envia, classifica, cria o lote
- ``GET  /dashboard/faq/importar/{lote}``           conferencia (reabrivel enquanto o lote vale)
- ``POST .../{lote}/gravar``                        grava (categoria por linha: ``cat_<linha>``)
- ``POST .../{lote}/cancelar``                      descarta

Toda rota exige ``current_admin``; todo POST exige ``require_csrf``. Arquivo recusado volta como o
proprio formulario (200) com a mensagem e o link do modelo. Gravar e cancelar redirecionam (303);
o servico ``faq_import`` garante que gravar duas vezes nao duplica. Uma conexao do pool por pedido.
Nada de texto da planilha vai para log ou excecao.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Annotated, Any

import asyncpg
from fastapi import APIRouter, Depends, Request, Response
from starlette.datastructures import FormData, UploadFile

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
from app.panel.services import faq_import as fi
from app.panel.services import import_batches as ib
from app.panel.services.faq_validation import ALL_BRANDS_KEY, FAQ_CATEGORIES
from app.panel.services.scope import Scope
from app.panel.values import (
    ALL_BRANDS_LABEL,
    BRAND_LABELS,
    BRANDS,
    classification_label,
)

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

BASE = "/dashboard/faq/importar"
FAQ_PATH = "/dashboard/faq"
FORM_TEMPLATE = "_faq_importar_form.html"
REVIEW_TEMPLATE = "faq_importar_previa.html"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CATEGORY_FIELD_PREFIX = "cat_"
MISSING_FILE = "Escolha um arquivo .xlsx ou .csv."
_COMMIT_MESSAGES: dict[str, str] = {
    "bad_category": "Categoria inválida na linha {line}. Escolha uma das opções da lista.",
    "unknown_line": "A tela não bate com a importação. Abra a conferência de novo.",
    "revalidation_failed": (
        "Não gravei nada: a linha {line} não passou na conferência final. "
        "Envie a planilha de novo."
    ),
    "wrong_batch": "Esta importação não é de perguntas da FAQ.",
}
# Mensagens de arquivo que pedem o link do modelo (colunas, tamanho, limite, leitura).
_NO_TEMPLATE_LINK = frozenset({"bad_brand"})


# ---------------------------------------------------------------- apoio


def _brand_options() -> list[tuple[str, str]]:
    return list(BRAND_LABELS.items())


def _redirect(url: str) -> Response:
    return Response(status_code=303, headers={"Location": url})


def _review_url(batch_id: uuid.UUID | str, **params: int) -> str:
    query = "&".join(f"{k}={int(v)}" for k, v in params.items())
    return f"{BASE}/{batch_id}" + (f"?{query}" if query else "")


def _parse_batch_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _not_found(request: Request, admin: PanelSession, scope: Scope) -> Response:
    context = {
        "active": "faq",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
    }
    return templates_for(request).TemplateResponse(request, "404.html", context, status_code=404)


def _render_form(
    request: Request,
    admin: PanelSession,
    *,
    brand: str,
    error: str = "",
    error_link: bool = False,
) -> Response:
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "brand_options": _brand_options(),
        "selected_brand": brand,
        "error": error,
        "error_link": error_link,
        "template_url": f"{BASE}/modelo",
        "action": BASE,
        "max_rows": fi.MAX_ROWS,
    }
    return templates_for(request).TemplateResponse(request, FORM_TEMPLATE, context)


def _brand_title(brand: str | None) -> str:
    if brand is None or brand == ALL_BRANDS_KEY:
        return ALL_BRANDS_LABEL
    return BRAND_LABELS.get(brand, brand)


def _render_review(
    request: Request,
    admin: PanelSession,
    scope: Scope,
    batch: ib.BatchRecord,
    *,
    error: str = "",
    gravadas: int | None = None,
    fora: int | None = None,
) -> Response:
    summary = batch.summary
    context: dict[str, Any] = {
        "active": "faq",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "batch": batch,
        "state": batch.status,
        "is_open": batch.is_open,
        "brand_title": _brand_title(batch.brand),
        "rows": batch.rows if batch.is_open else [],
        "n_entra": int(summary.get(fi.ENTRA, 0)),
        "n_fora": int(summary.get(fi.FICA_DE_FORA, 0)),
        "n_erro": int(summary.get(fi.ERRO, 0)),
        "n_conferir": int(summary.get("conferir", 0)),
        "situation_labels": fi.SITUATION_LABELS,
        "category_options": [(c, classification_label(c)) for c in FAQ_CATEGORIES],
        "category_labels": {c: classification_label(c) for c in FAQ_CATEGORIES},
        "field_prefix": CATEGORY_FIELD_PREFIX,
        "error": error,
        "gravadas": gravadas,
        "fora": fora,
        "faq_url": FAQ_PATH,
        "action": f"{BASE}/{batch.id}",
    }
    response = templates_for(request).TemplateResponse(request, REVIEW_TEMPLATE, context)
    response.headers["Vary"] = "HX-Request"
    return response


def _int_param(request: Request, name: str) -> int | None:
    raw = request.query_params.get(name, "")
    return int(raw) if raw.isdigit() and len(raw) < 6 else None


def _chosen_categories(form: FormData) -> dict[int, str]:
    """``{linha: CATEGORIA}`` dos campos ``cat_<linha>`` (valor invalido sobe como erro)."""
    chosen: dict[int, str] = {}
    for key, value in form.multi_items():
        suffix = key[len(CATEGORY_FIELD_PREFIX) :]
        if key.startswith(CATEGORY_FIELD_PREFIX) and suffix.isdigit() and isinstance(value, str):
            chosen[int(suffix)] = value
    return chosen


# ---------------------------------------------------------------- modal e modelo


@router.get(BASE, include_in_schema=False)
async def formulario(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    brand = scope.brand if scope.brand in BRANDS else BRANDS[0]
    return _render_form(request, admin, brand=brand)


@router.get(BASE + "/modelo", include_in_schema=False)
async def modelo(_: Annotated[PanelSession, Depends(current_admin)]) -> Response:
    data = await asyncio.to_thread(fi.template_xlsx)
    return Response(
        content=data,
        media_type=XLSX_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{fi.TEMPLATE_FILENAME}"'},
    )


# ---------------------------------------------------------------- enviar


async def _read_upload(form: FormData) -> tuple[bytes, str] | None:
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None
    data = await upload.read(fi.MAX_BYTES + 1)
    return (data, upload.filename) if data else None


@router.post(BASE, include_in_schema=False, dependencies=[Depends(require_csrf)])
async def enviar(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    form = await request.form()
    raw_brand = form.get("brand")
    brand = raw_brand if isinstance(raw_brand, str) else ""
    shown = brand if brand in BRANDS else BRANDS[0]
    try:
        if brand not in BRANDS:
            raise fi.ImportFileError("bad_brand")
        received = await _read_upload(form)
        if received is None:
            return _render_form(request, admin, brand=shown, error=MISSING_FILE)
        upload = await fi.load_upload(*received)
        async with request.app.state.pool.acquire() as conn:
            batch = await fi.create_preview(
                conn,
                upload,
                filename=received[1],
                brand=brand,
                actor=admin.email,
                clock=get_clock(request),
            )
    except fi.ImportFileError as exc:
        logger.info("panel_faq_import_refused", extra={"code": exc.code})
        return _render_form(
            request,
            admin,
            brand=shown,
            error=exc.message,
            error_link=exc.code not in _NO_TEMPLATE_LINK,
        )
    logger.info("panel_faq_import_preview", extra={"rows": len(batch.rows)})
    url = _review_url(batch.id)
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return _redirect(url)


# ---------------------------------------------------------------- conferencia


async def _load_faq_batch(
    conn: asyncpg.Connection, raw_id: str, request: Request
) -> ib.BatchRecord | None:
    batch_id = _parse_batch_id(raw_id)
    if batch_id is None:
        return None
    batch = await ib.get_batch(conn, batch_id, clock=get_clock(request))
    return batch if batch is not None and batch.kind == "faq" else None


@router.get(BASE + "/{lote}", include_in_schema=False)
async def conferencia(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        batch = await _load_faq_batch(conn, lote, request)
    if batch is None:
        return _not_found(request, admin, scope)
    return _render_review(
        request,
        admin,
        scope,
        batch,
        gravadas=_int_param(request, "gravadas"),
        fora=_int_param(request, "fora"),
    )


# ---------------------------------------------------------------- gravar e cancelar


def _commit_error_text(exc: fi.ImportCommitError) -> str:
    template = _COMMIT_MESSAGES.get(exc.code, "Não consegui gravar. Nada foi alterado.")
    return template.format(line=exc.line if exc.line is not None else "?")


@router.post(BASE + "/{lote}/gravar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def gravar(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    batch_id = _parse_batch_id(lote)
    if batch_id is None:
        return _not_found(request, admin, scope)
    categories = _chosen_categories(await request.form())
    clock = get_clock(request)
    async with request.app.state.pool.acquire() as conn:
        try:
            result = await fi.commit_import(
                conn, batch_id, actor=admin.email, categories=categories, clock=clock
            )
        except fi.ImportCommitError as exc:
            logger.warning("panel_faq_import_failed", extra={"code": exc.code})
            batch = await _load_faq_batch(conn, lote, request)
            if batch is None:
                return _not_found(request, admin, scope)
            return _render_review(request, admin, scope, batch, error=_commit_error_text(exc))
    if result is None:  # ja gravado, cancelado ou vencido: a conferencia mostra o estado
        return _redirect(_review_url(batch_id))
    logger.info("panel_faq_import_committed", extra={"imported": result.imported})
    return _redirect(_review_url(batch_id, gravadas=result.imported, fora=result.skipped))


@router.post(
    BASE + "/{lote}/cancelar", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def cancelar(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        batch = await _load_faq_batch(conn, lote, request)
        if batch is not None:
            cancelled = await ib.cancel_batch(conn, batch.id, clock=get_clock(request))
            logger.info("panel_faq_import_cancelled", extra={"cancelled": cancelled})
    return _redirect(FAQ_PATH)
