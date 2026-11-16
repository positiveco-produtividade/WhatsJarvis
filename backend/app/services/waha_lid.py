"""Resolucao do numero oculto (`@lid`) do WhatsApp pelo WAHA (spec canal-waha.1, WA-8; WA-T09).

Quando o WhatsApp esconde o telefone, o evento chega com `<id>@lid`. Este modulo pergunta ao
WAHA do Jarvis qual o telefone por tras do lid. Sem resposta util, devolve None: o webhook
segue com o lid no lugar do BSUID e o remetente aparece em "Pedindo acesso" como
"Numero oculto"; nunca e tratado como autorizado.

Premissa R1 (`spec/research.json`): o endpoint e `GET /api/{sessao}/lids/{lid}` e a resposta
traz o telefone num campo como `pn` ou `phoneNumber` (`<numero>@c.us`). O extrator aceita
essas formas; a captura real no go-live confirma. Nunca levanta excecao; o log nao leva o lid
nem o numero. Cache em memoria com validade e teto de entradas.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import SecretStr

from app.core.clock import Clock, SystemClock

logger = logging.getLogger(__name__)

RESOLVED_TTL = timedelta(hours=24)
UNRESOLVED_TTL = timedelta(minutes=5)
MAX_ENTRIES = 1000
HTTP_TIMEOUT = httpx.Timeout(3.0)
_PHONE_FIELDS = ("pn", "phoneNumber", "phone", "number")


def _digits(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    local = value.split("@", 1)[0].strip().lstrip("+")
    if "@" in value and not value.endswith(("@c.us", "@s.whatsapp.net")):
        return None
    return local if local.isascii() and local.isdigit() and 8 <= len(local) <= 15 else None


def extract_phone(data: Any) -> str | None:
    """Telefone (so digitos) de uma resposta do WAHA; None se nao houver um valido."""
    if isinstance(data, list):
        data = data[0] if len(data) == 1 else None
    if not isinstance(data, dict):
        return None
    for field in _PHONE_FIELDS:
        phone = _digits(data.get(field))
        if phone is not None:
            return phone
    return None


class LidResolver:
    """Pergunta ao WAHA o telefone de um `@lid`, com cache (resolvido 24 h, nao resolvido 5 min)."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: SecretStr,
        session: str,
        client: httpx.AsyncClient,
        clock: Clock | None = None,
        max_entries: int = MAX_ENTRIES,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._api_key = api_key
        self._session = session
        self._client = client
        self._clock = clock or SystemClock()
        self._max = max_entries
        self._cache: OrderedDict[str, tuple[str | None, datetime]] = OrderedDict()

    def cached(self, lid: str) -> tuple[bool, str | None]:
        """(tem no cache valido?, telefone)."""
        hit = self._cache.get(lid)
        if hit is None:
            return False, None
        phone, expires = hit
        if self._clock.now() >= expires:
            del self._cache[lid]
            return False, None
        self._cache.move_to_end(lid)
        return True, phone

    def _remember(self, lid: str, phone: str | None) -> None:
        ttl = RESOLVED_TTL if phone is not None else UNRESOLVED_TTL
        self._cache[lid] = (phone, self._clock.now() + ttl)
        self._cache.move_to_end(lid)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    async def resolve(self, lid: str) -> str | None:
        """Telefone (so digitos, como o `wa_id` da Meta) ou None. Nunca levanta excecao."""
        if not lid.endswith("@lid"):
            return None
        hit, phone = self.cached(lid)
        if hit:
            return phone
        phone = await self._fetch(lid)
        self._remember(lid, phone)
        return phone

    async def _fetch(self, lid: str) -> str | None:
        url = f"{self._base}/api/{quote(self._session, safe='')}/lids/{quote(lid, safe='')}"
        headers = {"X-Api-Key": self._api_key.get_secret_value()}
        try:
            response = await self._client.get(url, headers=headers, timeout=HTTP_TIMEOUT)
        except httpx.HTTPError as exc:
            logger.warning("waha_lid_lookup_failed", extra={"error_type": type(exc).__name__})
            return None
        if not response.is_success:
            if response.status_code != 404:
                logger.warning("waha_lid_lookup_failed", extra={"status": response.status_code})
            return None
        try:
            data = response.json()
        except ValueError:
            logger.warning("waha_lid_lookup_failed", extra={"error_type": "invalid_json"})
            return None
        return extract_phone(data)
