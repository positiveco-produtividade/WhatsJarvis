"""Escrita da FAQ do painel (PN-23, PN-24, PN-26, PN-55; spec 4.7).

Cada operacao e uma unica transacao: validar (``faq_validation``, pura) -> gravar em
``content.faq_items`` -> linha em ``content.faq_item_versions`` -> auditoria. Falha da
auditoria propaga e desfaz tudo. Edicao usa controle otimista (``version``): se mudou,
nada e gravado e o texto atual volta no resultado; so ``force=True`` sobrescreve.
Desativar e reativar nunca apagam. Resultados esperados (invalido, conflito, nao achado)
voltam como ``FaqWriteResult``; erros de infraestrutura propagam.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg

from app.core.clock import Clock, SystemClock
from app.panel.services import faq_validation as fv
from app.services import audit

__all__ = [
    "CONFLICT",
    "INVALID",
    "NOOP",
    "NOT_FOUND",
    "OK",
    "FaqInput",
    "FaqRecord",
    "FaqWriteResult",
    "create_item",
    "deactivate_item",
    "get_item",
    "reactivate_item",
    "update_item",
]

OK = "ok"
INVALID = "invalid"
CONFLICT = "conflict"
NOT_FOUND = "not_found"
NOOP = "noop"

_ACTOR_TYPE = "panel"
_ORIGINS = ("manual", "import", "seed", "nielsen", "gmr")
_CREATE_KINDS = ("create", "import", "seed")

_COLUMNS = (
    "id, brand, category, question, question_key, answer, variations, bot_responde, "
    "active, origin, origin_ref, version, created_at, created_by, updated_at, updated_by, "
    "deactivated_at, deactivated_by"
)


@dataclass(frozen=True, slots=True)
class FaqInput:
    """Campos editaveis do formulario."""

    brand: str
    category: str
    question: str | None
    answer: str | None
    variations: tuple[str, ...] = ()
    bot_responde: bool = True


@dataclass(frozen=True, slots=True)
class FaqRecord:
    id: int
    brand: str
    category: str
    question: str
    question_key: str
    answer: str | None
    variations: tuple[str, ...]
    bot_responde: bool
    active: bool
    origin: str
    origin_ref: str | None
    version: int
    created_at: datetime
    created_by: str
    updated_at: datetime
    updated_by: str
    deactivated_at: datetime | None
    deactivated_by: str | None

    def snapshot(self) -> dict[str, Any]:
        """Estado gravado em ``faq_item_versions.snapshot`` e na auditoria (sem PII)."""
        return {
            "brand": self.brand,
            "category": self.category,
            "question": self.question,
            "question_key": self.question_key,
            "answer": self.answer,
            "variations": list(self.variations),
            "bot_responde": self.bot_responde,
            "active": self.active,
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class FaqWriteResult:
    """``status``: ok | invalid | conflict | not_found | noop.

    ``item``: o item gravado (ok), o texto atual (conflict) ou o item inalterado (noop).
    ``problems``: todos os problemas de validacao (invalid).
    """

    status: str
    item: FaqRecord | None = None
    problems: tuple[fv.FaqProblem, ...] = field(default_factory=tuple)

    @property
    def ok(self) -> bool:
        return self.status == OK


def _record(row: asyncpg.Record) -> FaqRecord:
    data = dict(row)
    data["variations"] = tuple(data["variations"] or ())
    return FaqRecord(**data)


def _now(clock: Clock | None) -> datetime:
    return (clock or SystemClock()).now()


def _duplicate_result() -> FaqWriteResult:
    problem = fv.FaqProblem(
        code=fv.DUPLICATE_QUESTION,
        message="Já existe uma pergunta ativa igual nesta marca.",
        field="question",
    )
    return FaqWriteResult(INVALID, problems=(problem,))


async def get_item(conn: Any, item_id: int, *, for_update: bool = False) -> FaqRecord | None:
    lock = " FOR UPDATE" if for_update else ""
    row = await conn.fetchrow(
        f"SELECT {_COLUMNS} FROM content.faq_items WHERE id = $1{lock}", item_id
    )
    return None if row is None else _record(row)


async def _active_keys(conn: Any, brand: str, *, exclude_id: int | None) -> list[str]:
    rows = await conn.fetch(
        "SELECT question_key FROM content.faq_items "
        "WHERE active AND brand = $1 AND ($2::bigint IS NULL OR id <> $2)",
        brand,
        exclude_id,
    )
    return [r["question_key"] for r in rows]


async def _validate(
    conn: Any, data: FaqInput, *, exclude_id: int | None, check_duplicate: bool
) -> fv.FaqValidation:
    keys: list[str] = []
    if check_duplicate:
        keys = await _active_keys(conn, data.brand, exclude_id=exclude_id)
    return fv.validate_faq(
        brand=data.brand,
        category=data.category,
        question=data.question,
        answer=data.answer,
        variations=data.variations,
        bot_responde=data.bot_responde,
        active_keys=keys,
    )


async def _write_version(conn: Any, item: FaqRecord, kind: str, actor: str, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO content.faq_item_versions "
        "(item_id, version, snapshot, change_kind, changed_at, changed_by) "
        "VALUES ($1, $2, $3::jsonb, $4, $5, $6)",
        item.id,
        item.version,
        json.dumps(item.snapshot()),
        kind,
        now,
        actor,
    )


async def _audit(
    conn: Any,
    clock: Clock | None,
    actor: str,
    action: str,
    item: FaqRecord,
    old: FaqRecord | None,
) -> None:
    await audit.write(
        conn,
        _ACTOR_TYPE,
        actor,
        action,
        "faq_item",
        str(item.id),
        None if old is None else old.snapshot(),
        item.snapshot(),
        clock=clock,
    )


async def create_item(
    conn: Any,
    data: FaqInput,
    *,
    actor: str,
    clock: Clock | None = None,
    origin: str = "manual",
    origin_ref: str | None = None,
    change_kind: str = "create",
) -> FaqWriteResult:
    """Cria um item ativo (versao 1). ``origin``/``change_kind`` servem a importacao e a semente."""
    if origin not in _ORIGINS or change_kind not in _CREATE_KINDS:
        raise ValueError("origin ou change_kind desconhecido")
    now = _now(clock)
    try:
        async with conn.transaction():
            checked = await _validate(conn, data, exclude_id=None, check_duplicate=True)
            if not checked.ok:
                return FaqWriteResult(INVALID, problems=checked.problems)
            row = await conn.fetchrow(
                "INSERT INTO content.faq_items (brand, category, question, question_key, "
                "answer, variations, bot_responde, active, origin, origin_ref, version, "
                "created_at, created_by, updated_at, updated_by) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, true, $8, $9, 1, $10, $11, $10, $11) "
                f"RETURNING {_COLUMNS}",
                data.brand,
                data.category,
                checked.question,
                checked.question_key,
                checked.answer,
                list(checked.variations),
                data.bot_responde,
                origin,
                origin_ref,
                now,
                actor,
            )
            item = _record(row)
            await _write_version(conn, item, change_kind, actor, now)
            await _audit(conn, clock, actor, "faq.create", item, None)
            return FaqWriteResult(OK, item=item)
    except asyncpg.UniqueViolationError:
        return _duplicate_result()


async def update_item(
    conn: Any,
    item_id: int,
    data: FaqInput,
    *,
    expected_version: int,
    actor: str,
    force: bool = False,
    clock: Clock | None = None,
) -> FaqWriteResult:
    """Edita com controle otimista; ``force=True`` (PN-26) grava mesmo com versao diferente."""
    now = _now(clock)
    try:
        async with conn.transaction():
            old = await get_item(conn, item_id, for_update=True)
            if old is None:
                return FaqWriteResult(NOT_FOUND)
            if old.version != expected_version and not force:
                return FaqWriteResult(CONFLICT, item=old)
            checked = await _validate(conn, data, exclude_id=item_id, check_duplicate=old.active)
            if not checked.ok:
                return FaqWriteResult(INVALID, item=old, problems=checked.problems)
            row = await conn.fetchrow(
                "UPDATE content.faq_items SET brand = $2, category = $3, question = $4, "
                "question_key = $5, answer = $6, variations = $7, bot_responde = $8, "
                "version = version + 1, updated_at = $9, updated_by = $10 "
                f"WHERE id = $1 RETURNING {_COLUMNS}",
                item_id,
                data.brand,
                data.category,
                checked.question,
                checked.question_key,
                checked.answer,
                list(checked.variations),
                data.bot_responde,
                now,
                actor,
            )
            item = _record(row)
            await _write_version(conn, item, "update", actor, now)
            await _audit(conn, clock, actor, "faq.update", item, old)
            return FaqWriteResult(OK, item=item)
    except asyncpg.UniqueViolationError:
        return _duplicate_result()


async def deactivate_item(
    conn: Any, item_id: int, *, actor: str, clock: Clock | None = None
) -> FaqWriteResult:
    """Desativa sem apagar (PN-24). Ja desativado = ``noop`` sem versao nem auditoria."""
    return await _set_active(conn, item_id, active=False, actor=actor, clock=clock)


async def reactivate_item(
    conn: Any, item_id: int, *, actor: str, clock: Clock | None = None
) -> FaqWriteResult:
    """Reativa (PN-24). Se ja ha outra ativa igual na marca, devolve ``invalid`` (duplicata)."""
    return await _set_active(conn, item_id, active=True, actor=actor, clock=clock)


async def _set_active(
    conn: Any, item_id: int, *, active: bool, actor: str, clock: Clock | None
) -> FaqWriteResult:
    now = _now(clock)
    try:
        async with conn.transaction():
            old = await get_item(conn, item_id, for_update=True)
            if old is None:
                return FaqWriteResult(NOT_FOUND)
            if old.active == active:
                return FaqWriteResult(NOOP, item=old)
            if active and old.question_key in await _active_keys(
                conn, old.brand, exclude_id=item_id
            ):
                return _duplicate_result()
            row = await conn.fetchrow(
                "UPDATE content.faq_items SET active = $2, version = version + 1, "
                "updated_at = $3, updated_by = $4, deactivated_at = $5, deactivated_by = $6 "
                f"WHERE id = $1 RETURNING {_COLUMNS}",
                item_id,
                active,
                now,
                actor,
                None if active else now,
                None if active else actor,
            )
            item = _record(row)
            kind = "reactivate" if active else "deactivate"
            await _write_version(conn, item, kind, actor, now)
            await _audit(conn, clock, actor, f"faq.{kind}", item, old)
            return FaqWriteResult(OK, item=item)
    except asyncpg.UniqueViolationError:
        return _duplicate_result()
