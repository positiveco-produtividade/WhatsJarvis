"""Pagina Metricas (spec painel.1, 4.5; PN-41, PN-43; task PN-T47).

``GET /dashboard/metrics?periodo=hoje|7|30``. Pedido HTMX devolve so o resultado (KPIs +
graficos); sem ele, a pagina inteira. Le somente via ``metrics_read`` (cache de 60 s). Os
dados dos graficos vao em ``<script type="application/json">`` (CSP) e sao lidos por
``metrics-charts.js``; sem dados no periodo nao ha grafico, so o estado vazio (PN-43).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

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
from app.panel.services.formatting import (
    format_decimal,
    format_int,
    format_percent,
    response_time_label,
)
from app.panel.services.metrics_read import (
    TARGET_P95_SECONDS,
    MetricsKpis,
    MetricsSnapshot,
    metrics_snapshot,
    parse_period,
)
from app.panel.services.scope import Scope
from app.panel.values import brand_label

router = APIRouter(route_class=PanelRoute)

PATH = "/dashboard/metrics"
PERIOD_OPTIONS: list[tuple[str, str]] = [
    ("30", "Últimos 30 dias"),
    ("7", "Últimos 7 dias"),
    ("hoje", "Hoje"),
]
USEFUL_TARGET_HINT = "sem avaliações · meta ≥ 85%"
RESOLVED_TARGET_HINT = "meta ≥ 85%"


@dataclass(frozen=True, slots=True)
class StatRow:
    """Linha com barra: rotulo, texto a direita e largura (0-100)."""

    label: str
    text: str
    width: float


@dataclass(frozen=True, slots=True)
class KpiCard:
    value: str
    label: str
    hint: str


@dataclass(frozen=True, slots=True)
class MetricsView:
    period_key: str
    kpis: list[KpiCard]
    chart: dict[str, dict[str, list[str] | list[int]]]
    has_messages: bool
    has_brands: bool
    brand_rows: list[StatRow]
    response_rows: list[StatRow]
    block_rows: list[StatRow]
    is_empty: bool
    brand_focus: str = ""


def _bar(value: float | None, ceiling: float) -> float:
    if value is None or ceiling <= 0:
        return 0.0
    return round(min(100.0, 100.0 * value / ceiling), 1)


def _seconds_text(seconds: float | None) -> str:
    return "—" if seconds is None else f"{format_decimal(seconds, 1)} s"


def _change_hint(pct: float | None) -> str:
    if pct is None:
        return "sem período anterior para comparar"
    sign = "+" if pct > 0 else ""
    return f"{sign}{format_decimal(pct, 0)}% vs período anterior"


def _useful_hint(k: MetricsKpis) -> str:
    if not k.has_ratings:
        return USEFUL_TARGET_HINT
    coverage = f"cobertura {format_percent(k.coverage_pct)}"
    return f"{RESOLVED_TARGET_HINT} · {coverage} ({format_int(k.rated_count)} avaliações)"


def _kpi_cards(snap: MetricsSnapshot) -> list[KpiCard]:
    k = snap.kpis
    registered = format_int(k.registered_users)
    return [
        KpiCard(
            format_percent(k.active_users_pct),
            "Usuários ativos",
            f"{format_int(k.active_users)} de {registered} cadastrados",
        ),
        KpiCard(format_int(k.tickets), "Tickets", _change_hint(k.tickets_change_pct)),
        KpiCard(
            format_percent(k.resolved_without_escalation_pct),
            "Resolvidos sem escalar",
            RESOLVED_TARGET_HINT,
        ),
        KpiCard(
            format_percent(k.useful_pct),
            "Avaliados como úteis",
            _useful_hint(k),
        ),
    ]


def _response_rows(snap: MetricsSnapshot) -> list[StatRow]:
    r = snap.response
    human = r.human_median_seconds
    return [
        StatRow("Mediana", _seconds_text(r.p50_seconds), _bar(r.p50_seconds, 30.0)),
        StatRow(
            f"p95 (meta ≤ {format_int(int(TARGET_P95_SECONDS))} s)",
            _seconds_text(r.p95_seconds),
            _bar(r.p95_seconds, 30.0),
        ),
        StatRow(
            "Escalados respondidos pela equipe (mediana)",
            "—" if human is None else response_time_label(human),
            _bar(human, 24 * 3600.0),
        ),
    ]


def _block_rows(snap: MetricsSnapshot) -> list[StatRow]:
    b = snap.blocks
    ceiling = float(max(b.total, 1))

    def row(label: str, count: int | None, noun: str) -> StatRow:
        if count is None:
            return StatRow(label, "—", 0.0)
        text = f"{format_int(count)} {noun}{'' if count == 1 else 's'}"
        return StatRow(label, text, _bar(float(count), ceiling))

    return [
        row("Números desconhecidos", b.unknown_numbers, "tentativa"),
        row("Limite de 60 msg/h", b.rate_limited, "ocorrência"),
        row("Assinatura inválida (descartados)", b.invalid_signature, "descarte"),
    ]


def build_view(snap: MetricsSnapshot) -> MetricsView:
    days = [(d.day.strftime("%d/%m"), d.count) for d in snap.messages_per_day]
    brands = [(brand_label(s.brand), s.tickets) for s in snap.by_brand]
    return MetricsView(
        period_key=snap.period.key,
        kpis=_kpi_cards(snap),
        chart={
            "days": {"labels": [d[0] for d in days], "values": [d[1] for d in days]},
            "brands": {"labels": [b[0] for b in brands], "values": [b[1] for b in brands]},
        },
        has_messages=snap.total_messages > 0,
        has_brands=bool(snap.by_brand),
        brand_rows=[
            StatRow(
                brand_label(s.brand), f"{format_int(s.tickets)} · {format_percent(s.pct)}", s.pct
            )
            for s in snap.by_brand
        ],
        response_rows=_response_rows(snap),
        block_rows=_block_rows(snap),
        is_empty=snap.is_empty,
        brand_focus=brand_label(snap.brand) if snap.brand else "",
    )


@router.get(PATH, include_in_schema=False)
async def metrics(
    request: Request,
    admin: Annotated[PanelSession, Depends(current_admin)],
    scope: Annotated[Scope, Depends(brand_scope)],
    periodo: str | None = None,
) -> Response:
    period = parse_period(periodo)
    now = get_clock(request).now()
    snapshot = await metrics_snapshot(request.app.state.pool, scope, period, now=now)
    context = {
        "active": "metrics",
        "brand": scope.cookie_value,
        "csrf_token": csrf_token_for(request, admin),
        "admin_name": admin.name,
        "view": build_view(snapshot),
        "period_options": PERIOD_OPTIONS,
        "period_key": period.key,
        "metrics_path": PATH,
    }
    name = "_metrics_charts.html" if request.headers.get("hx-request") else "metrics.html"
    response = templates_for(request).TemplateResponse(request, name, context)
    response.headers["Vary"] = "HX-Request"
    return response
