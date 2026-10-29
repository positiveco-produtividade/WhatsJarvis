"""Pagina Relatorios e PDF mensal (spec painel.1, 4.6; PN-42, PN-43, PN-4; task PN-T50).

- ``GET /dashboard/relatorios?periodo=7|14|30|60|365``  tres listas do campo (top perguntas,
  objecoes, sem resposta na base), escopadas pela marca do cookie;
- ``GET /dashboard/relatorios/mensal.pdf?mes=YYYY-MM``  PDF do mes (``application/pdf``).

"Criar na FAQ" abre ``/dashboard/faq/nova?ticket=J-nnnn``: o numero vem de
``ReportItem.ticket_id`` (uma consulta por pagina), nunca o texto da pergunta. Uma conexao do
pool por pedido, liberada antes de gerar o PDF. Nada de texto de mensagem em log/excecao.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Request, Response

from app.panel.auth.sessions import PanelSession
from app.panel.deps import (
    PanelRoute,
    brand_scope,
    csrf_token_for,
    current_admin,
    get_clock,
    templates_for,
)
from app.panel.services.formatting import SP_TZ, format_int
from app.panel.services.report_pdf import PDF_FILENAME_FORMAT, month_title, render_monthly_pdf
from app.panel.services.reports import (
    FieldReport,
    ReportItem,
    field_report,
    monthly_report,
    parse_days,
    parse_month,
)
from app.panel.services.scope import Scope
from app.panel.values import brand_label

__all__ = ["router"]

router = APIRouter(route_class=PanelRoute)

PATH: Final = "/dashboard/relatorios"
PDF_PATH: Final = PATH + "/mensal.pdf"
FAQ_NEW_PATH: Final = "/dashboard/faq/nova"
PERIOD_LABELS: Final = {
    7: "Últimos 7 dias",
    14: "Últimos 14 dias",
    30: "Últimos 30 dias",
    60: "Últimos 60 dias",
    365: "Último ano",
}
MONTHS_OFFERED: Final = 12
_NUMBERS_SQL = "SELECT id, ticket_number FROM ops.v_tickets WHERE id = ANY($1::bigint[])"


@dataclass(frozen=True, slots=True)
class ReportRow:
    """Linha de lista: texto, quantas vezes, marca e (se o ticket existe) o link da FAQ."""

    text: str
    count_label: str
    brand: str | None
    brand_name: str
    faq_url: str


def _faq_url(number: str | None) -> str:
    return f"{FAQ_NEW_PATH}?ticket={number}" if number else ""


def _rows(items: Sequence[ReportItem], numbers: dict[int, str]) -> list[ReportRow]:
    return [
        ReportRow(
            text=i.text.strip(),
            count_label=f"{format_int(i.count)}x",
            brand=i.brand,
            brand_name=brand_label(i.brand),
            faq_url=_faq_url(numbers.get(i.ticket_id)),
        )
        for i in items
    ]


def month_options(now: datetime) -> list[tuple[str, str]]:
    """Mes corrente e os 11 anteriores (``YYYY-MM``, titulo), em Sao Paulo."""
    local = now.astimezone(SP_TZ)
    year, month = local.year, local.month
    options: list[tuple[str, str]] = []
    for _ in range(MONTHS_OFFERED):
        key = f"{year:04d}-{month:02d}"
        options.append((key, month_title(key)))
        year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return options


def pdf_filename(month: str) -> str:
    """Nome seguro: ``month`` ja passou por ``parse_month`` (so digitos e hifen)."""
    return PDF_FILENAME_FORMAT.format(month=month)


async def _load(
    request: Request, scope: Scope, now: datetime, days: int
) -> tuple[FieldReport, dict[int, str]]:
    async with request.app.state.pool.acquire() as conn:
        report = await field_report(conn, scope, now=now, days=days)
        ids = sorted(
            {i.ticket_id for i in (*report.top_questions, *report.objections, *report.unanswered)}
        )
        records = await conn.fetch(_NUMBERS_SQL, ids) if ids else []
    return report, {int(r["id"]): str(r["ticket_number"]) for r in records}


@router.get(PATH, include_in_schema=False)
async def relatorios(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    periodo: str | None = None,
    mes: str | None = None,
) -> Response:
    now = get_clock(request).now()
    days = parse_days(periodo)
    report, numbers = await _load(request, scope, now, days)
    context = {
        "active": "relatorios",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "report": report,
        "top_rows": _rows(report.top_questions, numbers),
        "objection_rows": _rows(report.objections, numbers),
        "unanswered_rows": _rows(report.unanswered, numbers),
        "period_options": [(str(d), label) for d, label in PERIOD_LABELS.items()],
        "period_key": str(days),
        "month_options": month_options(now),
        "month_key": parse_month(mes, now),
        "relatorios_path": PATH,
        "pdf_path": PDF_PATH,
    }
    response = templates_for(request).TemplateResponse(request, "relatorios.html", context)
    response.headers["Vary"] = "HX-Request"
    return response


@router.get(PDF_PATH, include_in_schema=False)
async def mensal_pdf(
    request: Request,
    _: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    mes: str | None = None,
) -> Response:
    month = parse_month(mes, get_clock(request).now())
    async with request.app.state.pool.acquire() as conn:
        data = await monthly_report(conn, scope, month)
    content = await render_monthly_pdf(data)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{pdf_filename(month)}"',
            "Cache-Control": "no-store, private",
        },
    )
