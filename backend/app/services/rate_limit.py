"""Limite HTTP por IP em janela deslizante, em memoria (adaptado do AUTO-1).

A chave do IP vem so de X-Real-IP (o nginx o define a partir do peer TCP e
descarta o valor enviado pelo cliente) ou do peer da conexao. X-Forwarded-For
nunca e lido: qualquer cliente consegue forja-lo. O IP nao e logado.
Implantacao com um unico worker (PM2); com mais workers o limite vale por processo.
"""

from __future__ import annotations

import math
import threading
from collections import deque
from datetime import datetime

from fastapi import HTTPException, Request, status

from app.config import get_settings
from app.core.clock import Clock, SystemClock

_SWEEP_EVERY = 1024  # varredura de chaves ociosas a cada N chamadas


def client_ip(request: Request) -> str:
    """IP do peer: X-Real-IP, senao o peer TCP, senao 'unknown'."""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real
    if request.client:
        return request.client.host
    return "unknown"


class SlidingWindowLimiter:
    """Janela deslizante por chave, com relogio injetavel e thread-safe."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self._hits: dict[str, deque[datetime]] = {}
        self._lock = threading.Lock()
        self._calls = 0

    def check(self, key: str, max_n: int, window_seconds: float) -> int:
        """Registra o acesso e devolve 0; se estourou, devolve Retry-After (s) e nao registra."""
        now = self._clock.now()
        with self._lock:
            self._calls += 1
            if self._calls % _SWEEP_EVERY == 0:
                self._sweep(now, window_seconds)
            hits = self._hits.setdefault(key, deque())
            while hits and (now - hits[0]).total_seconds() >= window_seconds:
                hits.popleft()
            if len(hits) >= max_n:
                wait = window_seconds - (now - hits[0]).total_seconds()
                return max(1, math.ceil(wait))
            hits.append(now)
            return 0

    def _sweep(self, now: datetime, window_seconds: float) -> None:
        idle = [
            k
            for k, dq in self._hits.items()
            if not dq or (now - dq[-1]).total_seconds() >= window_seconds
        ]
        for k in idle:
            del self._hits[k]

    def size(self) -> int:
        with self._lock:
            return len(self._hits)


class WebhookRateLimiter:
    """Limite do webhook (RL_WEBHOOK_MAX por RL_WEBHOOK_WINDOW s) por IP."""

    def __init__(self, max_n: int, window_seconds: int, clock: Clock | None = None) -> None:
        self._max = max_n
        self._window = window_seconds
        self._limiter = SlidingWindowLimiter(clock)

    def retry_after(self, request: Request) -> int:
        """0 se permitido; senao segundos ate poder tentar de novo."""
        return self._limiter.check(f"webhook:{client_ip(request)}", self._max, self._window)

    def __call__(self, request: Request) -> None:
        """Dependencia FastAPI: 429 com Retry-After quando estourar."""
        wait = self.retry_after(request)
        if wait:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": str(wait)},
            )


_webhook_limiter: WebhookRateLimiter | None = None
_webhook_lock = threading.Lock()


def get_webhook_limiter() -> WebhookRateLimiter:
    """Singleton do processo, configurado por Settings na primeira chamada."""
    global _webhook_limiter
    with _webhook_lock:
        if _webhook_limiter is None:
            s = get_settings()
            _webhook_limiter = WebhookRateLimiter(s.rl_webhook_max, s.rl_webhook_window)
        return _webhook_limiter


def rate_limit_webhook(request: Request) -> None:
    """Dependencia FastAPI do webhook (usa o singleton)."""
    get_webhook_limiter()(request)
