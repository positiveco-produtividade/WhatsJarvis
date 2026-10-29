"""Borda HTTP do webhook com PayloadProcessor falso, sem banco (AC-1..4, 5, 6, 31)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.webhook import router
from app.config import Settings
from app.core.errors import TransientDbError
from app.core.security import compute_hub_signature
from app.core.values import MessageOutcome
from app.services.counters import SecurityCounters
from app.services.inbound import InboundMessage, InboundStatus
from app.services.rate_limit import WebhookRateLimiter

SECRET = "app-secret-xyz"
VERIFY = "verify-token-xyz"
PHONE_ID = "100000000000001"
URL = "/webhook/whatsapp"
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "meta"


class FakeProcessor:
    def __init__(self) -> None:
        self.messages: list[InboundMessage] = []
        self.statuses: list[InboundStatus] = []
        self.fail_ids: set[str] = set()
        self.outcome = MessageOutcome()

    async def handle_message(self, msg: InboundMessage) -> MessageOutcome:
        self.messages.append(msg)
        if msg.wa_message_id in self.fail_ids:
            raise TransientDbError("pool esgotado")
        return self.outcome

    async def handle_status(self, st: InboundStatus) -> MessageOutcome | None:
        self.statuses.append(st)
        return None


class Env:
    def __init__(self, rl_max: int = 600) -> None:
        self.settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="postgresql://u:p@localhost/db",  # type: ignore[arg-type]
            whatsapp_app_secret=SECRET,  # type: ignore[arg-type]
            whatsapp_verify_token=VERIFY,  # type: ignore[arg-type]
            whatsapp_access_token="tok",  # type: ignore[arg-type]
            whatsapp_phone_number_id=PHONE_ID,
        )
        self.processor = FakeProcessor()
        self.counters = SecurityCounters()
        self.dispatched: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
        app = FastAPI()
        app.include_router(router)
        app.state.settings = self.settings
        app.state.counters = self.counters
        app.state.processor = self.processor
        app.state.webhook_limiter = WebhookRateLimiter(rl_max, 60)
        app.state.outbound_dispatcher = self._dispatch
        self.client = TestClient(app)

    async def _dispatch(self, outbound: tuple[int, ...], deliveries: tuple[int, ...]) -> None:
        self.dispatched.append((outbound, deliveries))

    def post(
        self,
        raw: bytes,
        signature: str | None = "auto",
        headers: dict[str, str] | None = None,
    ) -> object:
        headers = dict(headers or {})
        if signature == "auto":
            headers["X-Hub-Signature-256"] = compute_hub_signature(raw, SECRET)
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post(URL, content=raw, headers=headers)


@pytest.fixture
def env() -> Env:
    return Env()


def load(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


# ---------------------------------------------------------------- GET (AC-1)


def test_get_token_correto_ecoa_challenge(env: Env) -> None:
    r = env.client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": VERIFY, "hub.challenge": "1234"}
    )
    assert r.status_code == 200
    assert r.text == "1234"
    assert r.headers["content-type"].startswith("text/plain")


def test_get_token_errado_ou_ausente_403_sem_eco(env: Env) -> None:
    r = env.client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "ECO"}
    )
    assert r.status_code == 403
    assert "ECO" not in r.text
    r2 = env.client.get(URL, params={"hub.mode": "subscribe", "hub.challenge": "ECO"})
    assert r2.status_code == 403
    assert env.counters.pending() and sum(env.counters.pending().values()) == 2


def test_get_modo_errado_403(env: Env) -> None:
    r = env.client.get(
        URL, params={"hub.mode": "other", "hub.verify_token": VERIFY, "hub.challenge": "1"}
    )
    assert r.status_code == 403


def test_get_token_nao_ascii_nao_quebra(env: Env) -> None:
    r = env.client.get(
        URL, params={"hub.mode": "subscribe", "hub.verify_token": "çãé", "hub.challenge": "1"}
    )
    assert r.status_code == 403


# ------------------------------------------------------- assinatura (AC-2)


def test_sem_assinatura_403_sem_processar(env: Env) -> None:
    r = env.post(load("text"), signature=None)
    assert r.status_code == 403  # type: ignore[attr-defined]
    assert r.content == b""  # type: ignore[attr-defined]
    assert env.processor.messages == []


def test_assinatura_invalida_403(env: Env) -> None:
    r = env.post(load("text"), signature="sha256=" + "0" * 64)
    assert r.status_code == 403  # type: ignore[attr-defined]
    assert env.processor.messages == []
    assert any(k == "invalid_signature" for (_, k) in env.counters.pending())


def test_assinatura_sem_prefixo_403(env: Env) -> None:
    r = env.post(load("text"), signature="abc")
    assert r.status_code == 403  # type: ignore[attr-defined]


def test_header_nao_ascii_403_nao_500(env: Env) -> None:
    raw = load("text")
    r = env.client.post(
        URL, content=raw, headers={"X-Hub-Signature-256": "sha256=ção".encode("latin-1")}
    )
    assert r.status_code == 403
    assert env.processor.messages == []


def test_assinatura_sobre_bytes_brutos(env: Env) -> None:
    raw = load("text")
    sig = compute_hub_signature(raw, SECRET)
    r = env.post(raw + b" ", signature=sig)  # corpo alterado
    assert r.status_code == 403  # type: ignore[attr-defined]


# ------------------------------------------------------------ tamanho (AC-4)


def test_content_length_acima_do_teto_413_antes_da_assinatura(env: Env) -> None:
    raw = b"x" * 70_000
    r = env.post(raw, signature=None)  # sem assinatura: 413, nao 403
    assert r.status_code == 413  # type: ignore[attr-defined]
    assert env.processor.messages == []
    assert any(k == "oversize_payload" for (_, k) in env.counters.pending())


def test_corpo_em_stream_acima_do_teto_413(env: Env) -> None:
    def chunks():  # type: ignore[no-untyped-def]
        for _ in range(8):
            yield b"y" * 10_000

    r = env.client.post(URL, content=chunks())  # sem Content-Length (chunked)
    assert r.status_code == 413


def test_corpo_exatamente_no_teto_passa_a_assinatura(env: Env) -> None:
    r = env.post(b"z" * 65536)
    assert r.status_code == 400  # type: ignore[attr-defined]  # assinado, mas nao e JSON


# ------------------------------------------------------------- JSON (400)


def test_json_invalido_400(env: Env) -> None:
    r = env.post(b"{nao e json")
    assert r.status_code == 400  # type: ignore[attr-defined]
    assert env.processor.messages == []
    assert any(k == "invalid_json" for (_, k) in env.counters.pending())


def test_json_com_utf8_invalido_400(env: Env) -> None:
    assert env.post(b'{"a": "\xff\xfe"}').status_code == 400  # type: ignore[attr-defined]


def test_objeto_diferente_200_sem_efeito(env: Env) -> None:
    r = env.post(json.dumps({"object": "page", "entry": []}).encode())
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert env.processor.messages == [] and env.processor.statuses == []


def test_json_nao_objeto_200(env: Env) -> None:
    assert env.post(b"[1, 2]").status_code == 200  # type: ignore[attr-defined]


# ------------------------------------------------- destinatario (AC-6) e fluxo


def test_mensagem_ok_chama_processador_e_200(env: Env) -> None:
    r = env.post(load("text"))
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert r.json() == {"status": "ok"}  # type: ignore[attr-defined]
    assert len(env.processor.messages) == 1


def test_outro_phone_number_id_descartado(env: Env) -> None:
    r = env.post(load("other_phone_number_id"))
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert env.processor.messages == [] and env.processor.statuses == []
    assert env.counters.pending() and any(
        k == "wrong_recipient" for (_, k) in env.counters.pending()
    )


def test_payload_multiplo_chama_n_vezes(env: Env) -> None:
    r = env.post(load("multiple"))
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert [m.wa_message_id for m in env.processor.messages] == [
        "wamid.MUL00001",
        "wamid.MUL00002",
    ]


def test_statuses_nao_chamam_handle_message(env: Env) -> None:
    for name in ("status_sent", "status_delivered", "status_read", "status_failed"):
        assert env.post(load(name)).status_code == 200  # type: ignore[attr-defined]
    assert env.processor.messages == []
    assert len(env.processor.statuses) == 4


def test_erro_transitorio_503_e_demais_seguem(env: Env) -> None:
    env.processor.fail_ids = {"wamid.MUL00001"}
    r = env.post(load("multiple"))
    assert r.status_code == 503  # type: ignore[attr-defined]
    assert len(env.processor.messages) == 2  # a segunda foi processada mesmo assim


def test_outcome_com_falha_transitoria_vira_503(env: Env) -> None:
    env.processor.outcome = MessageOutcome(transient_failure=True)
    assert env.post(load("text")).status_code == 503  # type: ignore[attr-defined]


def test_erro_nao_transitorio_200(env: Env) -> None:
    async def boom(msg: InboundMessage) -> MessageOutcome:
        raise RuntimeError("bug")

    env.processor.handle_message = boom  # type: ignore[method-assign]
    assert env.post(load("text")).status_code == 200  # type: ignore[attr-defined]


def test_ids_de_saida_vao_para_background_apos_200(env: Env) -> None:
    env.processor.outcome = MessageOutcome(outbound_ids=(7,), delivery_ids=(9,))
    r = env.post(load("multiple"))
    assert r.status_code == 200  # type: ignore[attr-defined]
    assert env.dispatched == [((7, 7), (9, 9))]


def test_sem_ids_nao_agenda_despacho(env: Env) -> None:
    env.post(load("text"))
    assert env.dispatched == []


# ----------------------------------------------------------- limite (passo 0)


def test_limite_por_ip_429_antes_de_ler_o_corpo() -> None:
    env = Env(rl_max=2)
    for _ in range(2):
        assert env.post(load("text")).status_code == 200  # type: ignore[attr-defined]
    r = env.post(b"x" * 70_000, signature=None)
    assert r.status_code == 429  # type: ignore[attr-defined]
    assert int(r.headers["Retry-After"]) >= 1  # type: ignore[attr-defined]
    assert len(env.processor.messages) == 2


def test_limite_usa_x_real_ip_nao_x_forwarded_for() -> None:
    env = Env(rl_max=1)
    ok = env.post(load("text"), headers={"X-Real-IP": "1.1.1.1"})
    assert ok.status_code == 200  # type: ignore[attr-defined]
    outro = env.post(load("text"), headers={"X-Real-IP": "2.2.2.2"})
    assert outro.status_code == 200  # type: ignore[attr-defined]
    forjado = env.post(
        load("text"), headers={"X-Real-IP": "1.1.1.1", "X-Forwarded-For": "9.9.9.9"}
    )
    assert forjado.status_code == 429  # type: ignore[attr-defined]


# --------------------------------------------------------------- logs (4.6)


def test_logs_so_com_eventos_da_spec_e_sem_pii(
    env: Env, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    env.post(load("text"))
    env.post(load("text"), signature="sha256=" + "0" * 64)
    env.post(b"x" * 70_000, signature=None)
    events = {r.getMessage() for r in caplog.records if r.name == "app.api.webhook"}
    assert events <= {
        "webhook_received",
        "webhook_signature_invalid",
        "webhook_payload_too_large",
        "webhook_wrong_recipient",
    }
    assert {"webhook_received", "webhook_signature_invalid", "webhook_payload_too_large"} <= events
    dump = " ".join(str(r.__dict__) for r in caplog.records)
    assert "551199990001" not in dump
