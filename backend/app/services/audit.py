"""Auditoria (ops.audit_log): grava dentro da transacao de quem chama.

Sem PII: so ids e status em target_id/old/new. Uma falha ao gravar propaga e,
como a gravacao roda na mesma transacao da acao, aborta a acao inteira.
"""

import json
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue

ACTOR_TYPES: tuple[str, ...] = ("cli", "panel", "system")

_INSERT = (
    "INSERT INTO ops.audit_log "
    "(at, actor_type, actor, action, target_type, target_id, old_value, new_value) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb) RETURNING id"
)


def _to_json(value: Any) -> str | None:
    return None if value is None else json.dumps(value)


async def write(
    conn: Any,
    actor_type: str,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    old: Any = None,
    new: Any = None,
    *,
    clock: Clock | None = None,
) -> int:
    """Insere uma linha de auditoria e devolve o id. Nao captura excecoes."""
    if actor_type not in ACTOR_TYPES:
        raise InvalidValue("actor_type", f"valor nao aceito: {actor_type}")
    now = (clock or SystemClock()).now()
    row_id: int = await conn.fetchval(
        _INSERT,
        now,
        actor_type,
        actor,
        action,
        target_type,
        target_id,
        _to_json(old),
        _to_json(new),
    )
    return row_id
