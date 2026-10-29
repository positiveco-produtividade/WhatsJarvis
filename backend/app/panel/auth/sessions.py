"""Sessao propria do painel sobre o cookie do IAM (spec painel.1, 4.4.2).

Cookie opaco `jarvis_panel` (256 bits aleatorios); so o SHA-256 vai a `ops.panel_sessions`.
Ociosa 2 h, absoluta 12 h (config). Revalida no IAM a cada 5 min: 401 revoga; IAM fora do
ar NAO derruba sessao viva (segue ate `expires_at`). Cada operacao pega e solta sua
propria conexao do pool, e a chamada ao IAM acontece sem conexao presa.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

import asyncpg

from app.core.clock import Clock
from app.panel.auth.iam import IamClient, IamUnavailableError

logger = logging.getLogger(__name__)

__all__ = [
    "COOKIE_NAME",
    "COOKIE_PARAMS",
    "PanelSession",
    "SessionService",
    "hash_session_id",
]

COOKIE_NAME = "jarvis_panel"
COOKIE_PARAMS: dict[str, object] = {
    "httponly": True,
    "secure": True,
    "samesite": "lax",
    "path": "/",
}
REVALIDATE_SECONDS = 300
TOUCH_SECONDS = 60


@dataclass(frozen=True, slots=True)
class PanelSession:
    """Sessao viva. `cookie` so e preenchido na criacao (o banco guarda so o hash)."""

    id_hash: str
    email: str
    name: str
    csrf_secret: str
    expires_at: datetime
    cookie: str | None = None


def hash_session_id(cookie: str) -> str:
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()


class SessionService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        clock: Clock,
        iam: IamClient,
        *,
        idle_minutes: int = 120,
        absolute_hours: int = 12,
        revalidate_seconds: int = REVALIDATE_SECONDS,
    ) -> None:
        self._pool = pool
        self._clock = clock
        self._iam = iam
        self._idle = timedelta(minutes=idle_minutes)
        self._absolute = timedelta(hours=absolute_hours)
        self._revalidate = timedelta(seconds=revalidate_seconds)

    async def create(self, *, email: str, name: str) -> PanelSession:
        """Nova sessao (apos `whoami` valido no login). Devolve o cookie cru uma unica vez."""
        cookie = secrets.token_urlsafe(32)
        csrf_secret = secrets.token_urlsafe(32)
        now = self._clock.now()
        expires = now + self._absolute
        id_hash = hash_session_id(cookie)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO ops.panel_sessions (id_hash, email, name, csrf_secret, created_at, "
                "last_seen_at, last_iam_check_at, expires_at) VALUES ($1,$2,$3,$4,$5,$5,$5,$6)",
                id_hash, email, name, csrf_secret, now, expires,
            )
        return PanelSession(id_hash, email, name, csrf_secret, expires, cookie)

    async def revoke(self, cookie: str | None) -> None:
        """Sair: revoga a linha (idempotente)."""
        if cookie:
            await self._revoke_hash(hash_session_id(cookie))

    async def _revoke_hash(self, id_hash: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE ops.panel_sessions SET revoked_at = $2 "
                "WHERE id_hash = $1 AND revoked_at IS NULL",
                id_hash, self._clock.now(),
            )

    async def resolve(self, cookie: str | None, iam_cookie: str | None) -> PanelSession | None:
        """Sessao viva do cookie ou `None` (ausente, revogada, vencida, ociosa, 401 do IAM)."""
        if not cookie:
            return None
        id_hash = hash_session_id(cookie)
        now = self._clock.now()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT email, name, csrf_secret, expires_at, last_seen_at, last_iam_check_at "
                "FROM ops.panel_sessions WHERE id_hash = $1 AND revoked_at IS NULL",
                id_hash,
            )
        if row is None or row["expires_at"] <= now or row["last_seen_at"] + self._idle <= now:
            return None
        if row["last_iam_check_at"] + self._revalidate <= now:
            if not await self._iam_still_valid(id_hash, row["email"], iam_cookie):
                return None
        if row["last_seen_at"] + timedelta(seconds=TOUCH_SECONDS) <= now:
            await self._touch(id_hash, now)
        return PanelSession(
            id_hash, row["email"], row["name"], row["csrf_secret"], row["expires_at"]
        )

    async def _touch(self, id_hash: str, now: datetime) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE ops.panel_sessions SET last_seen_at = $2 WHERE id_hash = $1", id_hash, now
            )

    async def _iam_still_valid(self, id_hash: str, email: str, iam_cookie: str | None) -> bool:
        """401 (ou outra identidade) revoga; IAM fora do ar mantem a sessao viva."""
        try:
            who = await self._iam.whoami(iam_cookie or "")
        except IamUnavailableError:
            logger.warning("panel.session: IAM indisponivel; sessao viva mantida")
            return True
        if who is None or who.email.lower() != email.lower():
            await self._revoke_hash(id_hash)
            return False
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE ops.panel_sessions SET last_iam_check_at = $2 WHERE id_hash = $1",
                id_hash, self._clock.now(),
            )
        return True
