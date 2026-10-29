"""Leitura da auditoria (``ops.audit_log``) para a aba Auditoria de Logs (PN-55; task PN-T59).

Somente leitura: a escrita e de ``app.services.audit.write`` (mesma transacao da acao, entao
falha de auditoria aborta a escrita) e a tabela e imutavel por trigger. Filtro por acao e por
alvo (tipo e, opcionalmente, id). Lista paginada no servidor, mais novos primeiro.

Sem PII na tela: os valores antes/depois ja sao ids e status; ainda assim, por defesa, chaves
que soam como dado pessoal viram ``[oculto]`` e qualquer sequencia que lembre telefone e
mascarada. Todos os valores entram por parametros posicionais.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg

from app.panel.services.mask import mask_phone_display

__all__ = [
    "PAGE_SIZE",
    "AuditFacets",
    "AuditFilters",
    "AuditPage",
    "AuditRow",
    "audit_page",
    "load_facets",
    "mask_phone_like",
    "parse_filters",
    "safe_text",
]

PAGE_SIZE = 50
MAX_FIELD = 80
MAX_VALUE_TEXT = 240
HIDDEN = "[oculto]"
EMPTY = "—"
_PII_KEY = re.compile(
    r"phone|telefone|fone|celular|whats|bsuid|name|nome|body|text|texto|message|mensagem"
    r"|email|e_mail|cpf|summary|resumo",
    re.IGNORECASE,
)
_PHONE_LIKE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")
_MIN_PHONE_DIGITS = 8


@dataclass(frozen=True, slots=True)
class AuditFilters:
    """Filtros da aba; vazio = sem filtro naquele campo."""

    action: str = ""
    target_type: str = ""
    target_id: str = ""
    page: int = 1


@dataclass(frozen=True, slots=True)
class AuditRow:
    id: int
    at: datetime
    actor: str
    actor_type: str
    action: str
    target_type: str
    target_id: str
    old: str
    new: str


@dataclass(frozen=True, slots=True)
class AuditPage:
    rows: list[AuditRow]
    total: int
    page: int
    pages: int


@dataclass(frozen=True, slots=True)
class AuditFacets:
    """Valores existentes para os seletores de filtro."""

    actions: list[str]
    target_types: list[str]


def _clean(raw: str | None) -> str:
    return (raw or "").strip()[:MAX_FIELD]


def parse_filters(
    *,
    action: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    page: str | int | None = None,
) -> AuditFilters:
    """Normaliza a query string: valor invalido vira o padrao (nunca erro)."""
    try:
        number = int(page) if page is not None else 1
    except ValueError:
        number = 1
    return AuditFilters(
        action=_clean(action),
        target_type=_clean(target_type),
        target_id=_clean(target_id),
        page=max(1, number),
    )


def _mask_match(match: re.Match[str]) -> str:
    text = match.group(0)
    if sum(ch.isdigit() for ch in text) < _MIN_PHONE_DIGITS:
        return text
    return mask_phone_display(text)


def mask_phone_like(text: str) -> str:
    """Mascara trechos que lembram telefone (8+ digitos, com ou sem separadores)."""
    return _PHONE_LIKE.sub(_mask_match, text)


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key is not None and _PII_KEY.search(key):
        return HIDDEN
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, str):
        return mask_phone_like(value)
    return value


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "sim" if value else "não"
    if value is None:
        return EMPTY
    if isinstance(value, dict | list):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def safe_text(raw: object) -> str:
    """Valor antes/depois (jsonb) em texto curto e seguro; ``—`` quando nao ha valor."""
    if raw is None:
        return EMPTY
    try:
        data = json.loads(raw) if isinstance(raw, str | bytes) else raw
    except ValueError:
        return HIDDEN
    if data is None:
        return EMPTY
    clean = _sanitize(data)
    if isinstance(clean, dict):
        text = ", ".join(f"{k}: {_scalar(v)}" for k, v in sorted(clean.items()))
    else:
        text = _scalar(clean)
    text = mask_phone_like(text)
    return text if len(text) <= MAX_VALUE_TEXT else text[: MAX_VALUE_TEXT - 1] + "…"


def _where(f: AuditFilters) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("action", f.action),
        ("target_type", f.target_type),
        ("target_id", f.target_id),
    ):
        if value:
            params.append(value)
            clauses.append(f"{column} = ${len(params)}")
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def _row(r: asyncpg.Record) -> AuditRow:
    return AuditRow(
        id=int(r["id"]),
        at=r["at"],
        actor=r["actor"],
        actor_type=r["actor_type"],
        action=r["action"],
        target_type=r["target_type"],
        target_id=mask_phone_like(r["target_id"]),
        old=safe_text(r["old_value"]),
        new=safe_text(r["new_value"]),
    )


async def audit_page(conn: asyncpg.Connection, filters: AuditFilters) -> AuditPage:
    """Uma pagina da auditoria (mais novos primeiro); pagina alem do fim vira a ultima."""
    where, params = _where(filters)
    total = int(await conn.fetchval(f"SELECT count(*) FROM ops.audit_log{where}", *params) or 0)
    pages = max(1, -(-total // PAGE_SIZE))
    page = min(filters.page, pages)
    params.extend([PAGE_SIZE, (page - 1) * PAGE_SIZE])
    rows = await conn.fetch(
        "SELECT id, at, actor_type, actor, action, target_type, target_id, old_value, new_value"
        f" FROM ops.audit_log{where} ORDER BY at DESC, id DESC"
        f" LIMIT ${len(params) - 1} OFFSET ${len(params)}",
        *params,
    )
    return AuditPage([_row(r) for r in rows], total, page, pages)


async def load_facets(conn: asyncpg.Connection) -> AuditFacets:
    actions = await conn.fetch("SELECT DISTINCT action FROM ops.audit_log ORDER BY action")
    types = await conn.fetch("SELECT DISTINCT target_type FROM ops.audit_log ORDER BY target_type")
    return AuditFacets([r["action"] for r in actions], [r["target_type"] for r in types])
