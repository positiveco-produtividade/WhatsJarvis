"""Leitura de Pessoas do painel (PN-31, PN-37, PN-67; spec 4.8).

Tres abas: **Autorizadas** (``active|pending_optin``), **Pedindo acesso**
(``access.access_attempts`` sem usuario e nao dispensadas) e **Retiradas**
(``revoked|declined``). Busca unica sem acento: nome/empresa (``search_key``, migration 104),
UF (consulta de 2 letras, ``= ANY(ufs)``) e telefone (digitos contra ``phone_key``, mais a
``match_key`` de ``phone.normalize_phone`` quando o numero e completo: cobre o 9o digito).
50 por pagina, "X de Y". Telefone sempre mascarado aqui (completo so na ficha).

Primeira mensagem de desconhecido (PN-37): ``access.unknown_first_messages`` so e lida por
``pending_requests`` e so quando ``unknown_first_text_display`` esta ligada. Nenhuma outra
funcao deste modulo (nem outro modulo do painel) referencia essa tabela. O texto nunca vai a
log nem a excecao. Numero desconhecido nao gera alerta nem e-mail (PN-67): este modulo so le.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg

from app.panel.services.formatting import digits_only, fold_text
from app.panel.services.mask import mask_phone_display
from app.services.flags import Flags
from app.services.phone import InvalidPhoneError, normalize_phone

__all__ = [
    "HIGHLIGHT_ATTEMPTS",
    "PAGE_SIZE",
    "UNKNOWN_FIRST_TEXT_KEY",
    "PendingPage",
    "PendingRow",
    "PeopleCounts",
    "PeoplePage",
    "PersonRow",
    "Tab",
    "parse_tab",
    "pending_requests",
    "people_counts",
    "people_page",
]

Tab = Literal["autorizadas", "pedindo", "retiradas"]
PAGE_SIZE = 50
HIGHLIGHT_ATTEMPTS = 3
UNKNOWN_FIRST_TEXT_KEY = "unknown_first_text_display"

_AUTHORIZED = ("active", "pending_optin")
_REMOVED = ("revoked", "declined")
_PHONE_QUERY = re.compile(r"[\d\s()+\-.]+")
_UF_QUERY = re.compile(r"[A-Za-z]{2}")
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


@dataclass(frozen=True, slots=True)
class PeopleCounts:
    """Contagem por aba, ja respeitando a busca corrente."""

    autorizadas: int
    pedindo: int
    retiradas: int


@dataclass(frozen=True, slots=True)
class PersonRow:
    id: int
    name: str
    initial: str
    profile: str
    company: str
    ufs: list[str]
    brands: list[str]
    phone_masked: str
    status: str
    status_changed_at: datetime
    revoked_reason: str | None


@dataclass(frozen=True, slots=True)
class PendingRow:
    """Tentativa de acesso. ``first_text`` e ``None`` com a exibicao desligada ou em midia."""

    id: int
    phone_masked: str
    reason: str
    attempt_count: int
    highlight: bool
    first_attempt_at: datetime
    last_attempt_at: datetime
    first_msg_type: str
    first_msg_chars: int | None
    first_msg_mime: str | None
    first_text: str | None


@dataclass(frozen=True, slots=True)
class PeoplePage:
    """Pagina das abas Autorizadas/Retiradas."""

    tab: Tab
    rows: list[PersonRow]
    total: int
    page: int
    counts: PeopleCounts
    page_size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


@dataclass(frozen=True, slots=True)
class PendingPage:
    """Pagina da aba Pedindo acesso; ``text_display`` informa se o texto esta liberado."""

    rows: list[PendingRow]
    total: int
    page: int
    text_display: bool
    counts: PeopleCounts
    tab: Tab = "pedindo"
    page_size: int = PAGE_SIZE

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.page_size))


def parse_tab(raw: str | None) -> Tab:
    """Aba valida ou ``autorizadas``."""
    if raw == "pedindo":
        return "pedindo"
    return "retiradas" if raw == "retiradas" else "autorizadas"


def _clean_query(q: str | None) -> str:
    return (q or "").strip()[:200]


def _phone_terms(q: str, params: list[object], column: str) -> list[str]:
    """Condicoes de telefone para ``column`` (so se a busca parece um numero)."""
    digits = digits_only(q)
    if not digits or not _PHONE_QUERY.fullmatch(q):
        return []
    params.append("%" + digits + "%")
    terms = [f"{column} LIKE ${len(params)}"]
    try:
        key = normalize_phone(q).match_key
    except InvalidPhoneError:
        return terms
    params.append(key)
    terms.append(f"{column} = ${len(params)}")
    return terms


# Quem teve os dados excluidos (LGPD, `anon:<id>`) fica so no banco, para o historico de
# conversas, tickets e auditoria; nao aparece em nenhuma aba nem contagem.
_NOT_ANONYMIZED = "u.phone_key NOT LIKE 'anon:%'"


def _users_search(q: str, params: list[object]) -> str:
    if not q:
        return _NOT_ANONYMIZED
    params.append("%" + fold_text(q).translate(_LIKE_ESCAPE) + "%")
    terms = [f"u.search_key LIKE ${len(params)} ESCAPE '\\'"]
    if _UF_QUERY.fullmatch(q):
        params.append(q.upper())
        terms.append(f"${len(params)} = ANY(u.ufs)")
    terms.extend(_phone_terms(q, params, "u.phone_key"))
    return f"{_NOT_ANONYMIZED} AND (" + " OR ".join(terms) + ")"


def _attempts_search(q: str, params: list[object]) -> str:
    """Tentativas so tem telefone: texto que nao e numero nao casa com nada."""
    if not q:
        return "TRUE"
    terms = _phone_terms(q, params, "a.phone_key")
    return "(" + " OR ".join(terms) + ")" if terms else "FALSE"


# Numero ja cadastrado (mesmo phone_key) nao e mais pedido de acesso, mesmo cadastrado pelo
# "+ Cadastrar" sem passar pelo Autorizar.
_PENDING_BASE = (
    "a.user_id IS NULL AND a.dismissed_at IS NULL AND NOT EXISTS "
    "(SELECT 1 FROM access.users u2 WHERE u2.phone_key = a.phone_key)"
)


async def people_counts(conn: asyncpg.Connection, q: str | None = None) -> PeopleCounts:
    """Autorizadas/Retiradas numa consulta e Pedindo acesso noutra (busca aplicada)."""
    query = _clean_query(q)
    up: list[object] = [list(_AUTHORIZED), list(_REMOVED)]
    users_sql = (
        "SELECT count(*) FILTER (WHERE u.status = ANY($1::text[])) AS authorized, "
        "count(*) FILTER (WHERE u.status = ANY($2::text[])) AS removed "
        f"FROM access.users u WHERE {_users_search(query, up)}"
    )
    row = await conn.fetchrow(users_sql, *up)
    ap: list[object] = []
    pending_sql = (
        f"SELECT count(*) FROM access.access_attempts a WHERE {_PENDING_BASE} "
        f"AND {_attempts_search(query, ap)}"
    )
    pending = await conn.fetchval(pending_sql, *ap)
    if row is None:  # agregacao sem GROUP BY sempre devolve uma linha
        raise RuntimeError("contagem de pessoas sem resultado")
    return PeopleCounts(
        autorizadas=int(row["authorized"]), pedindo=int(pending), retiradas=int(row["removed"])
    )


_USERS_SQL = """
SELECT u.id, u.name, u.profile, u.company, u.ufs, u.brands, u.phone_e164, u.status,
       u.status_changed_at, u.revoked_reason, count(*) OVER () AS total_rows
  FROM access.users u
 WHERE u.status = ANY($1::text[]) AND {search}
 ORDER BY {order}
 LIMIT {limit} OFFSET {offset}
"""
_ORDER = {
    "autorizadas": "u.search_key, u.id",
    "retiradas": "u.status_changed_at DESC, u.id DESC",
}


def _person(r: asyncpg.Record) -> PersonRow:
    name = str(r["name"])
    return PersonRow(
        id=int(r["id"]),
        name=name,
        initial=name.strip()[:1].upper() or "?",
        profile=r["profile"],
        company=r["company"],
        ufs=list(r["ufs"]),
        brands=list(r["brands"]),
        phone_masked=mask_phone_display(r["phone_e164"]),
        status=r["status"],
        status_changed_at=r["status_changed_at"],
        revoked_reason=r["revoked_reason"],
    )


async def people_page(
    conn: asyncpg.Connection,
    tab: Literal["autorizadas", "retiradas"],
    *,
    q: str | None = None,
    page: int = 1,
) -> PeoplePage:
    """Uma pagina (50) das abas Autorizadas ou Retiradas, com contagens das tres abas."""
    query = _clean_query(q)
    number = max(1, page)
    statuses = _AUTHORIZED if tab == "autorizadas" else _REMOVED
    params: list[object] = [list(statuses)]
    search = _users_search(query, params)
    params.extend([PAGE_SIZE, (number - 1) * PAGE_SIZE])
    sql = _USERS_SQL.format(
        search=search,
        order=_ORDER[tab],
        limit=f"${len(params) - 1}",
        offset=f"${len(params)}",
    )
    records = await conn.fetch(sql, *params)
    counts = await people_counts(conn, query)
    total = int(records[0]["total_rows"]) if records else 0
    if not records and number > 1:  # pagina alem do fim: o total vem da contagem
        total = counts.autorizadas if tab == "autorizadas" else counts.retiradas
    return PeoplePage(
        tab=tab, rows=[_person(r) for r in records], total=total, page=number, counts=counts
    )


_PENDING_SQL = """
SELECT a.id, a.phone_key, a.reason, a.attempt_count, a.first_attempt_at, a.last_attempt_at,
       a.first_msg_type, a.first_msg_chars, a.first_msg_mime, {text} AS first_text,
       count(*) OVER () AS total_rows
  FROM access.access_attempts a
  {join}
 WHERE {where}
 ORDER BY a.last_attempt_at DESC, a.id DESC
 LIMIT {limit} OFFSET {offset}
"""
_TEXT_JOIN = "LEFT JOIN access.unknown_first_messages m ON m.attempt_id = a.id"


def _pending(r: asyncpg.Record) -> PendingRow:
    count = int(r["attempt_count"])
    phone = r["phone_key"]
    return PendingRow(
        id=int(r["id"]),
        phone_masked=mask_phone_display(phone),
        reason=r["reason"],
        attempt_count=count,
        highlight=count >= HIGHLIGHT_ATTEMPTS,
        first_attempt_at=r["first_attempt_at"],
        last_attempt_at=r["last_attempt_at"],
        first_msg_type=r["first_msg_type"],
        first_msg_chars=r["first_msg_chars"],
        first_msg_mime=r["first_msg_mime"],
        first_text=r["first_text"],
    )


async def pending_requests(
    conn: asyncpg.Connection, flags: Flags, *, q: str | None = None, page: int = 1
) -> PendingPage:
    """Aba Pedindo acesso. UNICO lugar que le ``unknown_first_messages`` (PN-37).

    O texto so e consultado com ``unknown_first_text_display`` ligada (lida por ``conn``,
    sem pedir outra conexao ao pool). Desligada: so tipo, contagem e datas.
    """
    query = _clean_query(q)
    number = max(1, page)
    display = await flags.get_bool(UNKNOWN_FIRST_TEXT_KEY, False, conn=conn)
    params: list[object] = []
    where = f"{_PENDING_BASE} AND {_attempts_search(query, params)}"
    params.extend([PAGE_SIZE, (number - 1) * PAGE_SIZE])
    sql = _PENDING_SQL.format(
        text="m.body" if display else "NULL::text",
        join=_TEXT_JOIN if display else "",
        where=where,
        limit=f"${len(params) - 1}",
        offset=f"${len(params)}",
    )
    records = await conn.fetch(sql, *params)
    counts = await people_counts(conn, query)
    total = int(records[0]["total_rows"]) if records else (counts.pedindo if number > 1 else 0)
    return PendingPage(
        rows=[_pending(r) for r in records],
        total=total,
        page=number,
        text_display=display,
        counts=counts,
    )
