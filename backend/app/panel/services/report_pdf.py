"""PDF do relatorio mensal do painel (PN-42; spec 4.6, Q-P7).

Recebe o ``MonthlyReport`` de ``services/reports.py`` e devolve os bytes do PDF:
indicadores, tickets por marca e por classificacao, as 10 perguntas mais frequentes e
o tempo de resposta. Mes sem dados gera um PDF coerente (zeros e avisos), nunca erro.

ReportLab e sincrono e usa CPU: a funcao publica roda em ``asyncio.to_thread``. Nenhum
texto de mensagem vai para log nem para excecao.
"""

import asyncio
import io
from collections.abc import Sequence
from typing import Final
from xml.sax.saxutils import escape

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet  # type: ignore[import-untyped]
from reportlab.lib.units import cm  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable  # type: ignore[import-untyped]

from app.panel.services.formatting import format_int, format_percent, response_time_label
from app.panel.services.reports import MonthlyReport, ReportItem
from app.panel.values import brand_label, classification_label

__all__ = ["PDF_FILENAME_FORMAT", "build_monthly_pdf", "month_title", "render_monthly_pdf"]

PDF_FILENAME_FORMAT: Final = "relatorio-{month}.pdf"
QUESTION_MAX_CHARS = 160
_MONTHS = (
    "Janeiro",
    "Fevereiro",
    "Marco",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
_EMPTY_MONTH = "Nenhum ticket neste mes."
_NO_DATA = "Sem dados no mes."
_GRID = colors.HexColor("#d4d4d8")
_HEAD_BG = colors.HexColor("#f4f4f5")


def month_title(month: str) -> str:
    """``2026-10`` vira ``Outubro de 2026``."""
    return f"{_MONTHS[int(month[5:]) - 1]} de {month[:4]}"


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": base["Title"],
        "h2": ParagraphStyle("h2", parent=base["Heading2"], spaceBefore=14, spaceAfter=6),
        "body": base["BodyText"],
        "muted": ParagraphStyle("muted", parent=base["BodyText"], textColor=colors.grey),
    }


def _pct(part: int, total: int) -> str:
    return format_percent(part * 100 / total, 1) if total else "—"


def _table(rows: Sequence[Sequence[object]], widths: Sequence[float]) -> Table:
    table = Table(list(rows), colWidths=list(widths), repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
                ("GRID", (0, 0), (-1, -1), 0.4, _GRID),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _kpis(report: MonthlyReport) -> Table:
    total = report.total
    rows = [
        ["Indicador", "Total", "% dos tickets"],
        ["Tickets no mes", format_int(total), "—"],
        ["Respondidos pelo bot", format_int(report.answered_bot), _pct(report.answered_bot, total)],
        [
            "Respondidos por humano",
            format_int(report.answered_human),
            _pct(report.answered_human, total),
        ],
        ["Aguardando resposta", format_int(report.awaiting), _pct(report.awaiting, total)],
        ["Escalados", format_int(report.escalated), _pct(report.escalated, total)],
        ["Falha no envio", format_int(report.send_error), _pct(report.send_error, total)],
    ]
    return _table(rows, (8 * cm, 4 * cm, 4 * cm))


def _breakdown(title: str, pairs: Sequence[tuple[str, int]], total: int) -> Table:
    rows: list[list[str]] = [[title, "Tickets", "% dos tickets"]]
    rows += [[name, format_int(n), _pct(n, total)] for name, n in pairs]
    return _table(rows, (8 * cm, 4 * cm, 4 * cm))


def _top_questions(items: Sequence[ReportItem], style: ParagraphStyle) -> Table:
    rows: list[list[object]] = [["#", "Pergunta", "Marca", "Vezes"]]
    for position, item in enumerate(items, start=1):
        text = item.text.strip()
        if len(text) > QUESTION_MAX_CHARS:
            text = text[: QUESTION_MAX_CHARS - 1] + "…"
        rows.append(
            [
                str(position),
                Paragraph(escape(text), style),
                brand_label(item.brand),
                format_int(item.count),
            ]
        )
    return _table(rows, (1 * cm, 10.2 * cm, 3.3 * cm, 1.5 * cm))


def _response_time(report: MonthlyReport) -> Table:
    def label(seconds: float | None) -> str:
        return "—" if seconds is None else response_time_label(seconds)

    rows = [
        ["Tempo de resposta", "Valor"],
        ["Mediana (50% respondidos em ate)", label(report.response_p50_seconds)],
        ["95% respondidos em ate", label(report.response_p95_seconds)],
    ]
    return _table(rows, (8 * cm, 8 * cm))


def _story(report: MonthlyReport) -> list[Flowable]:
    st = _styles()
    story: list[Flowable] = [
        Paragraph("Relatorio mensal - Jarvis", st["title"]),
        Paragraph(escape(month_title(report.month)), st["body"]),
    ]
    if report.is_empty:
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph(_EMPTY_MONTH, st["muted"]))
    story.append(Paragraph("Indicadores", st["h2"]))
    story.append(_kpis(report))

    story.append(Paragraph("Tickets por marca", st["h2"]))
    if report.by_brand:
        pairs = [(brand_label(k), n) for k, n in report.by_brand]
        story.append(_breakdown("Marca", pairs, report.total))
    else:
        story.append(Paragraph(_NO_DATA, st["muted"]))

    story.append(Paragraph("Tickets por classificacao", st["h2"]))
    if report.by_classification:
        pairs = [(classification_label(k), n) for k, n in report.by_classification]
        story.append(_breakdown("Classificacao", pairs, report.total))
    else:
        story.append(Paragraph(_NO_DATA, st["muted"]))

    story.append(Paragraph("Top 10 perguntas", st["h2"]))
    if report.top_questions:
        story.append(_top_questions(report.top_questions, st["body"]))
    else:
        story.append(Paragraph(_NO_DATA, st["muted"]))

    story.append(Paragraph("Tempo de resposta", st["h2"]))
    story.append(_response_time(report))
    return story


def build_monthly_pdf(report: MonthlyReport, *, compress: bool = True) -> bytes:
    """Monta o PDF (sincrono). ``compress=False`` deixa o texto legivel nos testes."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"Relatorio mensal {report.month}",
        author="Jarvis",
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
        pageCompression=1 if compress else 0,
        invariant=not compress,
    )
    doc.build(_story(report))
    return buffer.getvalue()


async def render_monthly_pdf(report: MonthlyReport) -> bytes:
    """PDF do mes sem bloquear o event loop."""
    return await asyncio.to_thread(build_monthly_pdf, report)
