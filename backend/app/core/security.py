"""Assinaturas HMAC (Meta e WAHA) e comparacoes em tempo constante, sempre sobre bytes."""
from __future__ import annotations

import hashlib
import hmac

from pydantic import SecretStr

SIGNATURE_PREFIX = "sha256="


def _secret_bytes(secret: str | SecretStr) -> bytes:
    raw = secret.get_secret_value() if isinstance(secret, SecretStr) else secret
    return raw.encode("utf-8")


def compute_hub_signature(raw: bytes, secret: str | SecretStr) -> str:
    """Devolve o valor do header X-Hub-Signature-256 para os bytes brutos."""
    digest = hmac.new(_secret_bytes(secret), raw, hashlib.sha256).hexdigest()
    return SIGNATURE_PREFIX + digest


def verify_hub_signature(raw: bytes, header: str | None, secret: str | SecretStr) -> bool:
    """Confere a assinatura sobre os bytes brutos do corpo (AC-2).

    Header ausente, sem o prefixo ``sha256=``, com caractere nao ASCII ou
    segredo vazio devolvem False; nunca levanta excecao.
    """
    if not header or not _secret_bytes(secret):
        return False
    if not header.startswith(SIGNATURE_PREFIX):
        return False
    try:
        received = header.encode("ascii")
    except UnicodeEncodeError:
        return False
    expected = compute_hub_signature(raw, secret).encode("ascii")
    return hmac.compare_digest(received, expected)


WAHA_HMAC_ALGORITHM = "sha512"


def compute_waha_hmac(raw: bytes, secret: str | SecretStr) -> str:
    """Valor esperado do header X-Webhook-Hmac do WAHA: HMAC SHA-512 hex do corpo bruto."""
    return hmac.new(_secret_bytes(secret), raw, hashlib.sha512).hexdigest()


def verify_waha_hmac(
    raw: bytes,
    header: str | None,
    algorithm: str | None,
    secret: str | SecretStr,
) -> bool:
    """Confere a assinatura do webhook do WAHA (spec canal-waha.1, WA-4).

    Exige ``X-Webhook-Hmac-Algorithm`` igual a ``sha512`` e o HMAC hex do corpo bruto em
    ``X-Webhook-Hmac`` (maiusculas ou minusculas). Header ausente, algoritmo diferente,
    caractere nao ASCII ou segredo vazio devolvem False; nunca levanta excecao.
    """
    if not header or not _secret_bytes(secret):
        return False
    if (algorithm or "").strip().lower() != WAHA_HMAC_ALGORITHM:
        return False
    try:
        received = header.strip().lower().encode("ascii")
    except UnicodeEncodeError:
        return False
    expected = compute_waha_hmac(raw, secret).encode("ascii")
    return hmac.compare_digest(received, expected)


def constant_time_equals(provided: str | None, expected: str | SecretStr) -> bool:
    """Comparacao em tempo constante (verify token do GET do webhook)."""
    if provided is None:
        return False
    expected_bytes = _secret_bytes(expected)
    if not expected_bytes:
        return False
    provided_bytes = provided.encode("utf-8", errors="surrogatepass")
    return hmac.compare_digest(provided_bytes, expected_bytes)
