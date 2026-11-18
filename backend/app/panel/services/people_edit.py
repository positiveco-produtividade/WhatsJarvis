"""Editar uma pessoa pelo painel (cadastro.1, CA-2..CA-11).

`edit_person` roda numa transacao so: `users.update` (dados, marca da conversa, auditoria) e,
para cada marca acrescentada que a pessoa tinha pedido (ticket `brand_access_request`
aguardando), conclui os pedidos (`tickets_admin.conclude`, auditado) e, se a pessoa esta
`active`, enfileira um aviso `brand_access_granted` por marca (CA-11). O envio e de quem
chama, depois do COMMIT (despachante do app). Nada aqui loga nome, telefone ou texto.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.config import Settings
from app.core.clock import Clock
from app.core.values import BRAND_NAMES, OutboundKind
from app.panel.services import tickets_admin
from app.services import outbox, users

logger = logging.getLogger(__name__)

_OPEN_REQUESTS = (
    "SELECT id FROM chat.tickets "
    "WHERE user_id = $1 AND brand = $2 AND status = 'awaiting' "
    "AND escalation_reason = 'brand_access_request' "
    "ORDER BY created_at DESC, id DESC"
)


@dataclass(frozen=True)
class _Target:
    id: int
    phone_e164: str


@dataclass(frozen=True)
class EditOutcome:
    result: users.UpdateResult
    concluded_tickets: tuple[int, ...] = ()
    outbound_ids: tuple[int, ...] = ()


def granted_text(brand: str, settings: Settings) -> str:
    return settings.text_brand_access_granted.format(marca=BRAND_NAMES.get(brand, brand))


async def edit_person(
    conn: Any,
    user_id: int,
    data: users.ValidatedUser,
    *,
    expected_updated_at: datetime | None,
    admin_email: str,
    admin_name: str | None,
    settings: Settings,
    clock: Clock | None = None,
) -> EditOutcome:
    concluded: list[int] = []
    outbound_ids: list[int] = []
    async with conn.transaction():
        result = await users.update(
            conn, user_id, data, expected_updated_at=expected_updated_at,
            actor=admin_email, actor_type="panel", clock=clock,
        )  # fmt: skip
        if result.status != users.UPDATE_OK or not result.granted:
            return EditOutcome(result)
        author = (admin_name or "").split()[0] if (admin_name or "").split() else admin_email
        for brand in result.granted:
            ticket_ids = [int(r["id"]) for r in await conn.fetch(_OPEN_REQUESTS, user_id, brand)]
            if not ticket_ids:
                continue
            for ticket_id in ticket_ids:
                await tickets_admin.conclude(
                    conn, ticket_id, author, actor=admin_email, clock=clock
                )
                concluded.append(ticket_id)
            if result.user_status == "active" and result.phone_e164:
                outbound_ids.append(
                    await outbox.enqueue(
                        conn, _Target(user_id, result.phone_e164),
                        OutboundKind.BRAND_ACCESS_GRANTED, body=granted_text(brand, settings),
                        ticket_id=ticket_ids[0], clock=clock,
                    )  # fmt: skip
                )
    logger.info(
        "panel_person_updated",
        extra={
            "changed": list(result.changed),
            "requests_concluded": len(concluded),
            "access_notices": len(outbound_ids),
        },
    )
    return EditOutcome(result, tuple(concluded), tuple(outbound_ids))
