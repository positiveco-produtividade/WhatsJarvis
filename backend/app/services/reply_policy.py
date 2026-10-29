"""Politica de resposta (R4): pausa e marca desligada. Definida aqui para evitar import circular.

A pausa silencia so respostas conversacionais; mensagens de consentimento (boas-vindas,
lembrete, confirmacao e recusa) continuam saindo. Marca desligada nega so respostas
ligadas a essa marca. O outbox importa daqui.
"""

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.values import ReplyKind
from app.services.flags import Flags

REASON_PAUSED = "paused"
REASON_BRAND_DISABLED = "brand_disabled"

# Consentimento/LGPD nao depende de pausa (decisao "pausa x opt-in", spec 4.9).
PAUSE_EXEMPT_KINDS: frozenset[str] = frozenset(
    {
        ReplyKind.WELCOME_TEMPLATE.value,
        ReplyKind.OPTIN_REMINDER.value,
        ReplyKind.OPTIN_CONFIRMATION.value,
        ReplyKind.OPTIN_DECLINED.value,
    }
)


@dataclass(frozen=True)
class PolicyDecision:
    ok: bool
    reason: str | None = None


ALLOWED = PolicyDecision(ok=True)


class ReplyPolicy(Protocol):
    async def allow(
        self, conn: Any, user: Any, kind: str, brand: str | None = None
    ) -> PolicyDecision: ...


class AllowAllPolicy:
    """Padrao do outbox e dos testes: permite tudo."""

    async def allow(
        self, conn: Any, user: Any, kind: str, brand: str | None = None
    ) -> PolicyDecision:
        return ALLOWED


class FlagsReplyPolicy:
    def __init__(self, flags: Flags) -> None:
        self._flags = flags

    async def brand_allows(self, brand: str, conn: Any | None = None) -> bool:
        return await self._flags.brand_enabled(brand, conn=conn)

    async def allow(
        self, conn: Any, user: Any, kind: str, brand: str | None = None
    ) -> PolicyDecision:
        kind_value = str(kind)
        if kind_value not in PAUSE_EXEMPT_KINDS and await self._flags.paused(conn=conn):
            return PolicyDecision(ok=False, reason=REASON_PAUSED)
        if brand is not None and not await self.brand_allows(brand, conn):
            return PolicyDecision(ok=False, reason=REASON_BRAND_DISABLED)
        return ALLOWED
