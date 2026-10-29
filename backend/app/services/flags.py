"""Flags operacionais em ops.system_state (R4): paused, brand_enabled:<marca>, team_group_enabled.

Leitura com cache por processo (TTL 30 s, relogio injetavel). set_bool grava,
audita na mesma transacao e invalida o cache. A pausa so silencia respostas
(a decisao fica no FlagsReplyPolicy, PF-17).
"""

import json
from typing import Any

from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidFlagKey
from app.core.values import BRANDS
from app.services import audit

CACHE_TTL_SECONDS = 30.0
TEAM_GROUP_KEY = "team_group_enabled"
PAUSED_KEY = "paused"
UNKNOWN_FIRST_TEXT_KEY = "unknown_first_text_display"
BRAND_PREFIX = "brand_enabled:"
FLAG_AUDIT_ACTION = "flag.set"


def brand_key(brand: str) -> str:
    return f"{BRAND_PREFIX}{brand}"


def default_for(key: str) -> bool:
    """Padrao de cada chave aceita; levanta InvalidFlagKey para as demais."""
    if key in (TEAM_GROUP_KEY, PAUSED_KEY, UNKNOWN_FIRST_TEXT_KEY):
        return False
    if key.startswith(BRAND_PREFIX) and key[len(BRAND_PREFIX) :] in BRANDS:
        return True
    raise InvalidFlagKey(key)


class Flags:
    """pool: asyncpg.Pool (so leitura). team_group_initial vem de Settings."""

    def __init__(
        self, pool: Any, clock: Clock | None = None, team_group_initial: bool = False
    ) -> None:
        self._pool = pool
        self._clock: Clock = clock or SystemClock()
        self._team_group_initial = team_group_initial
        self._cache: dict[str, tuple[float, bool | None]] = {}

    def _monotonic(self) -> float:
        return self._clock.now().timestamp()

    async def _stored(self, key: str, conn: Any | None = None) -> bool | None:
        """Valor gravado (None se ausente ou nao booleano), com cache de 30 s.

        Com `conn`, le por essa conexao (quem ja segura uma conexao do pool nao pede outra:
        evita deadlock de pool); sem ela, le pelo pool.
        """
        entry = self._cache.get(key)
        if entry is not None and self._monotonic() < entry[0]:
            return entry[1]
        reader = conn if conn is not None else self._pool
        raw = await reader.fetchval(
            "SELECT value::text FROM ops.system_state WHERE key = $1", key
        )
        parsed = json.loads(raw) if raw is not None else None
        value = parsed if isinstance(parsed, bool) else None
        self._cache[key] = (self._monotonic() + CACHE_TTL_SECONDS, value)
        return value

    async def get_bool(self, key: str, default: bool, *, conn: Any | None = None) -> bool:
        stored = await self._stored(key, conn)
        return default if stored is None else stored

    async def set_bool(
        self, conn: Any, key: str, value: bool, actor: str, actor_type: str = "cli"
    ) -> None:
        """Grava e audita na transacao de `conn` (o chamador abre/commita)."""
        default = default_for(key)
        now = self._clock.now()
        old_raw = await conn.fetchval(
            "SELECT value::text FROM ops.system_state WHERE key = $1 FOR UPDATE", key
        )
        old = json.loads(old_raw) if old_raw is not None else default
        await conn.execute(
            "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
            "VALUES ($1, $2::jsonb, $3, $4) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by",
            key,
            json.dumps(value),
            now,
            actor,
        )
        await audit.write(
            conn, actor_type, actor, FLAG_AUDIT_ACTION, "flag", key, old, value, clock=self._clock
        )
        self._cache.pop(key, None)

    async def team_group_enabled(self, *, conn: Any | None = None) -> bool:
        return await self.get_bool(TEAM_GROUP_KEY, self._team_group_initial, conn=conn)

    async def paused(self, *, conn: Any | None = None) -> bool:
        return await self.get_bool(PAUSED_KEY, False, conn=conn)

    async def brand_enabled(self, brand: str, *, conn: Any | None = None) -> bool:
        key = brand_key(brand)
        return await self.get_bool(key, default_for(key), conn=conn)
