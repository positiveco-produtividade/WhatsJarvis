"""Cabecalhos e cache do painel (spec 4.4.4).

Middleware ASGI puro, instalado por fora do `_request_context` de PF para que o
`Cache-Control` do painel prevaleca. So atua nos caminhos do painel: `/health`, o webhook
e demais rotas de PF ficam intactos.
"""

from __future__ import annotations

from urllib.parse import parse_qs

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Sem hashes de CDN: htmx, Alpine CSP e Chart.js vivem em /static/vendor/.
PANEL_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
CACHE_PRIVATE = "no-store, private"
CACHE_STATIC_VERSIONED = "public, max-age=31536000, immutable"
CACHE_STATIC_UNVERSIONED = "no-cache"

STATIC_PREFIX = "/static/"
# Somente caminhos do painel recebem estes cabecalhos; `/health`, o webhook e qualquer
# outra rota de PF ficam exatamente como PF as entrega.
PANEL_EXACT_PATHS = ("/",)
PANEL_PREFIXES = ("/dashboard", "/api/", STATIC_PREFIX)

PANEL_HEADERS = {
    "Content-Security-Policy": PANEL_CSP,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def is_panel_path(path: str) -> bool:
    """Verdadeiro para `/`, `/dashboard*`, `/api/*` e `/static/*`."""
    return path in PANEL_EXACT_PATHS or path.startswith(PANEL_PREFIXES)


def cache_control_for(path: str, query_string: bytes) -> str:
    """Estaticos com `?v=` cacheiam por muito tempo; todo o resto e `no-store, private`."""
    if not path.startswith(STATIC_PREFIX):
        return CACHE_PRIVATE
    if "v" in parse_qs(query_string.decode("latin-1")):
        return CACHE_STATIC_VERSIONED
    return CACHE_STATIC_UNVERSIONED


class PanelHeadersMiddleware:
    """Aplica CSP, anti-framing e cache a toda resposta do painel (HTML, JSON, estatico)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not is_panel_path(scope["path"]):
            await self.app(scope, receive, send)
            return
        cache = cache_control_for(scope["path"], scope.get("query_string", b""))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in PANEL_HEADERS.items():
                    headers[name] = value
                headers["Cache-Control"] = cache
            await send(message)

        await self.app(scope, receive, send_with_headers)
