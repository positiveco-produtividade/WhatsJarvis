"""Memoria de conversa do cerebro (CB-23, CB-12, CB-13): janela das ultimas trocas do vendedor.

Sem nova copia de texto: le `chat.message_trace.body` (pergunta) e a primeira parte enviada de
`chat.outbound_messages.body` (resposta), ligadas por `chat.brain_runs`. A exclusao LGPD ja zera
esses corpos, entao a janela de quem foi excluido some sozinha. Nao importa `app.panel`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

__all__ = ["Exchange", "recent_exchanges"]

_ELIGIBLE_OUTCOMES = ("answered", "ask_brand")

_SQL = """
SELECT t.id AS trace_id, t.received_at, t.body AS question, o.body AS answer
FROM chat.message_trace t
JOIN access.users u ON u.id = t.user_id AND u.status = 'active'
JOIN chat.brain_runs r ON r.trace_id = t.id
JOIN LATERAL (
  SELECT om.body FROM chat.outbound_messages om
  WHERE om.inbound_trace_id = t.id AND om.user_id = t.user_id
    AND om.status = 'sent' AND om.body IS NOT NULL
  ORDER BY om.id LIMIT 1
) o ON true
WHERE t.user_id = $1
  AND t.id < $2
  AND t.received_at >= $3
  AND t.body IS NOT NULL
  AND r.outcome = ANY($4::text[])
  AND NOT ('injection' = ANY(r.flags))
ORDER BY t.id DESC
LIMIT $5
"""


@dataclass(frozen=True, slots=True)
class Exchange:
    """Uma troca: o que o vendedor perguntou e a primeira parte do que o Jarvis respondeu."""

    trace_id: int
    received_at: datetime
    question: str
    answer: str


async def recent_exchanges(
    conn: Any,
    *,
    user_id: int,
    trace_id: int,
    now: datetime,
    exchanges: int,
    minutes: int,
) -> list[Exchange]:
    """Ultimas `exchanges` trocas do mesmo `user_id` nos ultimos `minutes`, em ordem cronologica.

    Usuario nao `active`, corpo anulado (LGPD) e desfecho fora de answered/ask_brand ficam fora.
    """
    if exchanges <= 0 or minutes <= 0:
        return []
    since = now - timedelta(minutes=minutes)
    rows = await conn.fetch(_SQL, user_id, trace_id, since, list(_ELIGIBLE_OUTCOMES), exchanges)
    return [
        Exchange(
            trace_id=int(r["trace_id"]),
            received_at=r["received_at"],
            question=str(r["question"]),
            answer=str(r["answer"]),
        )
        for r in reversed(rows)
    ]
