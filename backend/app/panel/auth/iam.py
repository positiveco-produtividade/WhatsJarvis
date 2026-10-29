"""Cliente do IAM (accounts.positiveco.com.br) para o painel (spec painel.1, 4.4.2).

Mesmo contrato do `services/iam.py` do AUTO-1 (`GET /api/v1/auth/whoami` com o cookie
`iam_session`), mas sem estado global: o `IamClient` recebe o `httpx.AsyncClient` e o
relogio, o que permite testar sem rede e sem dormir.

- cache LRU limitado (padrao 30 s), chaveado pelo hash do cookie (o valor cru nunca fica
  guardado nem vai para o log);
- single-flight: falhas de cache concorrentes para o mesmo cookie fazem 1 chamada so;
- disjuntor: 3 falhas seguidas abrem por 10 s (chamadas rejeitadas sem rede);
- timeout de 3 s por chamada;
- allow-list SSRF: `base_url` precisa ser https e de um host permitido;
- `safe_next_url`: o `next` do login so aponta para hosts permitidos (sempre https).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import OrderedDict
from collections.abc import Collection
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app.core.clock import Clock

logger = logging.getLogger(__name__)

IAM_ALLOWED_HOSTS: frozenset[str] = frozenset({"accounts.positiveco.com.br"})
WHOAMI_PATH = "/api/v1/auth/whoami"
LOGIN_PATH = "/login"
COOKIE_NAME = "iam_session"
MAX_COOKIE_LEN = 128
MAX_NEXT_LEN = 1800
BREAKER_THRESHOLD = 3
BREAKER_OPEN_SECONDS = 10.0
DEFAULT_CACHE_TTL_SECONDS = 30.0
DEFAULT_CACHE_MAX_ENTRIES = 1024
DEFAULT_TIMEOUT_SECONDS = 3.0


@dataclass(frozen=True)
class WhoamiResult:
    """Identidade devolvida pelo IAM (mesmo formato do AUTO-1)."""

    user_id: int
    email: str
    name: str
    area: dict[str, object] | None
    is_superadmin: bool


class IamUnavailableError(Exception):
    """IAM fora, lento, com resposta invalida ou disjuntor aberto."""


def _cache_key(cookie: str) -> str:
    return hashlib.sha256(cookie.encode("utf-8")).hexdigest()


def _check_base_url(base_url: str, allowed_hosts: Collection[str]) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("IAM base_url: use https e um host da allow-list")
    return base_url.rstrip("/")


def _parse_whoami(data: object) -> WhoamiResult:
    if not isinstance(data, dict):
        raise ValueError("corpo nao e objeto")
    area = data.get("area")
    return WhoamiResult(
        user_id=int(data["user_id"]),
        email=str(data["email"]),
        name=str(data["name"]),
        area=area if isinstance(area, dict) else None,
        is_superadmin=bool(data.get("is_superadmin", False)),
    )


class IamClient:
    """`whoami` com cache, single-flight e disjuntor. Um por processo."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        base_url: str,
        allowed_hosts: Collection[str] = IAM_ALLOWED_HOSTS,
        cache_ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        cache_max_entries: int = DEFAULT_CACHE_MAX_ENTRIES,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._http = http
        self._clock = clock
        self._base = _check_base_url(base_url, allowed_hosts)
        self._ttl = cache_ttl_seconds
        self._max = cache_max_entries
        self._timeout = timeout_seconds
        self._cache: OrderedDict[str, tuple[float, WhoamiResult]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[WhoamiResult | None]] = {}
        self._failures = 0
        self._open_until = 0.0

    def _now(self) -> float:
        return self._clock.now().timestamp()

    @property
    def breaker_open(self) -> bool:
        return self._open_until > self._now()

    def _cache_get(self, key: str) -> WhoamiResult | None:
        entry = self._cache.get(key)
        if entry is None:
            return None
        if entry[0] <= self._now():
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return entry[1]

    def _cache_set(self, key: str, value: WhoamiResult) -> None:
        self._cache[key] = (self._now() + self._ttl, value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures >= BREAKER_THRESHOLD:
            self._open_until = self._now() + BREAKER_OPEN_SECONDS
            logger.warning("iam: disjuntor aberto por %.0f s", BREAKER_OPEN_SECONDS)

    async def whoami(self, cookie: str) -> WhoamiResult | None:
        """Identidade do cookie; `None` se o IAM disse 401; `IamUnavailableError` se fora."""
        if not cookie or len(cookie) > MAX_COOKIE_LEN:
            return None
        key = _cache_key(cookie)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        if self.breaker_open:
            raise IamUnavailableError("disjuntor aberto")
        pending = self._inflight.get(key)
        if pending is not None:
            return await pending
        future: asyncio.Future[WhoamiResult | None] = asyncio.get_running_loop().create_future()
        self._inflight[key] = future
        try:
            result = await self._fetch(cookie, key)
        except IamUnavailableError as exc:
            future.set_exception(exc)
            future.exception()  # marca como lida (sem seguidores nao ha aviso do asyncio)
            raise
        except asyncio.CancelledError:
            future.set_exception(IamUnavailableError("consulta cancelada"))
            future.exception()
            raise
        else:
            future.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)

    async def _fetch(self, cookie: str, key: str) -> WhoamiResult | None:
        try:
            response = await self._http.get(
                f"{self._base}{WHOAMI_PATH}",
                headers={"Cookie": f"{COOKIE_NAME}={cookie}"},
                timeout=self._timeout,
            )
        except httpx.HTTPError as exc:
            self._record_failure()
            raise IamUnavailableError(f"falha de rede: {type(exc).__name__}") from exc
        if response.status_code == 401:
            self._failures = 0
            return None
        if response.status_code != 200:
            self._record_failure()
            raise IamUnavailableError(f"status inesperado {response.status_code}")
        try:
            result = _parse_whoami(response.json())
        except (ValueError, KeyError, TypeError) as exc:
            self._record_failure()
            raise IamUnavailableError("resposta invalida do IAM") from exc
        self._failures = 0
        self._cache_set(key, result)
        return result


def safe_next_url(candidate: str, *, allowed_hosts: Collection[str], fallback: str) -> str:
    """`candidate` se for https de um host permitido; senao `fallback`.

    Rejeita http (loop de login observado no AUTO-1 em 2026-05-20), usuario/senha na URL,
    barra invertida, caracteres de controle e URLs longas demais.
    """
    if not candidate or len(candidate) > MAX_NEXT_LEN:
        return fallback
    if "\\" in candidate or any(ord(c) < 32 or ord(c) == 127 for c in candidate):
        return fallback
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        return fallback
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return fallback
    if hostname is None or hostname not in allowed_hosts:
        return fallback
    return candidate


def login_redirect_url(
    next_url: str,
    *,
    iam_base_url: str,
    allowed_hosts: Collection[str],
    fallback: str,
) -> str:
    """URL de login do IAM com `next` ja validado e codificado."""
    base = _check_base_url(iam_base_url, IAM_ALLOWED_HOSTS)
    safe = safe_next_url(next_url, allowed_hosts=allowed_hosts, fallback=fallback)
    return f"{base}{LOGIN_PATH}?next={quote(safe, safe='')}"
