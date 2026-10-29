"""Leitura de tickets do painel: KPIs e lista com filtros (PN-6, PN-7, PN-8, PN-59).

Le SOMENTE ``ops.v_tickets`` (a escrita fica em ``tickets_admin.py``). KPIs numa unica
consulta ``COUNT(*) FILTER``; lista paginada de 25 em 25 com o total vindo da mesma
consulta (``COUNT(*) OVER ()``). Todos os valores entram por parametros posicionais.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from app.panel.services.formatting import fold_text
from app.panel.services.scope import Scope
from app.panel.values import CLASSIFICATIONS, TICKET_STATUSES

__all__ = [
    "AWAITING_OLD_HOURS",
    "DEFAULT_DAYS",
    "PAGE_SIZE",
    "PERIOD_DAYS",
    "TicketFilters",
    "TicketKpis",
    "TicketPage",
    "TicketRow",
    "parse_filters",
    "ticket_kpis",
    "tickets_page",
]

PAGE_SIZE = 25
PERIOD_DAYS: tuple[int, ...] = (7, 14, 30, 60, 365)
DEFAULT_DAYS = 30
AWAITING_OLD_HOURS = 48

# Mesmo mapeamento da coluna gerada search_key (migration 102), para o nome do vendedor.
_FROM = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
_TO = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
_USER_NAME_KEY = f"lower(translate(u.name, '{_FROM}', '{_TO}'))"


@dataclass(frozen=True, slots=True)
class TicketFilters:
    """Filtros da tela Tickets; ``None``/vazio = sem filtro naquele campo."""

    q: str = ""
    classification: str | None = None
    status: str | None = None
    days: int = DEFAULT_DAYS
    page: int = 1


@dataclass(frozen=True, slots=True)
class TicketKpis:
    """Os 5 KPIs do periodo (PN-7). ``unclassified_pct`` e ``None`` sem tickets."""

    total: int
    awaiting: int
    awaiting_over_48h: int
    awaiting_media: int
    escalated: int
    unclassified_pct: float | None
    bot_answers: int


@dataclass(frozen=True, slots=True)
class TicketRow:
    id: int
    ticket_number: str
    created_at: datetime
    kind: str
    status: str
    message: str | None
    brand: str | None
    classification: str
    urgency: str | None
    escalated: bool
    user_id: int
    user_name: str


@dataclass(frozen=True, slots=True)
class TicketPage:
    rows: list[TicketRow]
    total: int
    page: int
    page_size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


def parse_filters(
    *,
    q: str | None = None,
    classification: str | None = None,
    status: str | None = None,
    days: str | int | None = None,
    page: str | int | None = None,
) -> TicketFilters:
    """Normaliza entrada da query string: valor invalido vira o padrao (nunca erro)."""
    try:
        d = int(days) if days is not None else DEFAULT_DAYS
    except ValueError:
        d = DEFAULT_DAYS
    try:
        p = int(page) if page is not None else 1
    except ValueError:
        p = 1
    return TicketFilters(
        q=(q or "").strip()[:200],
        classification=classification if classification in CLASSIFICATIONS else None,
        status=status if status in TICKET_STATUSES else None,
        days=d if d in PERIOD_DAYS else DEFAULT_DAYS,
        page=max(1, p),
    )


def _like_pattern(q: str) -> str:
    """``%termo%`` sem acento/caixa, com ``\\``, ``%`` e ``_`` escapados."""
    term = fold_text(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{term}%"


_KPI_SQL = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE t.status = 'awaiting') AS awaiting,
       count(*) FILTER (WHERE t.status = 'awaiting' AND t.created_at < {old}) AS awaiting_old,
       count(*) FILTER (WHERE t.status = 'awaiting' AND t.kind = 'media') AS awaiting_media,
       count(*) FILTER (WHERE t.escalated) AS escalated,
       count(*) FILTER (WHERE t.classification = 'NAO_IDENTIFICADO') AS unclassified,
       count(*) FILTER (WHERE t.status = 'answered_bot') AS bot_answers
  FROM ops.v_tickets t
 WHERE t.created_at >= {since} AND {scope}
"""


async def ticket_kpis(
    conn: asyncpg.Connection, scope: Scope, *, now: datetime, days: int = DEFAULT_DAYS
) -> TicketKpis:
    """KPIs do periodo (``days`` dias ate ``now``) no escopo de marca, em uma consulta."""
    params: list[object] = [now - timedelta(days=days), now - timedelta(hours=AWAITING_OLD_HOURS)]
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    sql = _KPI_SQL.format(since="$1", old="$2", scope=scope_sql)
    row = await conn.fetchrow(sql, *params)
    if row is None:  # agregacao sem GROUP BY sempre devolve uma linha
        raise RuntimeError("consulta de KPIs sem resultado")
    total = int(row["total"])
    return TicketKpis(
        total=total,
        awaiting=int(row["awaiting"]),
        awaiting_over_48h=int(row["awaiting_old"]),
        awaiting_media=int(row["awaiting_media"]),
        escalated=int(row["escalated"]),
        unclassified_pct=(int(row["unclassified"]) * 100.0 / total) if total else None,
        bot_answers=int(row["bot_answers"]),
    )


_LIST_SQL = """
SELECT t.id, t.ticket_number, t.created_at, t.kind, t.status, t.message, t.brand,
       t.classification, t.urgency, t.escalated, t.user_id, u.name AS user_name,
       count(*) OVER () AS total_rows
  FROM ops.v_tickets t
  JOIN access.users u ON u.id = t.user_id
 WHERE {where}
 ORDER BY t.created_at DESC, t.id DESC
 LIMIT {limit} OFFSET {offset}
"""


def _where(scope: Scope, f: TicketFilters, now: datetime, params: list[object]) -> str:
    params.append(now - timedelta(days=f.days))
    parts = [f"t.created_at >= ${len(params)}", scope.sql("t.brand", params, kind_column="t.kind")]
    if f.classification:
        params.append(f.classification)
        parts.append(f"t.classification = ${len(params)}")
    if f.status:
        params.append(f.status)
        parts.append(f"t.status = ${len(params)}")
    if f.q:
        params.append(_like_pattern(f.q))
        ph = f"${len(params)}"
        parts.append(
            f"(t.search_key LIKE {ph} ESCAPE '\\' OR {_USER_NAME_KEY} LIKE {ph} ESCAPE '\\')"
        )
    return " AND ".join(parts)


async def tickets_page(
    conn: asyncpg.Connection, scope: Scope, filters: TicketFilters, *, now: datetime
) -> TicketPage:
    """Uma pagina (25) da interseccao dos filtros; ``total`` conta todas as paginas."""
    params: list[object] = []
    where = _where(scope, filters, now, params)
    params.extend([PAGE_SIZE, (max(1, filters.page) - 1) * PAGE_SIZE])
    sql = _LIST_SQL.format(
        where=where, limit=f"${len(params) - 1}", offset=f"${len(params)}"
    )
    records = await conn.fetch(sql, *params)
    rows = [
        TicketRow(
            id=int(r["id"]),
            ticket_number=r["ticket_number"],
            created_at=r["created_at"],
            kind=r["kind"],
            status=r["status"],
            message=r["message"],
            brand=r["brand"],
            classification=r["classification"],
            urgency=r["urgency"],
            escalated=bool(r["escalated"]),
            user_id=int(r["user_id"]),
            user_name=r["user_name"],
        )
        for r in records
    ]
    total = int(records[0]["total_rows"]) if records else 0
    if not records and filters.page > 1:  # pagina alem do fim: total real numa 2a leitura
        total = await _count(conn, where, params[:-2])
    return TicketPage(rows=rows, total=total, page=filters.page)


async def _count(conn: asyncpg.Connection, where: str, params: list[object]) -> int:
    sql = f"SELECT count(*) FROM ops.v_tickets t JOIN access.users u ON u.id = t.user_id WHERE {where}"  # noqa: E501,S608
    return int(await conn.fetchval(sql, *params))
