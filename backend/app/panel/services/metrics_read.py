"""Consultas da tela Metricas do painel (PN-41, PN-43; spec 4.6 e 5).

Somente leitura. Entrega, para uma marca e um periodo (``hoje``, ``7`` ou ``30`` dias):
KPIs, mensagens por dia, tickets por marca, tempo de resposta (p50/p95) e bloqueios, com
cache em memoria de 60 s por (marca, periodo). Pessoas e Tickets nunca passam por aqui.

Regras:
- dias sao dias de Sao Paulo; o periodo inclui o dia corrente (``hoje`` = 1 dia);
- mensagens e bloqueios vem de ``chat.message_trace``. O rastro nao tem marca: com uma
  marca em foco contam as mensagens de usuarios que atendem a marca (``access.users.brands``);
  numeros desconhecidos (sem usuario) e assinatura invalida (sem marca) so entram em Todas;
- "por marca" conta tickets com marca (``ops.v_tickets``); midia sem marca fica de fora;
- nenhum denominador zero: percentuais e tempos sem base viram ``None`` (PN-43);
- "avaliados como uteis" = up/(up+down) de ``chat.answer_ratings`` avaliadas no periodo, com a
  marca do ticket (``ops.v_tickets``); sem avaliacao => ``None`` => "sem avaliacoes". Cobertura
  = avaliadas / respostas enviadas com botoes (``chat.outbound_messages`` kind
  ``brain_answer_rate``; marca pelo ``chat.brain_runs`` do mesmo rastro) (CB-26);
- valores sempre por parametros posicionais; nenhum texto de mensagem sai do banco.

Uma conexao do pool por carga, usada em sequencia e devolvida antes de qualquer outra.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from time import monotonic

import asyncpg

from app.panel.services.formatting import SP_TZ
from app.panel.services.scope import Scope

__all__ = [
    "CACHE_TTL_SECONDS",
    "DEFAULT_PERIOD",
    "NO_RATINGS",
    "PERIODS",
    "TARGET_P95_SECONDS",
    "BlockCounts",
    "BrandShare",
    "DayCount",
    "MetricsCache",
    "MetricsKpis",
    "MetricsSnapshot",
    "Period",
    "ResponseTimes",
    "load_metrics",
    "metrics_snapshot",
    "parse_period",
]

CACHE_TTL_SECONDS = 60.0
NO_RATINGS = "sem avaliações"
TARGET_P95_SECONDS = 8.0
DEFAULT_PERIOD = "30"
PERIODS: dict[str, int] = {"hoje": 1, "7": 7, "30": 30}

_RATE_KIND = "brain_answer_rate"
_BLOCK_UNKNOWN = "unknown"
_BLOCK_RATE = "rate_limited"
_SIGNATURE_KIND = "invalid_signature"


@dataclass(frozen=True, slots=True)
class Period:
    """Periodo escolhido: ``key`` da query string e ``days`` (inclui o dia corrente)."""

    key: str
    days: int


@dataclass(frozen=True, slots=True)
class MetricsKpis:
    active_users: int  # usuarios ativos que mandaram mensagem no periodo
    registered_users: int  # usuarios ativos cadastrados (denominador)
    active_users_pct: float | None
    tickets: int
    tickets_previous: int
    tickets_change_pct: float | None  # vs periodo anterior; None sem base
    resolved_without_escalation_pct: float | None
    useful_pct: float | None = None  # up/(up+down); None sem avaliacao (CB-26)
    rated_count: int = 0  # avaliacoes (up + down) no periodo
    answers_with_buttons: int = 0  # respostas enviadas com botoes no periodo
    coverage_pct: float | None = None  # avaliadas / respostas com botoes; None sem base

    @property
    def has_ratings(self) -> bool:
        return self.useful_pct is not None

    @property
    def useful_label(self) -> str:
        return NO_RATINGS if self.useful_pct is None else f"{self.useful_pct:.0f}%"


@dataclass(frozen=True, slots=True)
class DayCount:
    day: date
    count: int


@dataclass(frozen=True, slots=True)
class BrandShare:
    brand: str
    tickets: int
    pct: float  # 0-100 do total com marca


@dataclass(frozen=True, slots=True)
class ResponseTimes:
    p50_seconds: float | None  # latencia do Jarvis (mensagem -> resposta)
    p95_seconds: float | None
    human_median_seconds: float | None  # escalados respondidos pela equipe

    @property
    def p95_within_target(self) -> bool | None:
        if self.p95_seconds is None:
            return None
        return self.p95_seconds <= TARGET_P95_SECONDS


@dataclass(frozen=True, slots=True)
class BlockCounts:
    unknown_numbers: int
    rate_limited: int
    invalid_signature: int | None  # None quando ha marca em foco (contador sem marca)

    @property
    def total(self) -> int:
        return self.unknown_numbers + self.rate_limited + (self.invalid_signature or 0)


@dataclass(frozen=True, slots=True)
class MetricsSnapshot:
    brand: str | None
    period: Period
    since: datetime
    kpis: MetricsKpis
    messages_per_day: list[DayCount]
    by_brand: list[BrandShare]
    response: ResponseTimes
    blocks: BlockCounts

    @property
    def total_messages(self) -> int:
        return sum(d.count for d in self.messages_per_day)

    @property
    def is_empty(self) -> bool:
        """Sem mensagem nem ticket no periodo: a tela mostra o estado vazio, sem grafico."""
        return self.total_messages == 0 and self.kpis.tickets == 0


def parse_period(raw: str | int | None) -> Period:
    """Valor invalido vira o padrao (nunca erro)."""
    key = str(raw) if raw is not None else DEFAULT_PERIOD
    if key not in PERIODS:
        key = DEFAULT_PERIOD
    return Period(key=key, days=PERIODS[key])


def _pct(part: int, whole: int) -> float | None:
    return None if whole <= 0 else round(100.0 * part / whole, 1)


def _change_pct(current: int, previous: int) -> float | None:
    return None if previous <= 0 else round(100.0 * (current - previous) / previous, 1)


def _seconds(value: object) -> float | None:
    return None if value is None else float(value)  # type: ignore[arg-type]


def _bounds(period: Period, now: datetime) -> tuple[datetime, datetime, datetime]:
    """(inicio anterior, inicio, fim) em UTC; inicio = 00:00 de SP do primeiro dia."""
    first = now.astimezone(SP_TZ).date() - timedelta(days=period.days - 1)
    start = datetime(first.year, first.month, first.day, tzinfo=SP_TZ).astimezone(UTC)
    previous = (
        datetime.combine(first - timedelta(days=period.days), datetime.min.time(), tzinfo=SP_TZ)
    ).astimezone(UTC)
    return previous, start, now + timedelta(microseconds=1)


_BRAND_OF_USER = (
    "($3::text IS NULL OR EXISTS (SELECT 1 FROM access.users u "
    "WHERE u.id = m.user_id AND $3::text = ANY (u.brands)))"
)

_USERS_SQL = """
SELECT (SELECT count(*) FROM access.users u
         WHERE u.status = 'active' AND ($1::text IS NULL OR $1::text = ANY (u.brands)))
         AS registered,
       (SELECT count(DISTINCT m.user_id) FROM chat.message_trace m
          JOIN access.users u ON u.id = m.user_id AND u.status = 'active'
         WHERE m.received_at >= $2 AND m.received_at < $3
           AND ($1::text IS NULL OR $1::text = ANY (u.brands))) AS active_in_period
"""

_TICKETS_SQL = """
SELECT count(*) FILTER (WHERE t.created_at >= $2) AS total,
       count(*) FILTER (WHERE t.created_at >= $2 AND NOT t.escalated) AS not_escalated,
       count(*) FILTER (WHERE t.created_at < $2) AS previous,
       percentile_cont(0.5) WITHIN GROUP (
         ORDER BY extract(epoch FROM t.answered_at - t.created_at)
       ) FILTER (WHERE t.created_at >= $2 AND t.escalated AND t.status = 'answered_human'
                   AND t.answered_at IS NOT NULL) AS human_p50
  FROM ops.v_tickets t
 WHERE t.created_at >= $1 AND t.created_at < $3 AND {scope}
"""

_BY_BRAND_SQL = """
SELECT t.brand AS brand, count(*) AS n
  FROM ops.v_tickets t
 WHERE t.created_at >= $1 AND t.created_at < $2 AND t.brand IS NOT NULL AND {scope}
 GROUP BY t.brand
 ORDER BY n DESC, t.brand
"""

_RATINGS_SQL = """
SELECT count(*) FILTER (WHERE r.rating = 'up') AS up,
       count(*) FILTER (WHERE r.rating = 'down') AS down
  FROM chat.answer_ratings r
  JOIN ops.v_tickets t ON t.id = r.ticket_id
 WHERE r.rated_at >= $1 AND r.rated_at < $2 AND {scope}
"""

_RATE_ANSWERS_SQL = f"""
SELECT count(*) AS n
  FROM chat.outbound_messages o
  LEFT JOIN chat.brain_runs br ON br.trace_id = o.inbound_trace_id
 WHERE o.kind = '{_RATE_KIND}' AND o.status NOT IN ('failed', 'discarded')
   AND o.created_at >= $1 AND o.created_at < $2
   AND ($3::text IS NULL OR br.brand = $3::text)
"""  # noqa: S608

_DAILY_SQL = f"""
SELECT (m.received_at AT TIME ZONE 'America/Sao_Paulo')::date AS day, count(*) AS n
  FROM chat.message_trace m
 WHERE m.received_at >= $1 AND m.received_at < $2 AND {_BRAND_OF_USER}
 GROUP BY 1
"""  # noqa: S608

_TRACE_SQL = f"""
SELECT count(*) FILTER (WHERE m.access_decision = '{_BLOCK_UNKNOWN}') AS unknown_numbers,
       count(*) FILTER (WHERE m.access_decision = '{_BLOCK_RATE}') AS rate_limited,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY m.latency_ms)
         FILTER (WHERE m.latency_ms IS NOT NULL) / 1000.0 AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY m.latency_ms)
         FILTER (WHERE m.latency_ms IS NOT NULL) / 1000.0 AS p95
  FROM chat.message_trace m
 WHERE m.received_at >= $1 AND m.received_at < $2 AND {_BRAND_OF_USER}
"""  # noqa: S608

_SIGNATURE_SQL = (
    "SELECT COALESCE(sum(count), 0) FROM ops.security_counters WHERE kind = $1 AND day >= $2"
)


def _build_kpis(
    registered: int,
    active: int,
    tickets: asyncpg.Record,
    ratings: asyncpg.Record,
    answers_with_buttons: int,
) -> MetricsKpis:
    up, down = int(ratings["up"]), int(ratings["down"])
    total, previous = int(tickets["total"]), int(tickets["previous"])
    return MetricsKpis(
        active_users=active,
        registered_users=registered,
        active_users_pct=_pct(active, registered),
        tickets=total,
        tickets_previous=previous,
        tickets_change_pct=_change_pct(total, previous),
        resolved_without_escalation_pct=_pct(int(tickets["not_escalated"]), total),
        useful_pct=_pct(up, up + down),
        rated_count=up + down,
        answers_with_buttons=answers_with_buttons,
        coverage_pct=_pct(up + down, answers_with_buttons),
    )


def _fill_days(rows: list[asyncpg.Record], period: Period, now: datetime) -> list[DayCount]:
    counts = {r["day"]: int(r["n"]) for r in rows}
    last = now.astimezone(SP_TZ).date()
    days = [last - timedelta(days=i) for i in range(period.days - 1, -1, -1)]
    return [DayCount(day=d, count=counts.get(d, 0)) for d in days]


def _shares(rows: list[asyncpg.Record]) -> list[BrandShare]:
    total = sum(int(r["n"]) for r in rows)
    return [
        BrandShare(brand=r["brand"], tickets=int(r["n"]), pct=_pct(int(r["n"]), total) or 0.0)
        for r in rows
    ]


async def load_metrics(
    conn: asyncpg.Connection, scope: Scope, period: Period, *, now: datetime
) -> MetricsSnapshot:
    """Consulta o banco (sem cache) numa unica conexao, em sequencia."""
    previous, start, end = _bounds(period, now)
    brand = scope.brand

    users = await conn.fetchrow(_USERS_SQL, brand, start, end)
    if users is None:  # SELECT de subconsultas sempre devolve uma linha
        raise RuntimeError("consulta de usuarios das metricas sem resultado")

    params: list[object] = [previous, start, end]
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    tickets = await conn.fetchrow(_TICKETS_SQL.format(scope=scope_sql), *params)
    if tickets is None:
        raise RuntimeError("consulta de tickets das metricas sem resultado")

    brand_params: list[object] = [start, end]
    brand_scope = scope.sql("t.brand", brand_params, kind_column="t.kind")
    by_brand = await conn.fetch(_BY_BRAND_SQL.format(scope=brand_scope), *brand_params)

    rating_params: list[object] = [start, end]
    rating_scope = scope.sql("t.brand", rating_params, kind_column="t.kind")
    ratings = await conn.fetchrow(_RATINGS_SQL.format(scope=rating_scope), *rating_params)
    if ratings is None:
        raise RuntimeError("consulta de avaliacoes das metricas sem resultado")
    with_buttons = int(await conn.fetchval(_RATE_ANSWERS_SQL, start, end, brand))

    daily = await conn.fetch(_DAILY_SQL, start, end, brand)
    trace = await conn.fetchrow(_TRACE_SQL, start, end, brand)
    if trace is None:
        raise RuntimeError("consulta do rastro das metricas sem resultado")

    signature: int | None = None
    if brand is None:
        first_day = start.astimezone(SP_TZ).date()
        signature = int(await conn.fetchval(_SIGNATURE_SQL, _SIGNATURE_KIND, first_day))

    kpis = _build_kpis(
        int(users["registered"]), int(users["active_in_period"]), tickets, ratings, with_buttons
    )
    return MetricsSnapshot(
        brand=brand,
        period=period,
        since=start,
        kpis=kpis,
        messages_per_day=_fill_days(list(daily), period, now),
        by_brand=_shares(list(by_brand)),
        response=ResponseTimes(
            p50_seconds=_seconds(trace["p50"]),
            p95_seconds=_seconds(trace["p95"]),
            human_median_seconds=_seconds(tickets["human_p50"]),
        ),
        blocks=BlockCounts(
            unknown_numbers=int(trace["unknown_numbers"]),
            rate_limited=int(trace["rate_limited"]),
            invalid_signature=signature,
        ),
    )


class MetricsCache:
    """Cache em memoria por (marca, periodo) com validade fixa (padrao 60 s)."""

    def __init__(
        self, ttl: float = CACHE_TTL_SECONDS, clock: Callable[[], float] = monotonic
    ) -> None:
        self._ttl = ttl
        self._clock = clock
        self._items: dict[tuple[str, str], tuple[float, MetricsSnapshot]] = {}

    def get(self, key: tuple[str, str]) -> MetricsSnapshot | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        if self._clock() - hit[0] >= self._ttl:
            del self._items[key]
            return None
        return hit[1]

    def put(self, key: tuple[str, str], snapshot: MetricsSnapshot) -> None:
        self._items[key] = (self._clock(), snapshot)

    def clear(self) -> None:
        self._items.clear()


DEFAULT_CACHE = MetricsCache()


async def metrics_snapshot(
    pool: asyncpg.Pool,
    scope: Scope,
    period: Period,
    *,
    now: datetime,
    cache: MetricsCache | None = None,
) -> MetricsSnapshot:
    """Metricas da marca/periodo; usa o cache de 60 s. A conexao so e pega no miss."""
    store = DEFAULT_CACHE if cache is None else cache
    key = (scope.cookie_value, period.key)
    hit = store.get(key)
    if hit is not None:
        return hit
    async with pool.acquire() as conn:
        snapshot = await load_metrics(conn, scope, period, now=now)
    store.put(key, snapshot)
    return snapshot
