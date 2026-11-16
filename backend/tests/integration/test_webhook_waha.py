"""Borda HTTP do webhook do WAHA, sem banco (spec canal-waha.1, WA-4, WA-5, WA-8, WA-9; WA-T10)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.webhook_waha import router
from app.config import Settings
from app.core.errors import TransientDbError
from app.core.security import compute_waha_hmac
from app.core.values import MessageOutcome
from app.services.counters import SecurityCounters
from app.services.inbound import ContentKind, InboundMessage, InboundStatus, Sender
from app.services.rate_limit import WebhookRateLimiter

HMAC_KEY = "hmac-waha-de-teste"
URL = "/webhook/waha"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "waha"
SELLER = "5500000000002"
LID = "100000000000001@lid"


class FakeProcessor:
    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []
        self.statuses: list[InboundStatus] = []
        self.fail = False
        self.outcome = MessageOutcome(outbound_ids=(7,))

    async def handle_message(self, msg: InboundMessage) -> MessageOutcome:
        self.messages.append(msg)
        if self.fail:
            raise TransientDbError("pool esgotado")
        return self.outcome

    async def handle_status(self, st: InboundStatus) -> MessageOutcome | None:
        self.statuses.append(st)
        return None


class FakeResolver:
    def __init__(self, phone: str | None) -> None:
        self.phone = phone
        self.asked: list[str] = []

    async def resolve(self, lid: str) -> str | None:
        self.asked.append(lid)
        return self.phone


class FakeSessionSink:
    def __init__(self) -> None:
        self.states: list[str] = []

    async def on_session_event(self, state: str) -> None:
        self.states.append(state)


class Env:
    def __init__(self, rl_max: int = 600, resolver: FakeResolver | None = None) -> None:
        self.settings = Settings(  # type: ignore[call-arg]
            _env_file=None,
            channel_provider="waha",
            database_url="postgresql://u:p@localhost/db_test",
            jarvis_waha_api_key="chave",
            jarvis_waha_hmac_key=HMAC_KEY,
            jarvis_whatsapp_number="+5500000000001",
        )
        self.processor = FakeProcessor()
        self.counters = SecurityCounters()
        self.sessions = FakeSessionSink()
        self.dispatched: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        app = FastAPI()
        app.include_router(router)
        app.state.settings = self.settings
        app.state.counters = self.counters
        app.state.processor = self.processor
        app.state.webhook_limiter = WebhookRateLimiter(rl_max, 60)
        app.state.outbound_dispatcher = self._dispatch
        app.state.session_monitor = self.sessions
        if resolver is not None:
            app.state.lid_resolver = resolver
        self.client = TestClient(app)

    async def _dispatch(self, outbound: tuple[int, ...], deliveries: tuple[int, ...]) -> None:
        self.dispatched.append((outbound, deliveries))

    def post(
        self,
        raw: bytes,
        *,
        signature: str | None = "auto",
        algorithm: str | None = "sha512",
        headers: dict[str, str] | None = None,
    ) -> Any:
        h = dict(headers or {})
        if signature == "auto":
            h["X-Webhook-Hmac"] = compute_waha_hmac(raw, HMAC_KEY)
        elif signature is not None:
            h["X-Webhook-Hmac"] = signature
        if algorithm is not None:
            h["X-Webhook-Hmac-Algorithm"] = algorithm
        return self.client.post(URL, content=raw, headers=h)


@pytest.fixture
def env() -> Env:
    return Env()


def raw(name: str, **changes: Any) -> bytes:
    body = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    for key, value in changes.items():
        body[key] = value
    return json.dumps(body).encode()


# ---------------------------------------------------------------- caminho feliz


def test_texto_chega_ao_pipeline_e_despacha(env: Env) -> None:
    r = env.post(raw("message_text"))
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    [msg] = env.processor.messages
    assert msg.kind is ContentKind.TEXT and msg.sender == Sender(wa_id=SELLER)
    assert env.dispatched == [((7,), ())]


def test_ack_vira_status(env: Env) -> None:
    assert env.post(raw("ack_error")).status_code == 200
    [st] = env.processor.statuses
    assert st.status == "failed" and env.processor.messages == []


def test_estado_da_sessao_vai_ao_monitor(env: Env) -> None:
    assert env.post(raw("session_status_failed")).status_code == 200
    assert env.sessions.states == ["FAILED"]
    assert env.processor.messages == [] and env.processor.statuses == []


@pytest.mark.parametrize(
    "fixture",
    ["message_group", "message_from_me", "message_status_broadcast", "message_newsletter"],
)
def test_filtros_respondem_200_sem_efeito(env: Env, fixture: str) -> None:
    assert env.post(raw(fixture)).status_code == 200
    assert env.processor.messages == [] and env.dispatched == []


# ---------------------------------------------------------------- ordem e protecoes (WA-4)


def test_sem_assinatura_403_sem_processar(env: Env) -> None:
    r = env.post(raw("message_text"), signature=None)
    assert r.status_code == 403 and env.processor.messages == []


def test_assinatura_errada_403(env: Env) -> None:
    body = raw("message_text")
    r = env.post(body, signature=compute_waha_hmac(body, "outra-chave"))
    assert r.status_code == 403 and env.processor.messages == []


@pytest.mark.parametrize("algorithm", [None, "sha256"])
def test_algoritmo_errado_403(env: Env, algorithm: str | None) -> None:
    assert env.post(raw("message_text"), algorithm=algorithm).status_code == 403


def test_corpo_grande_413_antes_da_assinatura(env: Env) -> None:
    big = b"{" + b" " * (env.settings.max_webhook_payload_bytes + 1) + b"}"
    assert env.post(big, signature=None).status_code == 413


def test_json_invalido_400_depois_da_assinatura(env: Env) -> None:
    assert env.post(b"nao e json").status_code == 400
    assert env.post(b"nao e json", signature=None).status_code == 403


def test_limite_por_ip_429() -> None:
    env = Env(rl_max=1)
    assert env.post(raw("message_text")).status_code == 200
    r = env.post(raw("message_text"))
    assert r.status_code == 429 and "Retry-After" in r.headers


# ---------------------------------------------------------------- sessao (WA-5)


def test_sessao_errada_descartada(env: Env) -> None:
    assert env.post(raw("message_text", session="default")).status_code == 200
    assert env.processor.messages == []


# ---------------------------------------------------------------- numero oculto (WA-8)


def test_lid_resolvido_vira_telefone_e_guarda_o_lid() -> None:
    resolver = FakeResolver(SELLER)
    env = Env(resolver=resolver)
    assert env.post(raw("message_lid")).status_code == 200
    [msg] = env.processor.messages
    assert msg.sender == Sender(wa_id=SELLER, bsuid=LID)
    assert resolver.asked == [LID]


def test_lid_nao_resolvido_segue_no_lugar_do_bsuid() -> None:
    env = Env(resolver=FakeResolver(None))
    env.post(raw("message_lid"))
    [msg] = env.processor.messages
    assert msg.sender == Sender(bsuid=LID) and msg.sender.wa_id is None


def test_sem_resolvedor_lid_segue_no_lugar_do_bsuid(env: Env) -> None:
    env.post(raw("message_lid"))
    assert env.processor.messages[0].sender == Sender(bsuid=LID)


def test_telefone_normal_nao_consulta_o_resolvedor() -> None:
    resolver = FakeResolver(SELLER)
    env = Env(resolver=resolver)
    env.post(raw("message_text"))
    assert resolver.asked == []


# ---------------------------------------------------------------- transitorio (WA-9)


def test_falha_transitoria_503_para_o_waha_reentregar(env: Env) -> None:
    env.processor.fail = True
    r = env.post(raw("message_text"))
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
