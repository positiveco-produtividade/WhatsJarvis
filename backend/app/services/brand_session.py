"""Marca da conversa (spec canal-waha.1, 9.9: WA-15, WA-21 a WA-23; task WA-T19).

O vendedor escolhe a marca por enquete (ou escrevendo so o nome dela); a marca vale ate
`BRAND_SESSION_MINUTES` depois da ultima mensagem dele (a janela se renova a cada mensagem).
Guardada em `access.users.active_brand` / `active_brand_at` (migration 301). Com
`BRAND_SESSION_MINUTES=0` a funcao fica desligada e o Jarvis volta ao comportamento anterior.

Puro + SQL curto; nao importa `app.panel` nem o cerebro. Quem decide o que responder e o
provedor do cerebro (WA-T21) e o opt-in (WA-T20).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from app.core.clock import Clock
from app.core.values import BRAND_NAMES, BRANDS, OutboundKind
from app.services import outbox
from app.services.access import normalize_keyword
from app.services.inbound import InboundMessage
from app.services.waha_inbound import POLL_VOTE_PREFIX

BRAND_PAYLOAD_PREFIX = "BRAND:"
SWITCH_COMMANDS: frozenset[str] = frozenset(
    {"trocar marca", "trocar de marca", "mudar marca", "mudar de marca", "outra marca"}
)

_LOAD = "SELECT active_brand, active_brand_at FROM access.users WHERE id = $1::bigint"
_SET = (
    "UPDATE access.users SET active_brand = $2, active_brand_at = $3 WHERE id = $1::bigint"
)
_TOUCH = (
    "UPDATE access.users SET active_brand_at = $2 "
    "WHERE id = $1::bigint AND active_brand IS NOT NULL"
)
_CLEAR = (
    "UPDATE access.users SET active_brand = NULL, active_brand_at = NULL WHERE id = $1::bigint"
)
_LAST_POLL = (
    "SELECT wa_message_id FROM chat.outbound_messages "
    "WHERE user_id = $1::bigint AND kind = 'brand_poll' ORDER BY id DESC LIMIT 1"
)

# Formas aceitas do nome de cada marca, ja normalizadas (minusculas, sem acento).
_NAME_FORMS: dict[str, str] = {}
for _slug, _name in BRAND_NAMES.items():
    for _form in (_name, _slug.replace("_", " "), _slug):
        _NAME_FORMS[normalize_keyword(_form)] = _slug
_NAME_FORMS[normalize_keyword("A Tal")] = "a_tal_da_castanha"
_NAME_FORMS[normalize_keyword("Tal da Castanha")] = "a_tal_da_castanha"


@dataclass(frozen=True)
class BrandState:
    brand: str | None
    at: datetime | None


def enabled(minutes: int) -> bool:
    return minutes > 0


def valid_brand(state: BrandState, now: datetime, minutes: int) -> str | None:
    """Marca da conversa ainda valida, ou None (vencida, ausente ou funcao desligada)."""
    if not enabled(minutes) or state.brand is None or state.at is None:
        return None
    return state.brand if now - state.at <= timedelta(minutes=minutes) else None


def ordered(brands: Sequence[str]) -> list[str]:
    """Marcas do cadastro na ordem oficial, sem repeticao nem marca desconhecida."""
    wanted = set(brands)
    return [b for b in BRANDS if b in wanted]


def brand_from_text(text: str | None, allowed: Sequence[str]) -> str | None:
    """Slug da marca quando o texto e SO o nome de uma marca do cadastro (WA-21)."""
    if not text:
        return None
    slug = _NAME_FORMS.get(normalize_keyword(text))
    return slug if slug is not None and slug in allowed else None


def brand_from_payload(payload: str | None, allowed: Sequence[str]) -> str | None:
    """Slug do botao `BRAND:<slug>` (escolha de marca na Meta, WA-25)."""
    if not payload or not payload.startswith(BRAND_PAYLOAD_PREFIX):
        return None
    slug = payload[len(BRAND_PAYLOAD_PREFIX) :]
    return slug if slug in allowed else None


def is_switch_command(text: str | None) -> bool:
    return bool(text) and normalize_keyword(text or "") in SWITCH_COMMANDS


def vote_poll_id(msg: InboundMessage) -> str | None:
    """Id da enquete votada (`poll.vote` do WAHA), ou None se a mensagem nao e voto."""
    payload = msg.button.payload if msg.button is not None else ""
    return payload[len(POLL_VOTE_PREFIX) :] if payload.startswith(POLL_VOTE_PREFIX) else None


def poll_options(brands: Sequence[str]) -> list[dict[str, str]]:
    """Opcoes da enquete: `id` = payload do botao na Meta; `title` = nome que volta no voto."""
    return [{"id": f"{BRAND_PAYLOAD_PREFIX}{b}", "title": BRAND_NAMES[b]} for b in ordered(brands)]


async def load(conn: Any, user_id: int) -> BrandState:
    row = await conn.fetchrow(_LOAD, user_id)
    if row is None:
        return BrandState(None, None)
    return BrandState(row["active_brand"], row["active_brand_at"])


async def set_brand(conn: Any, user_id: int, brand: str, now: datetime) -> None:
    if brand not in BRANDS:
        raise ValueError("marca desconhecida")
    await conn.execute(_SET, user_id, brand, now)


async def touch(conn: Any, user_id: int, now: datetime) -> None:
    """Renova a janela da marca da conversa (a cada mensagem do vendedor)."""
    await conn.execute(_TOUCH, user_id, now)


async def clear(conn: Any, user_id: int) -> None:
    await conn.execute(_CLEAR, user_id)


async def is_latest_poll(conn: Any, user_id: int, poll_id: str) -> bool:
    """Voto so vale para a ultima enquete de marca enviada ao vendedor (WA-21)."""
    latest = await conn.fetchval(_LAST_POLL, user_id)
    return latest is not None and latest == poll_id


async def enqueue_poll(
    conn: Any,
    recipient: outbox.OutboundTarget,
    question: str,
    brands: Sequence[str],
    *,
    inbound_trace_id: int | None = None,
    clock: Clock | None = None,
) -> int:
    """Enfileira a enquete de marca (kind `brand_poll`) na transacao do chamador."""
    options = poll_options(brands)
    if len(options) < 2:
        raise ValueError("enquete de marca precisa de 2 ou mais marcas")
    return await outbox.enqueue(
        conn,
        recipient,
        OutboundKind.BRAND_POLL,
        body=question,
        inbound_trace_id=inbound_trace_id,
        reply_buttons=options,
        clock=clock,
    )
