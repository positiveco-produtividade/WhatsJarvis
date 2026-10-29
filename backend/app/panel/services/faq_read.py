"""Leitura da FAQ para a tela do painel: lista com filtros e paginacao (PN-22, PN-43).

Le SOMENTE ``content.faq_items`` (a escrita fica em ``faq_admin.py``). Filtros: marca
(``todas`` mostra tudo; uma marca mostra a marca + os itens ``todas``), categoria, ativas ou
desativadas e busca sem acento em pergunta (``question_key``) e resposta. Itens ``todas`` vem
primeiro; 10 por pagina. Todos os valores entram por parametros posicionais.
"""

from dataclasses import dataclass
from datetime import datetime

import asyncpg

from app.panel.services.faq_validation import ALL_BRANDS_KEY, FAQ_CATEGORIES
from app.panel.services.formatting import fold_text
from app.panel.services.scope import Scope
from app.panel.values import BRANDS

__all__ = [
    "PAGE_SIZE",
    "FaqFilters",
    "FaqPage",
    "FaqRow",
    "active_count",
    "faq_page",
    "parse_faq_filters",
]

PAGE_SIZE = 10
MAX_QUERY_LENGTH = 200

# Mesmo mapeamento da coluna search_key dos tickets (migration 102), para a resposta.
_FROM = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
_TO = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
_ANSWER_KEY = f"lower(translate(coalesce(f.answer, ''), '{_FROM}', '{_TO}'))"


@dataclass(frozen=True, slots=True)
class FaqFilters:
    """Filtros da tela FAQ; ``brand='todas'`` = sem filtro de marca."""

    brand: str = ALL_BRANDS_KEY
    category: str | None = None
    inactive: bool = False
    q: str = ""
    page: int = 1

    @property
    def is_filtered(self) -> bool:
        """Busca ou categoria ativas (muda a mensagem de lista vazia)."""
        return bool(self.q or self.category)


@dataclass(frozen=True, slots=True)
class FaqRow:
    id: int
    number: int
    brand: str
    category: str
    question: str
    answer: str | None
    variations: tuple[str, ...]
    bot_responde: bool
    origin: str
    origin_ref: str | None
    version: int
    updated_at: datetime
    updated_by: str


@dataclass(frozen=True, slots=True)
class FaqPage:
    rows: list[FaqRow]
    total: int
    page: int
    page_size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


def parse_faq_filters(
    *,
    brand: str | None = None,
    category: str | None = None,
    inactive: str | None = None,
    q: str | None = None,
    page: str | int | None = None,
    default_brand: str = ALL_BRANDS_KEY,
) -> FaqFilters:
    """Normaliza a query string: valor invalido vira o padrao (nunca erro).

    ``brand`` ausente usa ``default_brand`` (marca em foco do cabecalho); presente e invalido
    vira ``todas``.
    """
    try:
        p = int(page) if page is not None else 1
    except ValueError:
        p = 1
    chosen = default_brand if brand is None else brand
    return FaqFilters(
        brand=chosen if chosen in BRANDS else ALL_BRANDS_KEY,
        category=category if category in FAQ_CATEGORIES else None,
        inactive=inactive in ("1", "true", "on"),
        q=(q or "").strip()[:MAX_QUERY_LENGTH],
        page=max(1, p),
    )


def _brand_scope(brand: str) -> Scope:
    return Scope(brand if brand in BRANDS else None)


def _like_pattern(q: str) -> str:
    """``%termo%`` sem acento/caixa, com ``\\``, ``%`` e ``_`` escapados."""
    term = fold_text(q).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{term}%"


def _where(f: FaqFilters, params: list[object]) -> str:
    params.append(not f.inactive)
    parts = [f"f.active = ${len(params)}", _brand_scope(f.brand).sql_faq("f.brand", params)]
    if f.category:
        params.append(f.category)
        parts.append(f"f.category = ${len(params)}")
    if f.q:
        params.append(_like_pattern(f.q))
        ph = f"${len(params)}"
        parts.append(
            f"(f.question_key LIKE {ph} ESCAPE '\\' OR {_ANSWER_KEY} LIKE {ph} ESCAPE '\\')"
        )
    return " AND ".join(parts)


_LIST_SQL = """
SELECT f.id, f.brand, f.category, f.question, f.answer, f.variations, f.bot_responde,
       f.origin, f.origin_ref, f.version, f.updated_at, f.updated_by
  FROM content.faq_items f
 WHERE {where}
 ORDER BY (f.brand = 'todas') DESC, f.id
 LIMIT {limit} OFFSET {offset}
"""


async def active_count(conn: asyncpg.Connection, brand: str) -> int:
    """Itens ativos visiveis na marca (a marca + ``todas``); sem outros filtros."""
    params: list[object] = []
    scope_sql = _brand_scope(brand).sql_faq("f.brand", params)
    sql = f"SELECT count(*) FROM content.faq_items f WHERE f.active AND {scope_sql}"  # noqa: S608
    return int(await conn.fetchval(sql, *params))


async def faq_page(conn: asyncpg.Connection, filters: FaqFilters) -> FaqPage:
    """Uma pagina (10) da interseccao dos filtros; pagina alem do fim vira a ultima."""
    params: list[object] = []
    where = _where(filters, params)
    count_sql = f"SELECT count(*) FROM content.faq_items f WHERE {where}"  # noqa: S608
    total = int(await conn.fetchval(count_sql, *params))
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(filters.page, pages)
    offset = (page - 1) * PAGE_SIZE
    params.extend([PAGE_SIZE, offset])
    sql = _LIST_SQL.format(where=where, limit=f"${len(params) - 1}", offset=f"${len(params)}")
    records = await conn.fetch(sql, *params)
    rows = [
        FaqRow(
            id=int(r["id"]),
            number=offset + i + 1,
            brand=r["brand"],
            category=r["category"],
            question=r["question"],
            answer=r["answer"],
            variations=tuple(r["variations"] or ()),
            bot_responde=bool(r["bot_responde"]),
            origin=r["origin"],
            origin_ref=r["origin_ref"],
            version=int(r["version"]),
            updated_at=r["updated_at"],
            updated_by=r["updated_by"],
        )
        for i, r in enumerate(records)
    ]
    return FaqPage(rows=rows, total=total, page=page)
