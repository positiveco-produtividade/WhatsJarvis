"""Limite por IP do painel: 240 requisicoes por 60 s (spec 4.4; task PN-T63).

Middleware ASGI puro, por fora do gate de sessao: vale tambem sem sessao. So atua nos
caminhos do painel (`/`, `/dashboard*`, `/api/*`); `/static`, `/health` e o webhook (que
tem limitador proprio) ficam intactos. A chave e o IP de `client_ip` (X-Real-IP, senao o
peer; X-Forwarded-For nunca). O IP nao e logado.

Testes podem trocar o limite com `app.state.panel_rate_limit = (max, janela_em_s)` e o
relogio com `app.state.clock`; ambos sao lidos a cada requisicao.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.clock import SystemClock
from app.panel.headers import CACHE_PRIVATE, STATIC_PREFIX, is_panel_path
from app.services.rate_limit import SlidingWindowLimiter, client_ip

logger = logging.getLogger(__name__)

PANEL_RATE_MAX = 240
PANEL_RATE_WINDOW_SECONDS = 60


class PanelRateLimitMiddleware:
    """429 com `Retry-After` quando um IP passa de 240 requisicoes em 60 s."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._limiter: SlidingWindowLimiter | None = None

    def _get_limiter(self, app: FastAPI) -> SlidingWindowLimiter:
        if self._limiter is None:
            self._limiter = SlidingWindowLimiter(getattr(app.state, "clock", None) or SystemClock())
        return self._limiter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"] if scope["type"] == "http" else ""
        if scope["type"] != "http" or path.startswith(STATIC_PREFIX) or not is_panel_path(path):
            await self.app(scope, receive, send)
            return
        app: FastAPI = scope["app"]
        max_n, window = getattr(
            app.state, "panel_rate_limit", (PANEL_RATE_MAX, PANEL_RATE_WINDOW_SECONDS)
        )
        key = f"panel:{client_ip(Request(scope))}"
        wait = self._get_limiter(app).check(key, max_n, window)
        if wait:
            logger.warning("panel_rate_limited retry_after=%s", wait)
            response = JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(wait), "Cache-Control": CACHE_PRIVATE},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
