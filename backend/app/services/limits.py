"""Limite de mensagens por hora (spec 4.4.5, AC-29).

Roda na transacao do chamador, com a linha do usuario ja travada (`FOR UPDATE` feito
na resolucao de identidade): isso serializa mensagens concorrentes do mesmo usuario,
entao a 61a nao passa. A janela e deslizante, o relogio vem por parametro e so
mensagens aceitas (`counted_in_limit`) contam: as bloqueadas nao prolongam a janela.
Esta camada nao cria alerta nem aviso; quem decide e o pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

DEFAULT_LIMIT = 60
DEFAULT_WINDOW_MINUTES = 60

_COUNT_SQL = (
    "SELECT count(*) FROM chat.message_trace "
    "WHERE user_id = $1::bigint AND counted_in_limit AND received_at > $2::timestamptz"
)


@dataclass(frozen=True)
class LimitResult:
    """`allowed` False = bloqueada (`rate_limited`); `count` = aceitas na janela."""

    allowed: bool
    count: int


async def check_and_count(
    conn: Any,
    user_id: int,
    now: datetime,
    *,
    limit: int = DEFAULT_LIMIT,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> LimitResult:
    """Conta as mensagens aceitas na janela; permitida se ainda houver vaga.

    O chamador grava `counted_in_limit = result.allowed` no rastro da mensagem.
    """
    since = now - timedelta(minutes=window_minutes)
    count = int(await conn.fetchval(_COUNT_SQL, user_id, since))
    return LimitResult(allowed=count < limit, count=count)


def rate_limit_notice_due(
    last_notice_at: datetime | None,
    now: datetime,
    *,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
) -> bool:
    """Um aviso por janela: devido se nunca houve ou o ultimo tem >= a janela."""
    if last_notice_at is None:
        return True
    return now - last_notice_at >= timedelta(minutes=window_minutes)
