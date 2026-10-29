"""PDF mensal (PN-42): PDF valido, conteudo esperado e mes sem dados coerente."""

import asyncio
from datetime import UTC, datetime

from app.panel.services.report_pdf import (
    PDF_FILENAME_FORMAT,
    build_monthly_pdf,
    month_title,
    render_monthly_pdf,
)
from app.panel.services.reports import MonthlyReport, ReportItem, month_bounds


def _report(**overrides: object) -> MonthlyReport:
    start, end = month_bounds("2026-10")
    data: dict[str, object] = {
        "month": "2026-10",
        "start": start,
        "end": end,
        "total": 40,
        "answered_bot": 30,
        "answered_human": 6,
        "awaiting": 3,
        "send_error": 1,
        "escalated": 4,
        "by_brand": [("positive", 25), ("tilibra", 15)],
        "by_classification": [("ARGUMENTO", 12), ("INFORMACAO", 28)],
        "top_questions": [
            ReportItem(ticket_id=1, text="Qual o prazo <b>de</b> entrega?", count=7, brand=None)
        ],
        "response_p50_seconds": 45.0,
        "response_p95_seconds": 600.0,
    }
    data.update(overrides)
    return MonthlyReport(**data)  # type: ignore[arg-type]


def _empty() -> MonthlyReport:
    return _report(
        total=0,
        answered_bot=0,
        answered_human=0,
        awaiting=0,
        send_error=0,
        escalated=0,
        by_brand=[],
        by_classification=[],
        top_questions=[],
        response_p50_seconds=None,
        response_p95_seconds=None,
    )


def _assert_valid_pdf(data: bytes) -> None:
    assert data.startswith(b"%PDF-")
    assert data.rstrip().endswith(b"%%EOF")


def test_pdf_com_dados_e_valido_e_tem_o_conteudo() -> None:
    data = build_monthly_pdf(_report(), compress=False)
    _assert_valid_pdf(data)
    for text in (
        b"Relatorio mensal",
        b"Outubro de 2026",
        b"Indicadores",
        b"Tickets por marca",
        b"Tickets por classificacao",
        b"Top 10 perguntas",
        b"Tempo de resposta",
        b"Qual o prazo",
        b"45 s",
        b"10 min",
    ):
        assert text in data, text


def test_pdf_comprimido_tambem_e_valido() -> None:
    data = build_monthly_pdf(_report())
    _assert_valid_pdf(data)
    assert b"Qual o prazo" not in data


def test_mes_sem_dados_gera_pdf_coerente() -> None:
    data = build_monthly_pdf(_empty(), compress=False)
    _assert_valid_pdf(data)
    assert b"Nenhum ticket neste mes." in data
    assert b"Sem dados no mes." in data
    assert b"Outubro de 2026" in data


async def test_render_async_devolve_pdf() -> None:
    data = await render_monthly_pdf(_empty())
    _assert_valid_pdf(data)


def test_render_async_fora_do_loop() -> None:
    _assert_valid_pdf(asyncio.run(render_monthly_pdf(_report())))


def test_texto_longo_e_truncado_sem_quebrar() -> None:
    item = ReportItem(ticket_id=2, text="x" * 5000, count=1, brand="positive")
    data = build_monthly_pdf(_report(top_questions=[item]), compress=False)
    _assert_valid_pdf(data)
    assert b"x" * 200 not in data


def test_titulo_e_nome_do_arquivo() -> None:
    assert month_title("2026-01") == "Janeiro de 2026"
    assert month_title("2026-12") == "Dezembro de 2026"
    assert PDF_FILENAME_FORMAT.format(month="2026-10") == "relatorio-2026-10.pdf"
    assert datetime(2026, 10, 1, tzinfo=UTC) <= _report().end
