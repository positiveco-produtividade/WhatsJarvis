"""Consultas de Relatorios do painel (PN-42, PN-43; spec 4.6).

Somente leitura sobre ``ops.v_tickets`` (e ``content.faq_items`` para saber o que a base
ja cobre). Tres listas do periodo: perguntas mais frequentes, objecoes mais citadas e
perguntas sem resposta na base; mais os dados do relatorio mensal (PDF).

Regras (nao ha campo de objecao no ticket; ver notas da tarefa T48):
- perguntas = tickets ``kind='question'`` agrupados pelo texto sem acento/caixa/espacos;
- objecoes = as mesmas perguntas com ``classification='ARGUMENTO'``;
- sem resposta na base = perguntas ``awaiting`` que nao casam com item ativo da FAQ
  que o bot responde e que nao ficaram aguardando so porque a marca estava desligada.

Escopo de marca por ``Scope.sql``. Periodo vazio devolve listas vazias e ``None`` nas
medias, nunca divisao por zero (PN-43). Valores sempre por parametros posicionais;
nenhum texto de mensagem vai para log nem para excecao.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from app.panel.services.formatting import SP_TZ
from app.panel.services.scope import Scope
from app.panel.services.tickets_read import DEFAULT_DAYS, PERIOD_DAYS

__all__ = [
    "TOP_LIMIT",
    "FieldReport",
    "MonthlyReport",
    "ReportItem",
    "field_report",
    "frequent_questions",
    "month_bounds",
    "monthly_report",
    "objections",
    "parse_days",
    "parse_month",
    "unanswered_questions",
]

TOP_LIMIT = 10
UNANSWERED_LIMIT = 20
ESCALATION_BRAND_OFF = "brand_off"

_FROM = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
_TO = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
_KEY = (
    f"btrim(regexp_replace(lower(translate(t.message, '{_FROM}', '{_TO}')), "
    "'\\s+', ' ', 'g'))"
)


@dataclass(frozen=True, slots=True)
class ReportItem:
    """Um grupo de perguntas iguais. ``ticket_id`` e o ticket mais recente do grupo
    (o "Criar na FAQ" abre a FAQ por ele, sem texto na URL)."""

    ticket_id: int
    text: str
    count: int
    brand: str | None


@dataclass(frozen=True, slots=True)
class FieldReport:
    days: int
    since: datetime
    top_questions: list[ReportItem]
    objections: list[ReportItem]
    unanswered: list[ReportItem]

    @property
    def is_empty(self) -> bool:
        return not (self.top_questions or self.objections or self.unanswered)


@dataclass(frozen=True, slots=True)
class MonthlyReport:
    """Dados do PDF mensal. ``month`` = ``YYYY-MM``; intervalo [start, end) em SP."""

    month: str
    start: datetime
    end: datetime
    total: int
    answered_bot: int
    answered_human: int
    awaiting: int
    send_error: int
    escalated: int
    by_brand: list[tuple[str | None, int]]
    by_classification: list[tuple[str, int]]
    top_questions: list[ReportItem]
    response_p50_seconds: float | None
    response_p95_seconds: float | None

    @property
    def is_empty(self) -> bool:
        return self.total == 0


def parse_days(raw: str | int | None) -> int:
    """Periodo da tela: valor invalido vira o padrao (nunca erro)."""
    try:
        days = int(raw) if raw is not None else DEFAULT_DAYS
    except ValueError:
        return DEFAULT_DAYS
    return days if days in PERIOD_DAYS else DEFAULT_DAYS


def parse_month(raw: str | None, now: datetime) -> str:
    """``YYYY-MM`` valido ou o mes corrente (Sao Paulo)."""
    try:
        if raw is not None and len(raw) == 7 and raw[4] == "-":
            year, month = int(raw[:4]), int(raw[5:])
            datetime(year, month, 1)
            return f"{year:04d}-{month:02d}"
    except ValueError:
        pass
    local = now.astimezone(SP_TZ)
    return f"{local.year:04d}-{local.month:02d}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    """[inicio, fim) do mes ``YYYY-MM`` em Sao Paulo, devolvidos em UTC."""
    year, mon = int(month[:4]), int(month[5:])
    start = datetime(year, mon, 1, tzinfo=SP_TZ)
    end = datetime(year + (mon == 12), mon % 12 + 1, 1, tzinfo=SP_TZ)
    return start.astimezone(UTC), end.astimezone(UTC)


_GROUP_SQL = """
SELECT (array_agg(t.id ORDER BY t.created_at DESC, t.id DESC))[1] AS ticket_id,
       (array_agg(t.message ORDER BY t.created_at DESC, t.id DESC))[1] AS text,
       (array_agg(t.brand ORDER BY t.created_at DESC, t.id DESC))[1] AS brand,
       count(*) AS n
  FROM ops.v_tickets t
 WHERE t.kind = 'question' AND t.created_at >= $1 AND t.created_at < $2
   AND t.message IS NOT NULL AND btrim(t.message) <> ''
   AND {scope}{extra}
 GROUP BY {key}
 ORDER BY n DESC, max(t.created_at) DESC, max(t.id) DESC
 LIMIT {limit}
"""

_UNANSWERED_EXTRA = f"""
   AND t.status = 'awaiting'
   AND coalesce(t.escalation_reason, '') <> '{ESCALATION_BRAND_OFF}'
   AND NOT EXISTS (
         SELECT 1 FROM content.faq_items f
          WHERE f.active AND f.bot_responde AND f.question_key = {_KEY}
            AND f.brand IN ('todas', t.brand))"""


def _items(records: list[asyncpg.Record]) -> list[ReportItem]:
    return [
        ReportItem(
            ticket_id=int(r["ticket_id"]), text=r["text"], count=int(r["n"]), brand=r["brand"]
        )
        for r in records
    ]


async def _grouped(
    conn: asyncpg.Connection,
    scope: Scope,
    start: datetime,
    end: datetime,
    *,
    extra: str = "",
    limit: int = TOP_LIMIT,
) -> list[ReportItem]:
    params: list[object] = [start, end]
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    sql = _GROUP_SQL.format(scope=scope_sql, extra=extra, key=_KEY, limit=limit)
    return _items(await conn.fetch(sql, *params))


async def frequent_questions(
    conn: asyncpg.Connection, scope: Scope, start: datetime, end: datetime
) -> list[ReportItem]:
    """Top 10 perguntas do periodo (mais citadas primeiro)."""
    return await _grouped(conn, scope, start, end)


async def objections(
    conn: asyncpg.Connection, scope: Scope, start: datetime, end: datetime
) -> list[ReportItem]:
    """Top 10 objecoes: perguntas classificadas como ARGUMENTO."""
    return await _grouped(conn, scope, start, end, extra=" AND t.classification = 'ARGUMENTO'")


async def unanswered_questions(
    conn: asyncpg.Connection, scope: Scope, start: datetime, end: datetime
) -> list[ReportItem]:
    """Perguntas aguardando resposta e ausentes da FAQ ativa (candidatas a "Criar na FAQ")."""
    return await _grouped(
        conn, scope, start, end, extra=_UNANSWERED_EXTRA, limit=UNANSWERED_LIMIT
    )


async def field_report(
    conn: asyncpg.Connection, scope: Scope, *, now: datetime, days: int = DEFAULT_DAYS
) -> FieldReport:
    """As tres listas da tela Relatorios para os ``days`` dias ate ``now``."""
    start, end = now - timedelta(days=days), now + timedelta(microseconds=1)
    return FieldReport(
        days=days,
        since=start,
        top_questions=await frequent_questions(conn, scope, start, end),
        objections=await objections(conn, scope, start, end),
        unanswered=await unanswered_questions(conn, scope, start, end),
    )


_SUMMARY_SQL = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE t.status = 'answered_bot') AS answered_bot,
       count(*) FILTER (WHERE t.status = 'answered_human') AS answered_human,
       count(*) FILTER (WHERE t.status = 'awaiting') AS awaiting,
       count(*) FILTER (WHERE t.status = 'send_error') AS send_error,
       count(*) FILTER (WHERE t.escalated) AS escalated,
       percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM t.answered_at - t.created_at)
       ) FILTER (WHERE t.answered_at IS NOT NULL) AS p50,
       percentile_cont(0.95) WITHIN GROUP (
         ORDER BY extract(epoch FROM t.answered_at - t.created_at)
       ) FILTER (WHERE t.answered_at IS NOT NULL) AS p95
  FROM ops.v_tickets t
 WHERE t.created_at >= $1 AND t.created_at < $2 AND {scope}
"""

_BREAKDOWN_SQL = """
SELECT t.{col} AS k, count(*) AS n
  FROM ops.v_tickets t
 WHERE t.created_at >= $1 AND t.created_at < $2 AND {scope}
 GROUP BY t.{col}
 ORDER BY n DESC, k NULLS LAST
"""


async def _breakdown(
    conn: asyncpg.Connection, scope: Scope, start: datetime, end: datetime, col: str
) -> list[asyncpg.Record]:
    params: list[object] = [start, end]
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    records = await conn.fetch(_BREAKDOWN_SQL.format(col=col, scope=scope_sql), *params)
    return list(records)


async def monthly_report(conn: asyncpg.Connection, scope: Scope, month: str) -> MonthlyReport:
    """Dados do relatorio do mes ``YYYY-MM`` (use ``parse_month`` antes). Mes sem
    tickets devolve zeros, listas vazias e tempos ``None``."""
    start, end = month_bounds(month)
    params: list[object] = [start, end]
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    row = await conn.fetchrow(_SUMMARY_SQL.format(scope=scope_sql), *params)
    if row is None:  # agregacao sem GROUP BY sempre devolve uma linha
        raise RuntimeError("consulta do relatorio mensal sem resultado")
    by_brand = [(r["k"], int(r["n"])) for r in await _breakdown(conn, scope, start, end, "brand")]
    by_class = [
        (r["k"], int(r["n"])) for r in await _breakdown(conn, scope, start, end, "classification")
    ]
    return MonthlyReport(
        month=month,
        start=start,
        end=end,
        total=int(row["total"]),
        answered_bot=int(row["answered_bot"]),
        answered_human=int(row["answered_human"]),
        awaiting=int(row["awaiting"]),
        send_error=int(row["send_error"]),
        escalated=int(row["escalated"]),
        by_brand=by_brand,
        by_classification=by_class,
        top_questions=await frequent_questions(conn, scope, start, end),
        response_p50_seconds=None if row["p50"] is None else float(row["p50"]),
        response_p95_seconds=None if row["p95"] is None else float(row["p95"]),
    )
