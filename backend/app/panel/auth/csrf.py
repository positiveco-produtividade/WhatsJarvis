"""CSRF do painel (spec painel.1, 4.4.3): token HMAC + checagem de Origin/Referer.

Os dois controles sao obrigatorios em todo POST/PUT/DELETE. Comparacao do token em
tempo constante. Funcoes puras: quem monta a requisicao (deps.py) passa os cabecalhos.
"""
from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection
from urllib.parse import urlparse

__all__ = ["CSRF_FIELD", "CSRF_HEADER", "check_origin", "csrf_token", "verify_csrf", "verify_token"]

CSRF_HEADER = "X-CSRF-Token"
CSRF_FIELD = "csrf"


def csrf_token(secret: str, session_csrf_secret: str) -> str:
    """`HMAC-SHA256(PANEL_CSRF_SECRET, csrf_secret_da_sessao)` em hexadecimal."""
    return hmac.new(
        secret.encode("utf-8"), session_csrf_secret.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def verify_token(secret: str, session_csrf_secret: str, provided: str | None) -> bool:
    """Compara o token recebido com o esperado em tempo constante; vazio nunca passa."""
    if not secret or not session_csrf_secret or not provided:
        return False
    expected = csrf_token(secret, session_csrf_secret)
    return hmac.compare_digest(expected.encode("utf-8"), provided.encode("utf-8", "replace"))


def _host_of(url: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError:
        return None, None
    return parsed.scheme, parsed.hostname


def check_origin(
    origin: str | None,
    referer: str | None,
    public_host: str,
    *,
    extra_hosts: Collection[str] = (),
    allow_http: bool = False,
) -> bool:
    """`Origin` (ou, na falta dele, `Referer`) tem de ser do host publico do painel.

    Sem nenhum dos dois, ou com `Origin: null`, falha. Esquema precisa ser https
    (http so com `allow_http`, para desenvolvimento local).
    """
    source = origin if origin else referer
    if not source or source == "null":
        return False
    scheme, host = _host_of(source)
    if host is None or scheme is None:
        return False
    allowed_schemes = {"https", "http"} if allow_http else {"https"}
    if scheme not in allowed_schemes:
        return False
    return host.lower() in {public_host.lower(), *(h.lower() for h in extra_hosts)}


def verify_csrf(
    *,
    secret: str,
    session_csrf_secret: str,
    provided_token: str | None,
    origin: str | None,
    referer: str | None,
    public_host: str,
    allow_http: bool = False,
) -> bool:
    """Token valido E origem valida. Falhou qualquer um: `False` (o chamador responde 403)."""
    token_ok = verify_token(secret, session_csrf_secret, provided_token)
    origin_ok = check_origin(origin, referer, public_host, allow_http=allow_http)
    return token_ok and origin_ok
