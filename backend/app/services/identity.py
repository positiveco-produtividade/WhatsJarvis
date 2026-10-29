"""Resolucao de identidade do remetente por telefone/BSUID (spec 4.4.1 passo 6.2, AC-8, AC-9).

Roda na transacao do chamador: `SELECT ... FOR UPDATE` trava a linha do usuario ate o
fim da transacao da mensagem (AC-22: o status e lido depois, sem cache). Sem I/O alem
do banco; nunca registra telefone nem BSUID em log.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import asyncpg

from app.core.values import UserStatus
from app.services.access import AccessUser, SenderState
from app.services.inbound import Sender
from app.services.phone import InvalidPhoneError, normalize_phone

# Parametros repetidos/de tipo ambiguo levam cast explicito (licao AUTO-1).
_SELECT_SQL = """
SELECT u.id, u.phone_key, u.bsuid, u.status, u.accepted_at, u.proactive_opt_out,
       u.optin_reminder_at,
       (SELECT r.id FROM access.optin_requests r
         WHERE r.user_id = u.id AND r.status = 'open') AS open_notice_id
  FROM access.users u
 WHERE u.phone_key = $1::text OR u.bsuid = $2::text
 ORDER BY u.id
   FOR UPDATE OF u
"""


class IdentityKind(StrEnum):
    UNKNOWN = "unknown"
    USER = "user"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Identity:
    """Resultado de `resolve`; `state` e o que `access.decide` consome."""

    kind: IdentityKind
    state: SenderState
    user_id: int | None = None  # so para kind == USER
    phone_key: str | None = None  # chave do wa_id recebido (None se ausente/invalido)
    bsuid: str | None = None  # BSUID recebido
    conflict_user_ids: tuple[int, ...] = ()  # ids envolvidos no conflito (sem PII)
    bsuid_associated: bool = False  # True se este acesso gravou o BSUID no usuario


def phone_key_from_wa_id(wa_id: str | None) -> str | None:
    """`wa_id` (digitos, sem '+') -> match_key; None se ausente ou invalido."""
    if not wa_id:
        return None
    try:
        return normalize_phone("+" + wa_id.strip().lstrip("+")).match_key
    except InvalidPhoneError:
        return None


def _to_access_user(row: asyncpg.Record) -> AccessUser:
    return AccessUser(
        status=UserStatus(row["status"]),
        accepted_at=row["accepted_at"],
        proactive_opt_out=row["proactive_opt_out"],
        optin_reminder_at=row["optin_reminder_at"],
        open_notice_id=row["open_notice_id"],
    )


def _is_conflict(rows: list[asyncpg.Record], phone_key: str | None, bsuid: str | None) -> bool:
    """Dois usuarios; BSUID guardado diferente do recebido; ou telefone diferente do cadastrado."""
    if len(rows) > 1:
        return True
    row = rows[0]
    stored = row["bsuid"]
    if bsuid is not None and stored is not None and stored != bsuid:
        return True
    return phone_key is not None and row["phone_key"] != phone_key


def _conflict(rows: list[asyncpg.Record], phone_key: str | None, bsuid: str | None) -> Identity:
    return Identity(
        kind=IdentityKind.CONFLICT,
        state=SenderState(conflict=True),
        phone_key=phone_key,
        bsuid=bsuid,
        conflict_user_ids=tuple(int(r["id"]) for r in rows),
    )


async def resolve(conn: asyncpg.Connection, sender: Sender) -> Identity:
    """Resolve o remetente a `unknown | user | conflict`, travando a linha do usuario.

    Associa o BSUID recebido ao usuario que ainda nao o tem. BSUID de outro usuario
    nunca e reassociado: vira conflito.
    """
    phone_key = phone_key_from_wa_id(sender.wa_id)
    bsuid = sender.bsuid or None
    if phone_key is None and bsuid is None:
        return Identity(kind=IdentityKind.UNKNOWN, state=SenderState())
    rows = await conn.fetch(_SELECT_SQL, phone_key, bsuid)
    if not rows:
        return Identity(
            kind=IdentityKind.UNKNOWN, state=SenderState(), phone_key=phone_key, bsuid=bsuid
        )
    if _is_conflict(rows, phone_key, bsuid):
        return _conflict(rows, phone_key, bsuid)
    row = rows[0]
    associated = bsuid is not None and row["bsuid"] is None
    if associated:
        await conn.execute(
            "UPDATE access.users SET bsuid = $1::text WHERE id = $2::bigint AND bsuid IS NULL",
            bsuid,
            row["id"],
        )
    return Identity(
        kind=IdentityKind.USER,
        state=SenderState(user=_to_access_user(row)),
        user_id=int(row["id"]),
        phone_key=phone_key,
        bsuid=bsuid,
        bsuid_associated=associated,
    )
