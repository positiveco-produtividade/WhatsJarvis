"""Tentativas de acesso de remetentes sem acesso (spec 4.4.2, AC-10, AC-11, AC-12, AC-27).

Uma linha por remetente em `access.access_attempts`, que alimenta "Pedindo acesso" do
painel. Roda na transacao do chamador. Nunca responde, alerta nem enfileira entrega.
O texto da 1a mensagem so e guardado (tabela separada) se o chamador ligar
`store_first_text` (UNKNOWN_FIRST_TEXT_STORE, desligado ate a aprovacao juridica, Q17),
a mensagem for texto, o motivo for `unknown` e a linha estiver sendo criada agora:
nunca e sobrescrito e midia nunca guarda corpo. Sem log de telefone nem BSUID.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.services import audit

REASONS: tuple[str, ...] = ("unknown", "revoked", "declined", "identity_conflict")

_LOCK_SQL = "SELECT pg_advisory_xact_lock(hashtextextended($1::text, 0))"

_FIND_SQL = (
    "SELECT id FROM access.access_attempts "
    "WHERE phone_key = $1::text OR bsuid = $2::text ORDER BY id LIMIT 1"
)

_INSERT_SQL = (
    "INSERT INTO access.access_attempts (phone_key, bsuid, user_id, reason, attempt_count, "
    "first_attempt_at, last_attempt_at, first_msg_type, first_msg_chars, first_msg_mime) "
    "VALUES ($1::text, $2::text, $3::bigint, $4::text, 1, $5::timestamptz, $5::timestamptz, "
    "$6::text, $7::integer, $8::text) RETURNING id"
)

# Completa o identificador que faltava, so se nenhuma outra linha ja o usa (UNIQUE).
_UPDATE_SQL = (
    "UPDATE access.access_attempts a SET "
    "attempt_count = a.attempt_count + 1, last_attempt_at = $2::timestamptz, "
    "reason = $3::text, dismissed_at = NULL, "
    "user_id = COALESCE($4::bigint, a.user_id), "
    "phone_key = COALESCE(a.phone_key, CASE WHEN $5::text IS NOT NULL AND NOT EXISTS ("
    "SELECT 1 FROM access.access_attempts o WHERE o.phone_key = $5::text AND o.id <> a.id) "
    "THEN $5::text END), "
    "bsuid = COALESCE(a.bsuid, CASE WHEN $6::text IS NOT NULL AND NOT EXISTS ("
    "SELECT 1 FROM access.access_attempts o WHERE o.bsuid = $6::text AND o.id <> a.id) "
    "THEN $6::text END) "
    "WHERE a.id = $1::bigint RETURNING a.attempt_count"
)

_STORE_TEXT_SQL = (
    "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at) "
    "VALUES ($1::bigint, $2::text, $3::timestamptz) ON CONFLICT (attempt_id) DO NOTHING"
)

_DISMISS_SQL = (
    "UPDATE access.access_attempts SET dismissed_at = $2::timestamptz "
    "WHERE id = $1::bigint AND dismissed_at IS NULL RETURNING id"
)


@dataclass(frozen=True)
class AttemptResult:
    attempt_id: int
    attempt_count: int
    is_first: bool
    text_stored: bool


async def record(
    conn: Any,
    *,
    phone_key: str | None,
    bsuid: str | None,
    reason: str,
    msg_type: str,
    now: datetime,
    text: str | None = None,
    mime: str | None = None,
    user_id: int | None = None,
    store_first_text: bool = False,
) -> AttemptResult:
    """Upsert da tentativa por `phone_key`/`bsuid`: +1, `last_attempt_at`, reabre dispensada."""
    if reason not in REASONS:
        raise InvalidValue("reason", f"valor nao aceito: {reason}")
    if phone_key is None and bsuid is None:
        raise InvalidValue("sender", "phone_key ou bsuid e obrigatorio")
    await conn.execute(_LOCK_SQL, f"attempt:{phone_key or bsuid}")
    found = await conn.fetchval(_FIND_SQL, phone_key, bsuid)
    if found is not None:
        count = await conn.fetchval(
            _UPDATE_SQL, found, now, reason, user_id, phone_key, bsuid
        )
        return AttemptResult(int(found), int(count), False, False)
    is_text = msg_type == "text"
    attempt_id = int(
        await conn.fetchval(
            _INSERT_SQL, phone_key, bsuid, user_id, reason, now, msg_type,
            len(text) if is_text and text is not None else None,
            None if is_text else mime,
        )
    )
    stored = bool(store_first_text and is_text and text is not None and reason == "unknown")
    if stored:
        await conn.execute(_STORE_TEXT_SQL, attempt_id, text, now)
    return AttemptResult(attempt_id, 1, True, stored)


async def dismiss(
    conn: Any, attempt_id: int, actor: str, *, clock: Clock | None = None
) -> bool:
    """"Ignorar" do painel: marca `dismissed_at` e audita. False se nao existe ou ja ignorada."""
    clock = clock or SystemClock()
    row = await conn.fetchval(_DISMISS_SQL, attempt_id, clock.now())
    if row is None:
        return False
    await audit.write(
        conn, "panel", actor, "access_attempt.dismiss", "access_attempt", str(attempt_id),
        clock=clock,
    )
    return True
