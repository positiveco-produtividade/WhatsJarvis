"""Dubles de teste do painel (dono: PN-T05). Outras tarefas so os importam.

Reexporta `FakeClock` (app.core.clock) e `FakeChannel` (tests.fakes, de PF) para que
os testes do painel tenham um unico ponto de importacao. Nada aqui toca rede ou banco.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import httpx

from app.core.clock import FakeClock
from tests.fakes import FakeChannel

__all__ = [
    "FakeChannel",
    "FakeClock",
    "FakeIam",
    "HttpSpy",
    "IamMode",
    "ResendSpy",
    "WahaSpy",
    "whoami_payload",
]

IamMode = Literal["ok", "unauthorized", "timeout", "server_error"]

IAM_BASE_URL = "https://accounts.positiveco.com.br"
WHOAMI_PATH = "/api/v1/auth/whoami"


def whoami_payload(
    email: str = "admin@example.test",
    *,
    user_id: int = 1,
    name: str = "Admin Teste",
    is_superadmin: bool = False,
) -> dict[str, object]:
    """Corpo do `whoami` do IAM (mesmo formato do AUTO-1)."""
    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "area": None,
        "is_superadmin": is_superadmin,
    }


@dataclass
class FakeIam:
    """IAM em memoria sobre `httpx.MockTransport`.

    `mode` pode mudar a qualquer momento: ok (200), unauthorized (401), timeout
    (levanta `httpx.ReadTimeout`) ou server_error (503). `requests` guarda as
    chamadas na ordem em que chegaram.
    """

    mode: IamMode = "ok"
    email: str = "admin@example.test"
    user_id: int = 1
    name: str = "Admin Teste"
    requests: list[httpx.Request] = field(default_factory=list)

    @property
    def calls(self) -> int:
        return len(self.requests)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("iam timeout (fake)", request=request)
        if self.mode == "unauthorized":
            return httpx.Response(401, json={"detail": "unauthorized"})
        if self.mode == "server_error":
            return httpx.Response(503, json={"detail": "unavailable"})
        payload = whoami_payload(self.email, user_id=self.user_id, name=self.name)
        return httpx.Response(200, json=payload)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> httpx.AsyncClient:
        """`AsyncClient` apontado para o IAM falso (base_url do IAM de producao)."""
        return httpx.AsyncClient(transport=self.transport(), base_url=IAM_BASE_URL)


class HttpSpy:
    """Espiao HTTP: registra requisicoes e responde 200 (ou o que `responder` decidir)."""

    def __init__(
        self,
        responder: Callable[[httpx.Request], httpx.Response] | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self._responder = responder
        self.fail_with: int | None = None

    @property
    def calls(self) -> int:
        return len(self.requests)

    def bodies(self) -> list[object]:
        """Corpos JSON das chamadas (ou o texto cru quando nao for JSON)."""
        out: list[object] = []
        for request in self.requests:
            try:
                out.append(json.loads(request.content))
            except ValueError:
                out.append(request.content.decode("utf-8", errors="replace"))
        return out

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={"error": "fake_failure"})
        if self._responder is not None:
            return self._responder(request)
        return self.ok_response()

    def ok_response(self) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())


class ResendSpy(HttpSpy):
    """Resend (e-mail): responde 200 com id; `fail_with=500` simula falha."""

    def ok_response(self) -> httpx.Response:
        return httpx.Response(200, json={"id": f"email_fake_{len(self.requests)}"})

    def emails(self) -> list[dict[str, object]]:
        return [b for b in self.bodies() if isinstance(b, dict)]


class WahaSpy(HttpSpy):
    """WAHA (grupo da equipe): responde 201; deve ficar sem chamadas com a chave desligada."""

    def ok_response(self) -> httpx.Response:
        return httpx.Response(201, json={"id": f"waha_fake_{len(self.requests)}"})
