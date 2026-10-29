"""Importar pessoas por CSV: telas e rotas (PN-40, PN-4; task PN-T38; spec 4.8).

- ``GET  /dashboard/pessoas/importar``                  modal (arquivo + link do modelo)
- ``GET  /dashboard/pessoas/importar/modelo``           CSV modelo (cabecalho + exemplo ficticio)
- ``POST /dashboard/pessoas/importar``                  envia, classifica, cria o lote
- ``GET  /dashboard/pessoas/importar/{lote}``           conferencia (reabrivel enquanto o lote vale)
- ``POST .../{lote}/gravar``                            grava so as validas e mostra o relatorio
- ``POST .../{lote}/cancelar``                          descarta

Mesmo padrao PRG da FAQ, com uma diferenca: ao gravar, o lote perde as linhas (PII). O relatorio
das que ficaram de fora e devolvido uma unica vez, na propria resposta do POST (sem redirecionar).
Toda rota exige ``current_admin``; todo POST exige ``require_csrf``. Uma conexao do pool por
pedido. Nada de nome, telefone ou linha do arquivo vai para log ou excecao; telefones aparecem
mascarados.
"""

from __future__ import annotations

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
    get_settings,
    require_csrf,
    templates_for,
)
from app.panel.services import import_batches as ib
from app.panel.services import people_import as pi
from app.panel.services.mask import mask_phone_display
from app.panel.services.scope import Scope
from app.panel.values import PROFILE_LABELS

__all__ = ["router"]

logger = logging.getLogger(__name__)

router = APIRouter(route_class=PanelRoute)

BASE = "/dashboard/pessoas/importar"
PEOPLE_PATH = "/dashboard/pessoas"
FORM_TEMPLATE = "_pessoas_importar_form.html"
REVIEW_TEMPLATE = "pessoas_importar_previa.html"
MISSING_FILE = "Escolha um arquivo .csv."
TEMPLATE_FILENAME = "modelo-pessoas.csv"
TEMPLATE_CSV = (
    ",".join(pi.HEADER) + "\n"
    "Maria Exemplo,+5585999990000,vendedor,Loja Exemplo,CE|PE,atacarejo|farma,jungle,Joao\n"
)
_COMMIT_FAILED = "Não consegui gravar. Nada foi alterado. Tente de novo."


# ---------------------------------------------------------------- apoio


def _redirect(url: str) -> Response:
    return Response(status_code=303, headers={"Location": url})


def _parse_batch_id(raw: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


def _not_found(request: Request, admin: PanelSession, scope: Scope) -> Response:
    context = {
        "active": "pessoas",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
    }
    return templates_for(request).TemplateResponse(request, "404.html", context, status_code=404)


def _render_form(
    request: Request, admin: PanelSession, *, error: str = "", error_link: bool = False
) -> Response:
    context = {
        "csrf_token": csrf_token_for(request, admin),
        "error": error,
        "error_link": error_link,
        "template_url": f"{BASE}/modelo",
        "action": BASE,
        "max_rows": pi.MAX_ROWS,
        "header": pi.HEADER,
    }
    return templates_for(request).TemplateResponse(request, FORM_TEMPLATE, context)


def _display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Linhas para a tela: telefone mascarado, perfil com rotulo."""
    shown: list[dict[str, Any]] = []
    for row in rows:
        shown.append(
            {
                "linha": row.get("linha"),
                "nome": row.get("nome", ""),
                "telefone": mask_phone_display(str(row.get("telefone", ""))),
                "perfil": PROFILE_LABELS.get(str(row.get("perfil", "")), row.get("perfil", "")),
                "empresa": row.get("empresa", ""),
                "situacao": row.get("situacao", ""),
                "motivo": row.get("motivo", ""),
            }
        )
    return shown


def _render_review(
    request: Request,
    admin: PanelSession,
    scope: Scope,
    batch: ib.BatchRecord,
    *,
    error: str = "",
    result: pi.PeopleImportResult | None = None,
) -> Response:
    summary = batch.summary
    context: dict[str, Any] = {
        "active": "pessoas",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "batch": batch,
        "state": batch.status,
        "is_open": batch.is_open,
        "rows": _display_rows(batch.rows) if batch.is_open else [],
        "n_entra": int(summary.get(pi.ENTRA, 0)),
        "n_fora": int(summary.get(pi.FICA_DE_FORA, 0)),
        "n_erro": int(summary.get(pi.ERRO, 0)),
        "situation_labels": pi.SITUATION_LABELS,
        "error": error,
        "result": result,
        "report": _display_report(result),
        "people_url": PEOPLE_PATH,
        "action": f"{BASE}/{batch.id}",
    }
    response = templates_for(request).TemplateResponse(request, REVIEW_TEMPLATE, context)
    response.headers["Vary"] = "HX-Request"
    response.headers["Cache-Control"] = "no-store"
    return response


def _display_report(result: pi.PeopleImportResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    return [
        {
            "linha": r.linha,
            "nome": r.nome,
            "situacao": r.situacao,
            "label": pi.SITUATION_LABELS.get(r.situacao, r.situacao),
            "motivo": r.motivo,
        }
        for r in result.not_imported
    ]


async def _load_people_batch(
    conn: asyncpg.Connection, raw_id: str, request: Request
) -> ib.BatchRecord | None:
    batch_id = _parse_batch_id(raw_id)
    if batch_id is None:
        return None
    batch = await ib.get_batch(conn, batch_id, clock=get_clock(request))
    return batch if batch is not None and batch.kind == "people" else None


# ---------------------------------------------------------------- modal e modelo


@router.get(BASE, include_in_schema=False)
async def formulario(
    request: Request, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    return _render_form(request, admin)


@router.get(BASE + "/modelo", include_in_schema=False)
async def modelo(_: Annotated[PanelSession, Depends(current_admin)]) -> Response:
    return Response(
        content=TEMPLATE_CSV.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{TEMPLATE_FILENAME}"'},
    )


# ---------------------------------------------------------------- enviar


async def _read_upload(form: FormData) -> tuple[bytes, str] | None:
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return None
    data = await upload.read(pi.MAX_BYTES + 1)
    return (data, upload.filename) if data else None


@router.post(BASE, include_in_schema=False, dependencies=[Depends(require_csrf)])
async def enviar(
    request: Request, admin: Annotated[PanelSession, Depends(current_admin)]
) -> Response:
    form = await request.form()
    try:
        received = await _read_upload(form)
        if received is None:
            return _render_form(request, admin, error=MISSING_FILE)
        upload = await pi.load_upload(*received)
        async with request.app.state.pool.acquire() as conn:
            batch = await pi.create_preview(
                conn, upload, filename=received[1], actor=admin.email, clock=get_clock(request)
            )
    except pi.ImportFileError as exc:
        logger.info("panel_people_import_refused", extra={"code": exc.code})
        return _render_form(request, admin, error=exc.message, error_link=True)
    logger.info("panel_people_import_preview", extra={"rows": len(batch.rows)})
    url = f"{BASE}/{batch.id}"
    if request.headers.get("hx-request"):
        return Response(status_code=200, headers={"HX-Redirect": url})
    return _redirect(url)


# ---------------------------------------------------------------- conferencia


@router.get(BASE + "/{lote}", include_in_schema=False)
async def conferencia(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        batch = await _load_people_batch(conn, lote, request)
    if batch is None:
        return _not_found(request, admin, scope)
    return _render_review(request, admin, scope, batch)


# ---------------------------------------------------------------- gravar e cancelar


@router.post(BASE + "/{lote}/gravar", include_in_schema=False, dependencies=[Depends(require_csrf)])
async def gravar(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        batch = await _load_people_batch(conn, lote, request)
        if batch is None:
            return _not_found(request, admin, scope)
        try:
            result = await pi.commit_import(
                conn,
                batch.id,
                actor=admin.email,
                settings=get_settings(request),
                clock=get_clock(request),
            )
        except Exception as exc:  # noqa: BLE001 - desfeito pelo servico; nada de texto no log
            logger.warning("panel_people_import_failed", extra={"error": type(exc).__name__})
            return _render_review(request, admin, scope, batch, error=_COMMIT_FAILED)
        if result is None:  # ja gravado, cancelado ou vencido: a conferencia mostra o estado
            return _redirect(f"{BASE}/{batch.id}")
        committed = await ib.get_batch(conn, batch.id, clock=get_clock(request))
    logger.info(
        "panel_people_import_committed",
        extra={"imported": result.imported, "skipped": result.skipped, "invalid": result.invalid},
    )
    return _render_review(request, admin, scope, committed or batch, result=result)


@router.post(
    BASE + "/{lote}/cancelar", include_in_schema=False, dependencies=[Depends(require_csrf)]
)
async def cancelar(
    request: Request,
    lote: str,
    admin: Annotated[PanelSession, Depends(current_admin)],
) -> Response:
    async with request.app.state.pool.acquire() as conn:
        batch = await _load_people_batch(conn, lote, request)
        if batch is not None:
            cancelled = await ib.cancel_batch(conn, batch.id, clock=get_clock(request))
            logger.info("panel_people_import_cancelled", extra={"cancelled": cancelled})
    return _redirect(PEOPLE_PATH)
