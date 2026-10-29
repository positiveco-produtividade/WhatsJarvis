"""Cadastro de pessoas autorizadas (R2, spec 4.3.2).

``validate_row`` e PURA (sem banco, rede ou relogio global): a mesma funcao serve ao CLI
("tudo ou nada") e ao painel ("so as validas"). ``register`` e ``revoke`` rodam na
transacao de quem chama (lote de CSV = uma transacao); o envio acontece depois, pela
fila de saida. Duplicata nao quebra a transacao do chamador (``ON CONFLICT DO NOTHING``),
entao o painel pode capturar ``DuplicatePhone`` e seguir com as demais linhas.
Sem PII em auditoria nem em erro: so ids, status e motivo.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import DuplicatePhone, InvalidValue
from app.core.values import (
    BRANDS,
    CHANNELS,
    PROFILES,
    REVOKE_REASONS,
    UFS,
    RevokeReason,
    UserStatus,
)
from app.services import audit, optin
from app.services.phone import InvalidPhoneError, normalize_phone

# Chaves de entrada (cabecalho do CSV, PT-BR) na ordem dos 8 campos obrigatorios.
KEY_NAME = "nome"
KEY_PHONE = "telefone"
KEY_PROFILE = "perfil"
KEY_COMPANY = "empresa"
KEY_UFS = "ufs"
KEY_CHANNELS = "canais"
KEY_BRANDS = "marcas"
KEY_MANAGER = "gestor"
FIELD_ORDER: tuple[str, ...] = (
    KEY_NAME, KEY_PHONE, KEY_PROFILE, KEY_COMPANY, KEY_UFS, KEY_CHANNELS, KEY_BRANDS, KEY_MANAGER,
)  # fmt: skip

MSG_REQUIRED = "campo obrigatório"
_SEPARATORS = re.compile(r"[|;]")


@dataclass(frozen=True)
class FieldError:
    """Erro de um campo: ``field`` e a chave de entrada (PT-BR); ``reason`` e exibivel."""

    field: str
    reason: str


@dataclass(frozen=True)
class ValidatedUser:
    """Linha validada, com os nomes internos (colunas de access.users)."""

    name: str
    phone_e164: str
    phone_key: str
    profile: str
    company: str
    ufs: tuple[str, ...]
    channels: tuple[str, ...]
    brands: tuple[str, ...]
    manager_name: str
    valid_until: datetime | None = None  # sempre None (Q6)


def _text(row: Mapping[str, Any], key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _items(row: Mapping[str, Any], key: str) -> list[str] | None:
    """Itens da lista (texto com ``|``/``;`` ou lista Python); None se invalida/vazia."""
    value = row.get(key)
    parts: Iterable[Any]
    if isinstance(value, str):
        parts = _SEPARATORS.split(value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        parts = value
    else:
        return None
    items: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            return None
        part = part.strip()
        if part:
            items.append(part)
    return items or None


def _check_list(
    row: Mapping[str, Any], key: str, allowed: tuple[str, ...], upper: bool
) -> tuple[tuple[str, ...], FieldError | None]:
    items = _items(row, key)
    if items is None:
        return (), FieldError(key, MSG_REQUIRED)
    normalized = list(dict.fromkeys(i.upper() if upper else i.lower() for i in items))
    invalid = [i for i in normalized if i not in allowed]
    if invalid:
        return (), FieldError(key, f"valor(es) fora da lista: {', '.join(invalid)}")
    return tuple(normalized), None


def _check_phone(row: Mapping[str, Any]) -> tuple[tuple[str, str], FieldError | None]:
    raw = _text(row, KEY_PHONE)
    if not raw:
        return ("", ""), FieldError(KEY_PHONE, MSG_REQUIRED)
    try:
        phone = normalize_phone(raw)
    except InvalidPhoneError as exc:
        return ("", ""), FieldError(KEY_PHONE, str(exc))
    return (phone.phone_e164, phone.match_key), None


def _check_profile(row: Mapping[str, Any]) -> tuple[str, FieldError | None]:
    profile = _text(row, KEY_PROFILE).lower()
    if not profile:
        return "", FieldError(KEY_PROFILE, MSG_REQUIRED)
    if profile not in PROFILES:
        return "", FieldError(KEY_PROFILE, f"perfil fora da lista: {profile}")
    return profile, None


def validate_row(row: Mapping[str, Any], *, now: datetime) -> ValidatedUser | list[FieldError]:
    """Valida os 8 campos obrigatorios; devolve o valor validado ou todos os erros.

    PURA: ``now`` e injetado (nao usado hoje porque ``valid_until`` e sempre None,
    mas faz parte do contrato congelado). Erros vem na ordem dos campos.
    """
    _ = now
    errors: list[FieldError] = []

    name = _text(row, KEY_NAME)
    company = _text(row, KEY_COMPANY)
    manager = _text(row, KEY_MANAGER)
    for key, value in ((KEY_NAME, name), (KEY_COMPANY, company), (KEY_MANAGER, manager)):
        if not value:
            errors.append(FieldError(key, MSG_REQUIRED))

    phone, phone_err = _check_phone(row)
    profile, profile_err = _check_profile(row)
    ufs, ufs_err = _check_list(row, KEY_UFS, UFS, upper=True)
    channels, channels_err = _check_list(row, KEY_CHANNELS, CHANNELS, upper=False)
    brands, brands_err = _check_list(row, KEY_BRANDS, BRANDS, upper=False)
    errors.extend(e for e in (phone_err, profile_err, ufs_err, channels_err, brands_err) if e)
    if errors:
        return sorted(errors, key=lambda e: FIELD_ORDER.index(e.field))

    return ValidatedUser(
        name=name,
        phone_e164=phone[0],
        phone_key=phone[1],
        profile=profile,
        company=company,
        ufs=ufs,
        channels=channels,
        brands=brands,
        manager_name=manager,
        valid_until=None,
    )


# ---------------------------------------------------------------------------
# Administracao (banco): register e revoke
# ---------------------------------------------------------------------------

ACTION_REGISTERED = "user.registered"
ACTION_REVOKED = "user.revoked"
TARGET_TYPE = "user"

_COLUMNS = (
    "id, name, phone_e164, phone_key, bsuid, profile, company, ufs, channels, brands, "
    "manager_name, valid_until, status, status_changed_at, accepted_at, revoked_at, "
    "revoked_by, revoked_reason, created_at, created_by"
)
_INSERT_USER = (
    "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, channels, "
    "brands, manager_name, valid_until, status, status_changed_at, optin_reminder_at, "
    "created_at, created_by, updated_at) "
    "VALUES ($1, $2, $3, $4, $5, $6::text[], $7::text[], $8::text[], $9, $10, 'pending_optin', "
    "$11, NULL, $11, $12, $11) "
    "ON CONFLICT (phone_key) DO NOTHING "
    f"RETURNING {_COLUMNS}"
)
_LOCK_USER = f"SELECT {_COLUMNS} FROM access.users WHERE id = $1 FOR UPDATE"
_REVOKE_USER = (
    "UPDATE access.users SET status = 'revoked', status_changed_at = $2, revoked_at = $2, "
    "revoked_by = $3, revoked_reason = $4, updated_at = $2 "
    f"WHERE id = $1 RETURNING {_COLUMNS}"
)


@dataclass(frozen=True)
class UserRow:
    """Pessoa autorizada (colunas de access.users usadas por quem administra)."""

    id: int
    name: str
    phone_e164: str
    phone_key: str
    bsuid: str | None
    profile: str
    company: str
    ufs: tuple[str, ...]
    channels: tuple[str, ...]
    brands: tuple[str, ...]
    manager_name: str
    valid_until: datetime | None
    status: str
    status_changed_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    revoked_by: str | None
    revoked_reason: str | None
    created_at: datetime
    created_by: str


def _to_user_row(record: Any) -> UserRow:
    return UserRow(
        id=record["id"],
        name=record["name"],
        phone_e164=record["phone_e164"],
        phone_key=record["phone_key"],
        bsuid=record["bsuid"],
        profile=record["profile"],
        company=record["company"],
        ufs=tuple(record["ufs"]),
        channels=tuple(record["channels"]),
        brands=tuple(record["brands"]),
        manager_name=record["manager_name"],
        valid_until=record["valid_until"],
        status=record["status"],
        status_changed_at=record["status_changed_at"],
        accepted_at=record["accepted_at"],
        revoked_at=record["revoked_at"],
        revoked_by=record["revoked_by"],
        revoked_reason=record["revoked_reason"],
        created_at=record["created_at"],
        created_by=record["created_by"],
    )


async def register(
    conn: Any,
    data: ValidatedUser,
    *,
    actor: str,
    actor_type: str = "cli",
    enqueue_welcome: bool = True,
    settings: Settings | None = None,
    clock: Clock | None = None,
) -> UserRow:
    """Cadastra a pessoa como ``pending_optin`` na transacao do chamador (R2, AC-15/16/17).

    Com ``enqueue_welcome`` (padrao) abre o aviso de opt-in e enfileira o modelo de
    boas-vindas (``settings`` e obrigatorio nesse caso). ``enqueue_welcome=False`` e so para
    testes/migracao: cria o usuario e audita, sem aviso nem saida. Telefone ja cadastrado
    (``phone_key``, em qualquer status) levanta ``DuplicatePhone`` sem nenhum efeito e sem
    abortar a transacao do chamador; para reativar, usar o reenvio.
    """
    if enqueue_welcome and settings is None:
        raise TypeError("settings e obrigatorio quando enqueue_welcome=True")
    now = (clock or SystemClock()).now()
    record = await conn.fetchrow(
        _INSERT_USER,
        data.name, data.phone_e164, data.phone_key, data.profile, data.company,
        list(data.ufs), list(data.channels), list(data.brands), data.manager_name,
        data.valid_until, now, actor,
    )  # fmt: skip
    if record is None:
        raise DuplicatePhone("telefone ja cadastrado")
    user = _to_user_row(record)
    notice_id: int | None = None
    if enqueue_welcome and settings is not None:
        issued = await optin.issue_notice(conn, user, actor, settings=settings, clock=clock)
        notice_id = issued.notice_id
    await audit.write(
        conn, actor_type, actor, ACTION_REGISTERED, TARGET_TYPE, str(user.id),
        old=None,
        new={"status": UserStatus.PENDING_OPTIN.value, "notice_id": notice_id},
        clock=clock,
    )  # fmt: skip
    return user


def _parse_reason(reason: RevokeReason | str) -> str:
    value = reason.value if isinstance(reason, RevokeReason) else reason
    if value not in REVOKE_REASONS:
        raise InvalidValue("reason", "motivo de revogacao nao aceito")
    return value


async def revoke(
    conn: Any,
    user_id: int,
    reason: RevokeReason | str,
    actor: str,
    *,
    actor_type: str = "cli",
    clock: Clock | None = None,
) -> UserRow:
    """Revoga o acesso (AC-34), na transacao do chamador. Idempotente.

    Motivo fora da lista fixa ou usuario inexistente levantam ``InvalidValue``. Se ja esta
    ``revoked`` nada muda (motivo, autor e data originais ficam; sem nova auditoria). Nao ha
    prazo de validade. Respostas pendentes sao descartadas no despacho (``required_status``).
    """
    reason_value = _parse_reason(reason)
    current = await conn.fetchrow(_LOCK_USER, user_id)
    if current is None:
        raise InvalidValue("user_id", "usuario nao encontrado")
    if current["status"] == UserStatus.REVOKED.value:
        return _to_user_row(current)
    now = (clock or SystemClock()).now()
    record = await conn.fetchrow(_REVOKE_USER, user_id, now, actor, reason_value)
    await audit.write(
        conn, actor_type, actor, ACTION_REVOKED, TARGET_TYPE, str(user_id),
        old={"status": current["status"]},
        new={"status": UserStatus.REVOKED.value, "reason": reason_value},
        clock=clock,
    )  # fmt: skip
    return _to_user_row(record)


# ---------------------------------------------------------------------------
# Reenvio/reativacao e troca de numero (AC-35, AC-26)
# ---------------------------------------------------------------------------

ACTION_RESENT = "user.resent"
ACTION_NUMBER_SWAPPED = "user.number_swapped"

_LOCK_BY_PHONE_KEY = f"SELECT {_COLUMNS} FROM access.users WHERE phone_key = $1 FOR UPDATE"
# Prefixo que a exclusao LGPD do painel grava em nome/telefone (`anon:<id>`).
ANONYMIZED_PREFIX = "anon:"
_LOCK_FOR_RESEND = f"SELECT {_COLUMNS}, declined_at FROM access.users WHERE id = $1 FOR UPDATE"
_REACTIVATE_USER = (
    "UPDATE access.users SET status = 'pending_optin', status_changed_at = $2, "
    "accepted_at = NULL, accepted_notice_version = NULL, declined_at = NULL, "
    "revoked_at = NULL, revoked_by = NULL, revoked_reason = NULL, "
    "optin_reminder_at = NULL, updated_at = $2 "
    f"WHERE id = $1 RETURNING {_COLUMNS}"
)


class UserAlreadyActive(InvalidValue):
    """Reenvio recusado: a pessoa ja esta ativa (AC-35)."""

    def __init__(self) -> None:
        super().__init__("status", "usuario ja esta ativo; nao ha o que reenviar")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _previous_state(row: Any) -> dict[str, Any]:
    """Estado anterior que vai para ``audit_log.old_value`` (so ids, status, datas, motivo)."""
    return {
        "status": row["status"],
        "accepted_at": _iso(row["accepted_at"]),
        "declined_at": _iso(row["declined_at"]),
        "revoked_at": _iso(row["revoked_at"]),
        "revoked_by": row["revoked_by"],
        "revoked_reason": row["revoked_reason"],
    }


async def resend(
    conn: Any,
    user_id: int,
    actor: str,
    *,
    actor_type: str = "cli",
    settings: Settings,
    clock: Clock | None = None,
) -> UserRow:
    """Reenvia as boas-vindas / reativa (AC-35), na transacao do chamador.

    ``pending_optin``/``declined``/``revoked`` voltam a ``pending_optin`` com novo aviso
    (o anterior vira ``superseded``); a nova aceitacao e obrigatoria (``accepted_at`` e
    ``revoked_*`` zerados; os valores antigos ficam em ``audit_log.old_value``). ``active``
    levanta ``UserAlreadyActive``; usuario inexistente levanta ``InvalidValue``.
    """
    current = await conn.fetchrow(_LOCK_FOR_RESEND, user_id)
    if current is None:
        raise InvalidValue("user_id", "usuario nao encontrado")
    if current["status"] == UserStatus.ACTIVE.value:
        raise UserAlreadyActive
    if str(current["phone_e164"]).startswith(ANONYMIZED_PREFIX):
        # Dados excluidos pela LGPD: nao ha telefone para onde mandar; cadastrar de novo.
        raise InvalidValue("user_id", "dados excluidos (LGPD); cadastre a pessoa de novo")
    now = (clock or SystemClock()).now()
    record = await conn.fetchrow(_REACTIVATE_USER, user_id, now)
    user = _to_user_row(record)
    issued = await optin.issue_notice(conn, user, actor, settings=settings, clock=clock)
    await audit.write(
        conn, actor_type, actor, ACTION_RESENT, TARGET_TYPE, str(user_id),
        old=_previous_state(current),
        new={"status": UserStatus.PENDING_OPTIN.value, "notice_id": issued.notice_id},
        clock=clock,
    )  # fmt: skip
    return user


def _phone_key(raw: str, field: str) -> tuple[str, str]:
    try:
        phone = normalize_phone(raw)
    except InvalidPhoneError as exc:
        raise InvalidValue(field, str(exc)) from exc
    return phone.phone_e164, phone.match_key


def _copy_for_new_number(old: Any, phone_e164: str, phone_key: str) -> ValidatedUser:
    return ValidatedUser(
        name=old["name"],
        phone_e164=phone_e164,
        phone_key=phone_key,
        profile=old["profile"],
        company=old["company"],
        ufs=tuple(old["ufs"]),
        channels=tuple(old["channels"]),
        brands=tuple(old["brands"]),
        manager_name=old["manager_name"],
        valid_until=None,
    )


async def swap_number(
    conn: Any,
    from_phone: str,
    to_phone: str,
    actor: str,
    *,
    actor_type: str = "cli",
    settings: Settings,
    clock: Clock | None = None,
) -> tuple[UserRow, UserRow]:
    """Troca de numero (AC-26): revoga A (``number_change``) e cria B com os mesmos dados.

    Tudo numa unica (sub)transacao: qualquer falha (A inexistente, B ja cadastrado,
    telefone invalido) desfaz todos os passos. Devolve ``(A revogado, B pending_optin)``.
    """
    _, from_key = _phone_key(from_phone, "from")
    to_e164, to_key = _phone_key(to_phone, "to")
    async with conn.transaction():
        old = await conn.fetchrow(_LOCK_BY_PHONE_KEY, from_key)
        if old is None:
            raise InvalidValue("from", "numero de origem nao cadastrado")
        revoked = await revoke(
            conn, old["id"], RevokeReason.NUMBER_CHANGE, actor, actor_type=actor_type, clock=clock
        )
        created = await register(
            conn, _copy_for_new_number(old, to_e164, to_key), actor=actor,
            actor_type=actor_type, settings=settings, clock=clock,
        )  # fmt: skip
        await audit.write(
            conn, actor_type, actor, ACTION_NUMBER_SWAPPED, TARGET_TYPE, str(revoked.id),
            old={"user_id": revoked.id},
            new={"user_id": created.id},
            clock=clock,
        )  # fmt: skip
    return revoked, created
