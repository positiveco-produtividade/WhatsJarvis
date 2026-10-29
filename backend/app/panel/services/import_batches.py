"""Lotes de importacao com conferencia (PN-27..29, PN-40; spec 4.7 e 4.8).

Servico generico sobre ``ops.import_batches`` para FAQ e pessoas. O lote guarda as linhas
classificadas no servidor (nada de JSON em campo oculto). Linhas de ``people`` sao PII:
sao apagadas (``rows = []``) ao gravar, cancelar ou expirar. TTL: 2 h (faq), 1 h (people).

Gravacao atomica: ``claim_commit`` faz um UPDATE condicional (``status='preview'``) na
transacao do chamador e devolve as linhas ANTES do apagamento. Quem chega depois (segundo
clique, reabertura) recebe ``None`` e nao duplica. Se o chamador falhar e desfizer a
transacao, o lote volta a ``preview`` com as linhas intactas.
Nunca coloca linhas, nomes ou telefones em logs ou excecoes.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import asyncpg

from app.core.clock import Clock, SystemClock

__all__ = [
    "CANCELLED",
    "COMMITTED",
    "EXPIRED",
    "PREVIEW",
    "TTL",
    "BatchRecord",
    "cancel_batch",
    "claim_commit",
    "create_batch",
    "expire_due",
    "get_batch",
]

PREVIEW = "preview"
COMMITTED = "committed"
CANCELLED = "cancelled"
EXPIRED = "expired"

TTL: dict[str, timedelta] = {"faq": timedelta(hours=2), "people": timedelta(hours=1)}

_COLS = (
    "id, kind, actor, brand, filename, sha256, size_bytes, rows, summary, status, "
    "created_at, expires_at, finished_at"
)
_B_COLS = ", ".join("b." + c.strip() for c in _COLS.split(","))
# Apaga a PII das linhas de pessoas; FAQ mantem as linhas como registro.
_WIPE = "rows = CASE WHEN kind = 'people' THEN '[]'::jsonb ELSE rows END"


@dataclass(frozen=True, slots=True)
class BatchRecord:
    id: uuid.UUID
    kind: str
    actor: str
    brand: str | None
    filename: str
    sha256: str
    size_bytes: int
    rows: list[dict[str, Any]]
    summary: dict[str, Any]
    status: str
    created_at: datetime
    expires_at: datetime
    finished_at: datetime | None

    @property
    def is_open(self) -> bool:
        return self.status == PREVIEW


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _record(row: asyncpg.Record, *, rows: Any = None) -> BatchRecord:
    data = {k: row[k] for k in row.keys() if k != "old_rows"}  # noqa: SIM118 - Record
    data["rows"] = list(_loads(data["rows"] if rows is None else rows) or [])
    data["summary"] = dict(_loads(data["summary"]) or {})
    return BatchRecord(**data)


def _now(clock: Clock | None) -> datetime:
    return (clock or SystemClock()).now()


async def create_batch(
    conn: asyncpg.Connection,
    *,
    kind: str,
    actor: str,
    filename: str,
    sha256: str,
    size_bytes: int,
    rows: list[dict[str, Any]],
    summary: dict[str, Any],
    brand: str | None = None,
    clock: Clock | None = None,
) -> BatchRecord:
    """Cria o lote em ``preview`` com o TTL do tipo. ``summary`` nao pode ter PII."""
    if kind not in TTL:
        raise ValueError("tipo de lote invalido")
    now = _now(clock)
    row = await conn.fetchrow(
        f"""INSERT INTO ops.import_batches
              (id, kind, actor, brand, filename, sha256, size_bytes, rows, summary,
               status, created_at, expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, 'preview', $10, $11)
            RETURNING {_COLS}""",  # noqa: S608 - colunas constantes
        uuid.uuid4(),
        kind,
        actor,
        brand,
        filename,
        sha256,
        size_bytes,
        json.dumps(rows, ensure_ascii=False),
        json.dumps(summary, ensure_ascii=False),
        now,
        now + TTL[kind],
    )
    if row is None:  # pragma: no cover - INSERT ... RETURNING sempre devolve
        raise RuntimeError("falha ao criar o lote")
    return _record(row)


async def get_batch(
    conn: asyncpg.Connection, batch_id: uuid.UUID, *, clock: Clock | None = None
) -> BatchRecord | None:
    """Le o lote (reabrir a conferencia). Se venceu, expira na hora e apaga a PII."""
    now = _now(clock)
    async with conn.transaction():
        await conn.execute(
            f"""UPDATE ops.import_batches
                   SET status = 'expired', finished_at = $2, {_WIPE}
                 WHERE id = $1 AND status = 'preview' AND expires_at <= $2""",  # noqa: S608
            batch_id,
            now,
        )
        row = await conn.fetchrow(
            f"SELECT {_COLS} FROM ops.import_batches WHERE id = $1",  # noqa: S608
            batch_id,
        )
    return _record(row) if row else None


async def claim_commit(
    conn: asyncpg.Connection, batch_id: uuid.UUID, *, clock: Clock | None = None
) -> BatchRecord | None:
    """Marca ``committed`` so se ainda ``preview`` e dentro do prazo.

    Chamar dentro da transacao da gravacao. Devolve o lote com as linhas ANTES do
    apagamento, ou ``None`` (ja gravado, cancelado, expirado ou inexistente).
    """
    now = _now(clock)
    row = await conn.fetchrow(
        f"""WITH old AS (
              SELECT id, rows AS old_rows FROM ops.import_batches
               WHERE id = $1 AND status = 'preview' AND expires_at > $2
                 FOR UPDATE)
            UPDATE ops.import_batches b
               SET status = 'committed', finished_at = $2,
                   rows = CASE WHEN b.kind = 'people' THEN '[]'::jsonb ELSE b.rows END
              FROM old WHERE b.id = old.id
            RETURNING {_B_COLS}, old.old_rows""",  # noqa: S608 - colunas constantes
        batch_id,
        now,
    )
    if row is None:
        return None
    return _record(row, rows=row["old_rows"])


async def cancel_batch(
    conn: asyncpg.Connection, batch_id: uuid.UUID, *, clock: Clock | None = None
) -> bool:
    """Cancela um lote em ``preview``; apaga a PII. ``False`` se nao estava aberto."""
    result = await conn.execute(
        f"""UPDATE ops.import_batches
               SET status = 'cancelled', finished_at = $2, {_WIPE}
             WHERE id = $1 AND status = 'preview'""",  # noqa: S608
        batch_id,
        _now(clock),
    )
    return bool(result == "UPDATE 1")


async def expire_due(conn: asyncpg.Connection, *, clock: Clock | None = None) -> int:
    """Expira todos os lotes vencidos (apagando PII de ``people``). Devolve quantos."""
    result = await conn.execute(
        f"""UPDATE ops.import_batches
               SET status = 'expired', finished_at = $1, {_WIPE}
             WHERE status = 'preview' AND expires_at <= $1""",  # noqa: S608
        _now(clock),
    )
    return int(result.rsplit(" ", 1)[1])
