"""Leitura do Inbox e do chat (PN-10, PN-12, PN-6, PN-59; spec 4.3 e 4.6).

Somente leitura. A lista usa ``LATERAL`` sobre ``chat.message_trace`` (indice
``message_trace_user_time_idx``) e sobre ``ops.v_tickets``; o painel nunca le
``chat.tickets`` diretamente. O chat junta ``chat.message_trace`` (vendedor),
``chat.outbound_messages`` (Jarvis, rotulado "Bot") e as conclusoes humanas dos tickets
(rotuladas "Humano"), as 200 ultimas, em ordem cronologica.

Abas: **Conversas** = tickets de pergunta (``kind <> 'media'``); **Midias** = tickets de
midia. Um vendedor com os dois tipos aparece nas duas abas, cada uma contando so os seus
tickets. Escopo de marca (PN-6): a conversa entra se tiver ao menos um ticket da aba que
case com o escopo; midia sem marca casa com qualquer marca. Em Todas as marcas, a aba
Conversas tambem traz quem nao tem ticket algum (autorizados sem historico de midia).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import asyncpg

from app.panel.services.formatting import (
    day_label,
    digits_only,
    fold_text,
    format_time,
    is_new_day,
    responded_in,
    response_time_label,
)
from app.panel.services.mask import mask_phone_display
from app.panel.services.scope import MEDIA_KIND, Scope

__all__ = [
    "BOT_LABEL",
    "CHAT_LIMIT",
    "HUMAN_LABEL",
    "PAGE_SIZE",
    "RECENT_TICKETS",
    "Chat",
    "ChatEntry",
    "ConversationHeader",
    "ConversationRow",
    "InboxCounts",
    "InboxPage",
    "RecentTicket",
    "Tab",
    "get_chat",
    "inbox_counts",
    "inbox_page",
    "parse_tab",
]

Tab = Literal["conversas", "midias"]
PAGE_SIZE = 50
CHAT_LIMIT = 200
RECENT_TICKETS = 10
BOT_LABEL = "Bot"
HUMAN_LABEL = "Humano"
SELLER_LABEL = "Vendedor"

_PHONE_QUERY = re.compile(r"[\d\s()+\-]+")
_LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})
_MEDIA_PLACEHOLDER = {
    "audio": "[áudio]",
    "image": "[imagem]",
    "video": "[vídeo]",
    "document": "[documento]",
    "sticker": "[figurinha]",
}
_RATING_REASONS = frozenset({"rating_up", "rating_down"})
_RATING_LABEL = "[avaliação]"  # toque no botao de nota; opt-in e outros botoes seguem "[button]"
_BOT_FALLBACK = {"media_ack": "Recebi seu arquivo. A equipe foi avisada."}
_FOLD_FROM = "áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ"
_FOLD_TO = "aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC"
_NAME_KEY = f"lower(translate(u.name, '{_FOLD_FROM}', '{_FOLD_TO}'))"


@dataclass(frozen=True, slots=True)
class ConversationRow:
    user_id: int
    name: str
    initial: str
    status: str
    last_text: str | None
    last_at: datetime | None
    last_time: str
    classification: str | None
    awaiting_count: int
    awaiting_since: datetime | None
    awaiting_label: str | None


@dataclass(frozen=True, slots=True)
class InboxCounts:
    """Conversas aguardando por aba, dentro do escopo (sem busca)."""

    conversas: int
    midias: int


@dataclass(frozen=True, slots=True)
class InboxPage:
    tab: Tab
    rows: list[ConversationRow]
    total: int
    page: int
    page_size: int
    awaiting_only: bool
    counts: InboxCounts


@dataclass(frozen=True, slots=True)
class ConversationHeader:
    user_id: int
    name: str
    initial: str
    profile: str
    ufs: list[str]
    phone_masked: str
    status: str
    message_count: int
    last_at: datetime | None


@dataclass(frozen=True, slots=True)
class ChatEntry:
    """Uma bolha. ``role``: seller | bot | human; ``author`` e a legenda exibida."""

    role: str
    author: str
    at: datetime
    time: str
    body: str | None
    kind: str | None
    new_day: bool
    day: str
    responded_in: str | None


@dataclass(frozen=True, slots=True)
class RecentTicket:
    ticket_number: str
    kind: str
    brand: str | None
    classification: str
    status: str
    escalated: bool
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Chat:
    header: ConversationHeader
    entries: list[ChatEntry]
    recent_tickets: list[RecentTicket]
    awaiting_ticket_numbers: list[str]


def parse_tab(raw: str | None) -> Tab:
    """``midias`` ou ``conversas``; qualquer outro valor vira ``conversas``."""
    return "midias" if raw == "midias" else "conversas"


def _tab_kind_sql(tab: Tab, alias: str = "t") -> str:
    op = "=" if tab == "midias" else "<>"
    return f"{alias}.kind {op} '{MEDIA_KIND}'"


def _initial(name: str) -> str:
    return name.strip()[:1].upper() or "?"


def _trace_text(
    body: str | None, msg_type: str | None, reason: str | None = None
) -> str | None:
    if body:
        return body
    if msg_type == "button" and reason in _RATING_REASONS:
        return _RATING_LABEL
    return _MEDIA_PLACEHOLDER.get(msg_type or "", f"[{msg_type}]" if msg_type else None)


def _search_sql(query: str, params: list[object]) -> str:
    """Nome (sem acento/caixa) ou, se a busca e so numero, telefone por digitos."""
    q = query.strip()
    if not q:
        return "TRUE"
    params.append("%" + fold_text(q).translate(_LIKE_ESCAPE) + "%")
    parts = [f"{_NAME_KEY} LIKE ${len(params)} ESCAPE '\\'"]
    digits = digits_only(q)
    if digits and _PHONE_QUERY.fullmatch(q):
        params.append("%" + digits + "%")
        parts.append(f"u.phone_key LIKE ${len(params)}")
    return "(" + " OR ".join(parts) + ")"


def _eligibility_sql(tab: Tab, scope: Scope) -> str:
    """Quem entra na aba (a linha ``lt`` ja traz o ultimo ticket da aba e do escopo)."""
    if tab == "conversas" and scope.is_all:
        return (
            "(lt.ticket_number IS NOT NULL OR NOT EXISTS (SELECT 1 FROM ops.v_tickets m "
            f"WHERE m.user_id = u.id AND m.kind = '{MEDIA_KIND}'))"
        )
    return "lt.ticket_number IS NOT NULL"


def _history_sql() -> str:
    """Usuarios cadastrados: autorizados/em aceite, ou retirados com historico (PN-10)."""
    return (
        "(u.status IN ('active', 'pending_optin') "
        "OR EXISTS (SELECT 1 FROM chat.message_trace h WHERE h.user_id = u.id) "
        "OR EXISTS (SELECT 1 FROM ops.v_tickets h WHERE h.user_id = u.id))"
    )


def _row(rec: asyncpg.Record, now: datetime) -> ConversationRow:
    since: datetime | None = rec["awaiting_since"]
    last_at: datetime | None = rec["last_at"]
    return ConversationRow(
        user_id=int(rec["id"]),
        name=rec["name"],
        initial=_initial(rec["name"]),
        status=rec["status"],
        last_text=_trace_text(rec["last_body"], rec["last_type"], rec["last_reason"]),
        last_at=last_at,
        last_time=format_time(last_at),
        classification=rec["classification"],
        awaiting_count=int(rec["awaiting_count"]),
        awaiting_since=since,
        awaiting_label=(
            response_time_label((now - since).total_seconds()) if since is not None else None
        ),
    )


async def _fetch_rows(
    conn: asyncpg.Connection,
    scope: Scope,
    tab: Tab,
    query: str,
    awaiting_only: bool,
    page: int,
) -> list[asyncpg.Record]:
    params: list[object] = []
    scope_sql = scope.sql("t.brand", params, kind_column="t.kind")
    search = _search_sql(query, params)
    params.extend([PAGE_SIZE, (page - 1) * PAGE_SIZE])
    limit_ph, offset_ph = f"${len(params) - 1}", f"${len(params)}"
    tab_kind = _tab_kind_sql(tab)
    only_awaiting = "coalesce(ag.n, 0) > 0" if awaiting_only else "TRUE"
    sql = f"""
        SELECT u.id, u.name, u.status,
               lm.body AS last_body, lm.msg_type AS last_type,
               lm.decision_reason AS last_reason, lm.received_at AS last_at,
               lt.classification, coalesce(ag.n, 0) AS awaiting_count,
               ag.since AS awaiting_since, count(*) OVER () AS total
        FROM access.users u
        LEFT JOIN LATERAL (
            SELECT m.body, m.msg_type, m.decision_reason, m.received_at FROM chat.message_trace m
            WHERE m.user_id = u.id ORDER BY m.received_at DESC LIMIT 1) lm ON true
        LEFT JOIN LATERAL (
            SELECT t.classification, t.ticket_number FROM ops.v_tickets t
            WHERE t.user_id = u.id AND {tab_kind} AND {scope_sql}
            ORDER BY t.created_at DESC LIMIT 1) lt ON true
        LEFT JOIN LATERAL (
            SELECT count(*) AS n, min(t.created_at) AS since FROM ops.v_tickets t
            WHERE t.user_id = u.id AND {tab_kind} AND {scope_sql}
              AND t.status = 'awaiting') ag ON true
        WHERE {_history_sql()} AND {_eligibility_sql(tab, scope)} AND {search}
          AND {only_awaiting}
        ORDER BY coalesce(lm.received_at, u.status_changed_at) DESC, u.id DESC
        LIMIT {limit_ph} OFFSET {offset_ph}
    """  # noqa: S608 - so identificadores fixos e placeholders; valores em params
    return list(await conn.fetch(sql, *params))


async def inbox_page(
    conn: asyncpg.Connection,
    scope: Scope,
    *,
    tab: Tab = "conversas",
    query: str = "",
    awaiting_only: bool = False,
    page: int = 1,
    now: datetime,
) -> InboxPage:
    """Lista paginada (50) de conversas; mais recentes primeiro."""
    page = max(1, page)
    records = await _fetch_rows(conn, scope, tab, query, awaiting_only, page)
    if records:
        total = int(records[0]["total"])
    elif page > 1:  # pagina alem do fim: o total vem da primeira pagina
        first = await _fetch_rows(conn, scope, tab, query, awaiting_only, 1)
        total = int(first[0]["total"]) if first else 0
    else:
        total = 0
    return InboxPage(
        tab=tab,
        rows=[_row(r, now) for r in records],
        total=total,
        page=page,
        page_size=PAGE_SIZE,
        awaiting_only=awaiting_only,
        counts=await inbox_counts(conn, scope),
    )


async def inbox_counts(conn: asyncpg.Connection, scope: Scope) -> InboxCounts:
    """Conversas aguardando por aba (PN-11: contagem separada por aba), numa consulta."""
    params: list[object] = []
    scope_sql = scope.sql("brand", params)
    rec = await conn.fetchrow(
        f"""
        SELECT count(DISTINCT user_id) FILTER (WHERE kind <> '{MEDIA_KIND}') AS conversas,
               count(DISTINCT user_id) FILTER (WHERE kind = '{MEDIA_KIND}') AS midias
        FROM ops.v_tickets WHERE status = 'awaiting' AND {scope_sql}
        """,  # noqa: S608
        *params,
    )
    assert rec is not None
    return InboxCounts(conversas=int(rec["conversas"]), midias=int(rec["midias"]))


# ---------------------------------------------------------------- chat


_CHAT_SQL = """
    (SELECT 'seller' AS role, m.received_at AS at, m.body, m.msg_type AS kind, 0 AS rank,
            m.decision_reason AS reason
       FROM chat.message_trace m WHERE m.user_id = $1
       ORDER BY m.received_at DESC LIMIT $2)
    UNION ALL
    (SELECT 'bot', coalesce(o.sent_at, o.created_at), o.body, o.kind, 1, NULL
       FROM chat.outbound_messages o WHERE o.user_id = $1 AND o.status <> 'discarded'
       ORDER BY coalesce(o.sent_at, o.created_at) DESC LIMIT $2)
    UNION ALL
    (SELECT 'human', t.answered_at, NULL, t.answered_by, 2, NULL
       FROM ops.v_tickets t WHERE t.user_id = $1 AND t.status = 'answered_human'
        AND t.answered_at IS NOT NULL
       ORDER BY t.answered_at DESC LIMIT $2)
"""


def _build_entries(records: list[asyncpg.Record], now: datetime) -> list[ChatEntry]:
    ordered = sorted(records, key=lambda r: (r["at"], r["rank"]))[-CHAT_LIMIT:]
    entries: list[ChatEntry] = []
    previous_at: datetime | None = None
    last_question_at: datetime | None = None
    for rec in ordered:
        at: datetime = rec["at"]
        role: str = rec["role"]
        text, author, reply = _entry_parts(rec, role)
        if role == "seller":
            last_question_at = at
        entries.append(
            ChatEntry(
                role=role,
                author=author,
                at=at,
                time=format_time(at),
                body=text,
                kind=reply,
                new_day=is_new_day(previous_at, at),
                day=day_label(at, now),
                responded_in=(
                    responded_in(last_question_at, at)
                    if role != "seller" and last_question_at is not None
                    else None
                ),
            )
        )
        previous_at = at
    return entries


def _entry_parts(rec: asyncpg.Record, role: str) -> tuple[str | None, str, str | None]:
    """(texto, legenda, tipo). Resposta do bot sem corpo usa o texto padrao do tipo."""
    if role == "seller":
        return _trace_text(rec["body"], rec["kind"], rec["reason"]), SELLER_LABEL, rec["kind"]
    if role == "bot":
        return rec["body"] or _BOT_FALLBACK.get(rec["kind"]), BOT_LABEL, rec["kind"]
    return None, HUMAN_LABEL, rec["kind"]  # kind = quem concluiu (answered_by)


def _recent(rec: asyncpg.Record) -> RecentTicket:
    return RecentTicket(
        ticket_number=rec["ticket_number"],
        kind=rec["kind"],
        brand=rec["brand"],
        classification=rec["classification"],
        status=rec["status"],
        escalated=bool(rec["escalated"]),
        created_at=rec["created_at"],
    )


async def get_chat(
    conn: asyncpg.Connection, user_id: int, *, now: datetime
) -> Chat | None:
    """Chat do usuario (200 ultimas entradas) ou ``None`` se o usuario nao existe."""
    user = await conn.fetchrow(
        "SELECT id, name, profile, ufs, phone_e164, status FROM access.users WHERE id = $1",
        user_id,
    )
    if user is None:
        return None
    records = await conn.fetch(_CHAT_SQL, user_id, CHAT_LIMIT)
    tickets = await conn.fetch(
        "SELECT ticket_number, kind, brand, classification, status, escalated, created_at "
        "FROM ops.v_tickets WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
        user_id,
        RECENT_TICKETS,
    )
    awaiting = await conn.fetch(
        "SELECT ticket_number FROM ops.v_tickets WHERE user_id = $1 AND status = 'awaiting' "
        "ORDER BY created_at",
        user_id,
    )
    entries = _build_entries(list(records), now)
    seller_msgs = sum(1 for e in entries if e.role == "seller")
    last_at = max((e.at for e in entries if e.role == "seller"), default=None)
    header = ConversationHeader(
        user_id=int(user["id"]),
        name=user["name"],
        initial=_initial(user["name"]),
        profile=user["profile"],
        ufs=list(user["ufs"]),
        phone_masked=mask_phone_display(user["phone_e164"]),
        status=user["status"],
        message_count=seller_msgs,
        last_at=last_at,
    )
    return Chat(
        header=header,
        entries=entries,
        recent_tickets=[_recent(r) for r in tickets],
        awaiting_ticket_numbers=[r["ticket_number"] for r in awaiting],
    )

