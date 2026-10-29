"""Dependencias do painel: gate de admin, CSRF e escopo de marca (spec painel.1, 4.4, 4.6).

- `current_admin`: sessao `jarvis_panel` viva; sem ela, `iam_session` -> `whoami` -> nova
  sessao. Fora de `PANEL_ADMIN_EMAILS` = 403 (sem consultar dado algum) e auditoria
  `panel.forbidden` (no maximo 1 por minuto por e-mail). Sem sessao: 302 ao IAM (HTML),
  401 (JSON, `/api/`) ou `HX-Redirect` (HTMX). IAM fora e sem sessao viva = pagina 503.
- `require_csrf`: token HMAC (cabecalho `X-CSRF-Token` ou campo `csrf`) E Origin/Referer.
- `brand_scope`: cookie `jarvis_brand` validado por `scope.parse_scope`.

Os erros sao `PanelAbort` (um `HTTPException`): numa rota comum viram o JSON generico do app
com o status certo; numa rota de `PanelRoute` viram as paginas 403/503 ou o redirecionamento.
`DEV_AUTH_EMAIL` (so com `PRODUCTION=false`; a config recusa em producao) dispensa o IAM.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Annotated, Any, Literal

import httpx
from fastapi import Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.routing import APIRoute
from fastapi.templating import Jinja2Templates

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.panel.auth import iam as iam_mod
from app.panel.auth.csrf import CSRF_FIELD, CSRF_HEADER, csrf_token, verify_csrf
from app.panel.auth.iam import IamClient, IamUnavailableError
from app.panel.auth.sessions import COOKIE_NAME, COOKIE_PARAMS, PanelSession, SessionService
from app.panel.services.scope import Scope, parse_scope
from app.panel.templating import get_templates
from app.services import audit

logger = logging.getLogger(__name__)

__all__ = [
    "BRAND_COOKIE",
    "BRAND_COOKIE_MAX_AGE",
    "PanelAbort",
    "PanelRoute",
    "brand_scope",
    "csrf_token_for",
    "current_admin",
    "get_clock",
    "get_iam",
    "iam_login_url",
    "templates_for",
    "get_sessions",
    "get_settings",
    "render_abort",
    "require_csrf",
]

BRAND_COOKIE = "jarvis_brand"
BRAND_COOKIE_MAX_AGE = 365 * 24 * 3600
FORBIDDEN_ACTION = "panel.forbidden"
FORBIDDEN_WINDOW_SECONDS = 60
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FORM_TYPES = ("application/x-www-form-urlencoded", "multipart/form-data")
API_PREFIX = "/api/"
DEV_ADMIN_NAME = "Admin (dev)"

AbortKind = Literal["login", "unauthorized", "forbidden", "iam_down"]


class PanelAbort(HTTPException):
    """Recusa do gate. `kind` decide a resposta em `PanelRoute`; `location` e o login do IAM."""

    def __init__(self, status_code: int, kind: AbortKind, location: str | None = None) -> None:
        headers = {"Location": location} if location else None
        super().__init__(status_code=status_code, detail=kind, headers=headers)
        self.kind: AbortKind = kind
        self.location = location


# ---- acesso ao estado do app -----------------------------------------------------------


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_clock(request: Request) -> Clock:
    clock: Clock | None = getattr(request.app.state, "clock", None)
    return clock if clock is not None else SystemClock()


def get_iam(request: Request) -> IamClient:
    """Cliente do IAM unico por app (criado na primeira chamada; testes injetam `panel_iam`)."""
    state = request.app.state
    client: IamClient | None = getattr(state, "panel_iam", None)
    if client is None:
        cfg = get_settings(request)
        client = IamClient(
            httpx.AsyncClient(),
            get_clock(request),
            base_url=cfg.iam_base_url,
            cache_ttl_seconds=float(cfg.iam_whoami_cache_ttl_seconds),
            timeout_seconds=cfg.iam_http_timeout_seconds,
        )
        state.panel_iam = client
    return client


def get_sessions(request: Request) -> SessionService:
    state = request.app.state
    service: SessionService | None = getattr(state, "panel_sessions", None)
    if service is None:
        cfg = get_settings(request)
        service = SessionService(
            state.pool,
            get_clock(request),
            get_iam(request),
            idle_minutes=cfg.panel_session_idle_minutes,
            absolute_hours=cfg.panel_session_absolute_hours,
        )
        state.panel_sessions = service
    return service


# ---- gate de admin ---------------------------------------------------------------------


def _is_api(request: Request) -> bool:
    return request.url.path.startswith(API_PREFIX)


def _login_abort(request: Request, cfg: Settings) -> PanelAbort:
    if _is_api(request):
        return PanelAbort(401, "unauthorized")
    host = cfg.panel_public_host
    query = f"?{request.url.query}" if request.url.query else ""
    target = f"https://{host}{request.url.path}{query}"
    location = iam_mod.login_redirect_url(
        target,
        iam_base_url=cfg.iam_base_url,
        allowed_hosts={host},
        fallback=f"https://{host}/dashboard",
    )
    return PanelAbort(302, "login", location)


async def _record_forbidden(request: Request, email: str) -> None:
    """Grava `panel.forbidden` (1 por minuto por e-mail). Nunca propaga: o 403 sai igual."""
    clock = get_clock(request)
    since = clock.now().timestamp() - FORBIDDEN_WINDOW_SECONDS
    try:
        async with request.app.state.pool.acquire() as conn:
            recent = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM ops.audit_log WHERE action = $1 AND actor = $2 "
                "AND at > to_timestamp($3))",
                FORBIDDEN_ACTION, email, since,
            )
            if not recent:
                await audit.write(
                    conn, "panel", email, FORBIDDEN_ACTION, "route", request.url.path[:200],
                    clock=clock,
                )
    except Exception as exc:  # noqa: BLE001 - auditoria do 403 nunca troca a resposta
        logger.error("panel_forbidden_audit_failed", extra={"error": type(exc).__name__})
    logger.warning("panel_forbidden")


async def _forbid(request: Request, email: str) -> PanelAbort:
    await _record_forbidden(request, email)
    return PanelAbort(403, "forbidden")


def _is_admin(cfg: Settings, email: str) -> bool:
    return email.strip().lower() in cfg.panel_admin_email_list


async def _identity(
    request: Request, cfg: Settings, iam_cookie: str | None
) -> tuple[str, str]:
    """(e-mail, nome) de quem esta entrando: `DEV_AUTH_EMAIL` (so fora de producao) ou IAM."""
    if not iam_cookie and cfg.dev_auth_email.strip() and not cfg.production:
        return cfg.dev_auth_email.strip().lower(), DEV_ADMIN_NAME
    if not iam_cookie:
        raise _login_abort(request, cfg)
    try:
        who = await get_iam(request).whoami(iam_cookie)
    except IamUnavailableError as exc:
        raise PanelAbort(503, "iam_down") from exc
    if who is None:
        raise _login_abort(request, cfg)
    return who.email.strip().lower(), who.name


async def _login(request: Request, response: Response, cfg: Settings) -> PanelSession:
    iam_cookie = request.cookies.get(iam_mod.COOKIE_NAME)
    email, name = await _identity(request, cfg, iam_cookie)
    if not _is_admin(cfg, email):
        raise await _forbid(request, email)
    session = await get_sessions(request).create(email=email, name=name)
    assert session.cookie is not None
    max_age = int((session.expires_at - get_clock(request).now()).total_seconds())
    response.set_cookie(COOKIE_NAME, session.cookie, max_age=max_age, **COOKIE_PARAMS)  # type: ignore[arg-type]
    # O FastAPI nao junta os cabecalhos de `response` quando a rota devolve um Response proprio
    # (ex.: redirect): `PanelRoute` reaplica o cookie guardado aqui.
    request.state.new_panel_cookie = (session.cookie, max_age)
    logger.info("panel_login")
    return session


async def current_admin(request: Request, response: Response) -> PanelSession:
    """Sessao do administrador logado; recusa com `PanelAbort` (302/401/403/503)."""
    cfg = get_settings(request)
    session = await get_sessions(request).resolve(
        request.cookies.get(COOKIE_NAME), request.cookies.get(iam_mod.COOKIE_NAME)
    )
    if session is None:
        return await _login(request, response, cfg)
    if not _is_admin(cfg, session.email):
        raise await _forbid(request, session.email)
    return session


# ---- CSRF ------------------------------------------------------------------------------


def csrf_token_for(request: Request, admin: PanelSession) -> str:
    """Token que as paginas colocam em `hx-headers` e no campo oculto `csrf`."""
    secret = get_settings(request).panel_csrf_secret.get_secret_value()
    return csrf_token(secret, admin.csrf_secret) if secret else ""


async def _provided_token(request: Request) -> str | None:
    header = request.headers.get(CSRF_HEADER)
    if header:
        return header
    if request.headers.get("content-type", "").startswith(FORM_TYPES):
        value = (await request.form()).get(CSRF_FIELD)
        return value if isinstance(value, str) else None
    return None


async def require_csrf(
    request: Request, admin: Annotated[PanelSession, Depends(current_admin)]
) -> None:
    """POST/PUT/DELETE: token + Origin/Referer. Falhou: 403 e nada e alterado."""
    if request.method in SAFE_METHODS:
        return
    cfg = get_settings(request)
    ok = verify_csrf(
        secret=cfg.panel_csrf_secret.get_secret_value(),
        session_csrf_secret=admin.csrf_secret,
        provided_token=await _provided_token(request),
        origin=request.headers.get("origin"),
        referer=request.headers.get("referer"),
        public_host=cfg.panel_public_host,
        allow_http=not cfg.production,
    )
    if not ok:
        logger.warning("panel_csrf_rejected")
        raise PanelAbort(403, "forbidden")


# ---- marca -----------------------------------------------------------------------------


def brand_scope(request: Request) -> Scope:
    """Escopo do cookie `jarvis_brand` (ausente ou invalido = Todas as marcas)."""
    return parse_scope(request.cookies.get(BRAND_COOKIE))


# ---- respostas de recusa ---------------------------------------------------------------


def templates_for(request: Request) -> Jinja2Templates:
    found: Jinja2Templates | None = getattr(request.app.state, "templates", None)
    return found if found is not None else get_templates()


def render_abort(request: Request, exc: PanelAbort) -> Response:
    """Pagina/redirecionamento/JSON para uma recusa do gate (sem dado algum no corpo)."""
    if exc.kind == "iam_down":
        return templates_for(request).TemplateResponse(
            request, "503_iam.html", {}, status_code=503, headers={"Retry-After": "60"}
        )
    if exc.kind == "forbidden":
        return templates_for(request).TemplateResponse(request, "403.html", {}, status_code=403)
    if exc.kind == "unauthorized":
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    location = exc.location or "/dashboard"
    if request.headers.get("hx-request"):
        return Response(status_code=401, headers={"HX-Redirect": location})
    return RedirectResponse(location, status_code=302)


def _apply_new_session_cookie(request: Request, response: Response) -> None:
    pending: tuple[str, int] | None = getattr(request.state, "new_panel_cookie", None)
    if pending is None:
        return
    already = any(
        v.decode("latin-1").startswith(f"{COOKIE_NAME}=")
        for k, v in response.headers.raw
        if k == b"set-cookie"
    )
    if not already:
        response.set_cookie(COOKIE_NAME, pending[0], max_age=pending[1], **COOKIE_PARAMS)  # type: ignore[arg-type]


class PanelRoute(APIRoute):
    """`APIRouter(route_class=PanelRoute)`: recusas do gate viram pagina 403/503 ou redirect."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                result = await original(request)
            except PanelAbort as exc:
                return render_abort(request, exc)
            _apply_new_session_cookie(request, result)
            return result

        return handler


def iam_login_url(request: Request) -> str:
    """Pagina de login do IAM (sem `next`): destino do "Sair"."""
    return f"{get_settings(request).iam_base_url.rstrip('/')}{iam_mod.LOGIN_PATH}"
