"""Rastro por mensagem e logs sem PII (PF-40; AC-31, AC-32 e a parte de log do AC-37).

App real (`create_app` com `FakeChannel` e `httpx.MockTransport`), Postgres de teste real,
webhook assinado. O fluxo completo cobre desconhecido, texto de ativo, midia, conflito de
identidade, limite de mensagens e falha definitiva de envio, tudo capturado em DEBUG.
Os logs sao conferidos CRUS (antes do filtro de redacao): o filtro e defesa em profundidade,
o contrato do spec 4.6 e que o codigo nao coloque PII no log.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import main
from app.config import Settings
from app.core.security import compute_hub_signature
from app.services.phone import normalize_phone
from tests.fakes import FakeChannel

pytestmark = pytest.mark.db

BASE = "http://localhost"
URL = "/webhook/whatsapp"
SECRET = "app-secret-trace"
VERIFY = "verify-token-trace"
PHONE_ID = "100000000000001"
ACCESS_TOKEN = "access-token-trace"
RESEND_KEY = "re_fake_key_trace"
WAHA_KEY = "waha-key-trace-1"
RECIPIENT = "equipe-secreta@example.test"
NOW = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
PROFILE_NAME = "Zeferino Quixote Perfil"

PHONE_ACTIVE = "551199990001"
PHONE_UNKNOWN = "551199990002"
PHONE_CONFLICT = "551199990003"
PHONE_FAIL = "551199990004"
ALL_PHONES = (PHONE_ACTIVE, PHONE_UNKNOWN, PHONE_CONFLICT, PHONE_FAIL)

NAME_ACTIVE = "Anastacia Souza Testadora"
NAME_CONFLICT = "Clotilde Conflitante Silva"
NAME_FAIL = "Dorotea Falhadora Lima"

BSUID_STORED = "BR.13491208655302741111"
BSUID_OTHER = "BR.13491208655302742222"

TEXT_ACTIVE = "SEGREDO-ATIVO Jungle tem glúten e lactose?"
TEXT_UNKNOWN = "SEGREDO-DESCONHECIDO quero falar com alguém"
TEXT_CONFLICT = "SEGREDO-CONFLITO posso comprar?"
TEXT_LIMIT = "SEGREDO-LIMITE terceira mensagem"
TEXT_FAIL = "SEGREDO-FALHA preciso de ajuda"
CAPTION_IMAGE = "SEGREDO-LEGENDA foto do produto"
ALL_TEXTS = (TEXT_ACTIVE, TEXT_UNKNOWN, TEXT_CONFLICT, TEXT_LIMIT, TEXT_FAIL, CAPTION_IMAGE)

_GENERIC_PHONE = re.compile(r"(?<![\w.])\+?\d[\d ()\-]{8,}\d(?!\w)")
_GENERIC_EMAIL = re.compile(r"[\w.%+\-]+@[\w.\-]+\.[A-Za-z]{2,}")


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": os.environ["TEST_DATABASE_URL"],
        "whatsapp_app_secret": SECRET,
        "whatsapp_verify_token": VERIFY,
        "whatsapp_access_token": ACCESS_TOKEN,
        "whatsapp_phone_number_id": PHONE_ID,
        "resend_api_key": RESEND_KEY,
        "resend_from_email": "jarvis@example.test",
        "alert_recipient_email": RECIPIENT,
        "log_level": "DEBUG",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


def payload(
    wamid: str,
    *,
    wa_id: str | None = None,
    bsuid: str | None = None,
    kind: str = "text",
    text: str | None = None,
) -> bytes:
    """Payload da Meta (formato da Cloud API) com um unico item em `messages`."""
    msg: dict[str, Any] = {"id": wamid, "timestamp": "1761200000", "type": kind}
    contact: dict[str, Any] = {"profile": {"name": PROFILE_NAME}}
    if wa_id is not None:
        msg["from"] = wa_id
        contact["wa_id"] = wa_id
    if bsuid is not None:
        msg["from_user_id"] = bsuid
        contact["user_id"] = bsuid
    if kind == "text":
        msg["text"] = {"body": text}
    elif kind == "image":
        msg["image"] = {"id": "300000000000002", "mime_type": "image/jpeg", "caption": text}
    body = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "200000000000001",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000001",
                                "phone_number_id": PHONE_ID,
                            },
                            "contacts": [contact],
                            "messages": [msg],
                        },
                    }
                ],
            }
        ],
    }
    return json.dumps(body).encode()


@dataclass
class Env:
    app: FastAPI
    client: TestClient
    channel: FakeChannel
    http_calls: list[httpx.Request]
    settings: Settings

    def post(self, raw: bytes, signature: str | None = "auto") -> httpx.Response:
        headers: dict[str, str] = {}
        if signature == "auto":
            headers["X-Hub-Signature-256"] = compute_hub_signature(raw, SECRET)
        elif signature is not None:
            headers["X-Hub-Signature-256"] = signature
        return self.client.post(URL, content=raw, headers=headers)


def start_env(
    handler: Callable[[httpx.Request], httpx.Response] | None = None, **over: Any
) -> Env:
    channel = FakeChannel()
    calls: list[httpx.Request] = []

    def default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "email-fake-1"})

    chosen = handler or default_handler

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return chosen(request)

    cfg = make_settings(**over)
    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    app = main.create_app(cfg, channel=channel, http_client=http)
    client = TestClient(app, base_url=BASE)
    client.__enter__()
    return Env(app, client, channel, calls, cfg)


async def add_user(
    pool: asyncpg.Pool,
    *,
    name: str,
    phone: str,
    bsuid: str | None = None,
    status: str = "active",
) -> int:
    num = normalize_phone("+" + phone)
    return int(
        await pool.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, bsuid, profile, company, "
            "ufs, channels, brands, manager_name, status, status_changed_at, created_at, "
            "created_by, updated_at, accepted_at, accepted_notice_version) VALUES ($5, "
            "$1, $2, $6, 'vendedor', 'Acme', ARRAY['SP'], ARRAY['farma'], ARRAY['jungle'], "
            "'Gestora', $3, $4, $4, 'teste', $4, "
            "CASE WHEN $3 = 'active' THEN $4::timestamptz END, "
            "CASE WHEN $3 = 'active' THEN 'v1' END) RETURNING id",
            num.phone_e164,
            num.match_key,
            status,
            NOW,
            name,
            bsuid,
        )
    )


async def wait_for(check: Callable[[], Any], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await check():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condicao nao atingida no prazo")


async def settle(pool: asyncpg.Pool) -> None:
    """Espera as saidas de resposta chegarem a um estado final (nenhuma em curso)."""

    async def idle() -> bool:
        busy = await pool.fetchval(
            "SELECT count(*) FROM chat.outbound_messages WHERE status IN ('pending','sending')"
        )
        pending_reply = await pool.fetchval(
            "SELECT count(*) FROM chat.message_trace WHERE reply_kind = 'pending'"
        )
        return busy == 0 and pending_reply == 0

    await wait_for(idle)


# ------------------------------------------------------------------ verificacao de vazamento


def phone_variants(digits: str) -> list[str]:
    """Formas em que um telefone pode vazar (a mascara `***0001` e permitida)."""
    ddd, local = digits[2:4], digits[4:]
    return [
        digits,
        "+" + digits,
        digits[2:],
        f"+{digits[:2]} {ddd} {local}",
        f"({ddd}) {local[:4]}-{local[4:]}",
        f"{ddd} {local[:4]}-{local[4:]}",
        local,
        f"{local[:4]}-{local[4:]}",
        f"{local[:4]} {local[4:]}",
    ]


def record_dump(records: list[logging.LogRecord]) -> str:
    """Tudo que cada registro carrega: mensagem, extras e traceback."""
    standard = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
        "message",
        "asctime",
        "taskName",
    }
    formatter = logging.Formatter()
    lines: list[str] = []
    for rec in records:
        parts = [rec.name, rec.getMessage()]
        parts += [f"{k}={v!r}" for k, v in rec.__dict__.items() if k not in standard]
        if rec.exc_info:
            parts.append(formatter.formatException(rec.exc_info))
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def secrets_to_hide() -> list[str]:
    values = [SECRET, VERIFY, ACCESS_TOKEN, RESEND_KEY, WAHA_KEY]
    dsn = os.environ["TEST_DATABASE_URL"]
    values.append(dsn)
    password = urlparse(dsn).password
    if password:
        values.append(password)
    return values


def find_leaks(dump: str) -> list[str]:
    """Nomes (nunca valores de segredo) das categorias que vazaram."""
    found: list[str] = []
    for digits in ALL_PHONES:
        if any(v in dump for v in phone_variants(digits)):
            found.append(f"telefone final {digits[-4:]}")
    for text in ALL_TEXTS:
        if text in dump or text.split()[0] in dump:
            found.append(f"texto {text.split()[0]}")
    for name in (PROFILE_NAME, NAME_ACTIVE, NAME_CONFLICT, NAME_FAIL):
        if name in dump or name.split()[0] in dump:
            found.append(f"nome {name.split()[0]}")
    for bsuid in (BSUID_STORED, BSUID_OTHER):
        if bsuid in dump or bsuid.split(".")[1] in dump:
            found.append("bsuid")
    if RECIPIENT in dump or RECIPIENT.split("@")[0] in dump:
        found.append("email do destinatario")
    if _GENERIC_EMAIL.search(dump):
        found.append("padrao de e-mail")
    if _GENERIC_PHONE.search(dump):
        found.append("sequencia longa de digitos")
    for index, secret in enumerate(secrets_to_hide()):
        leaked = secret in dump
        if leaked:
            found.append(f"segredo #{index}")
    return found


# ------------------------------------------------------------------ fixtures


@pytest.fixture
def env(db_pool: asyncpg.Pool) -> Any:  # db_pool limpa as tabelas antes
    e = start_env(rate_limit_per_hour=2, send_failure_alert_threshold=1)
    try:
        yield e
    finally:
        e.client.__exit__(None, None, None)


async def run_full_flow(env: Env, pool: asyncpg.Pool) -> list[str]:
    """Desconhecido, texto, midia, conflito, limite, reentrega e falha de envio."""
    await add_user(pool, name=NAME_ACTIVE, phone=PHONE_ACTIVE)
    await add_user(pool, name=NAME_CONFLICT, phone=PHONE_CONFLICT, bsuid=BSUID_STORED)
    await add_user(pool, name=NAME_FAIL, phone=PHONE_FAIL)
    ids: list[str] = []

    def send(wamid: str, **kw: Any) -> None:
        ids.append(wamid)
        assert env.post(payload(wamid, **kw)).status_code == 200

    send("wamid.FLOW.UNKNOWN", wa_id=PHONE_UNKNOWN, text=TEXT_UNKNOWN)
    send("wamid.FLOW.TEXT", wa_id=PHONE_ACTIVE, text=TEXT_ACTIVE)
    await settle(pool)
    send("wamid.FLOW.IMAGE", wa_id=PHONE_ACTIVE, kind="image", text=CAPTION_IMAGE)
    await settle(pool)
    send("wamid.FLOW.LIMIT", wa_id=PHONE_ACTIVE, text=TEXT_LIMIT)
    await settle(pool)
    send("wamid.FLOW.CONFLICT", wa_id=PHONE_CONFLICT, bsuid=BSUID_OTHER, text=TEXT_CONFLICT)
    # reentrega do mesmo payload: log de duplicata, nenhum rastro novo
    again = payload("wamid.FLOW.TEXT", wa_id=PHONE_ACTIVE, text=TEXT_ACTIVE)
    assert env.post(again).status_code == 200
    env.channel.fail_always("definitive")
    send("wamid.FLOW.FAIL", wa_id=PHONE_FAIL, text=TEXT_FAIL)
    await settle(pool)
    # bordas de HTTP: assinatura invalida e outro numero de destino
    assert env.post(payload("wamid.FLOW.BADSIG", wa_id=PHONE_ACTIVE, text=TEXT_ACTIVE),
                    signature="sha256=" + "0" * 64).status_code == 403
    other = json.loads(payload("wamid.FLOW.OTHER", wa_id=PHONE_ACTIVE, text=TEXT_ACTIVE))
    other["entry"][0]["changes"][0]["value"]["metadata"]["phone_number_id"] = "999999999999999"
    assert env.post(json.dumps(other).encode()).status_code == 200
    return ids


# ------------------------------------------------------------------ AC-32: exatamente 1 rastro


async def test_exatamente_um_rastro_por_mensagem_nao_duplicada(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    ids = await run_full_flow(env, db_pool)
    rows = await db_pool.fetch("SELECT * FROM chat.message_trace ORDER BY id")
    assert sorted(r["wa_message_id"] for r in rows) == sorted(ids)  # 1 por id, reentrega nao somou
    for row in rows:
        assert row["msg_type"] in ("text", "image")
        assert row["access_decision"] != "processing" and row["decision"] != "processing"
        if row["send_result"] == "sent":  # latencia = recebimento ate o envio da resposta
            assert row["latency_ms"] is not None and row["latency_ms"] >= 0
            assert row["reply_sent_at"] is not None
        assert row["reply_kind"] is not None  # resposta ou 'none'
        assert row["user_id"] is not None or row["attempt_id"] is not None


async def test_rastro_traz_decisao_resposta_e_resultado_do_envio(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await run_full_flow(env, db_pool)
    rows = {
        r["wa_message_id"]: r
        for r in await db_pool.fetch("SELECT * FROM chat.message_trace")
    }
    text = rows["wamid.FLOW.TEXT"]
    assert text["decision"] == "allowed" and text["reply_kind"] == "under_construction"
    assert text["send_result"] == "sent" and text["counted_in_limit"] is True
    image = rows["wamid.FLOW.IMAGE"]
    assert image["decision"] == "escalated_media" and image["reply_kind"] == "media_ack"
    assert image["ticket_number"] is not None
    limit = rows["wamid.FLOW.LIMIT"]
    assert limit["decision"] == "rate_limited" and limit["reply_kind"] == "rate_limit_notice"
    assert limit["counted_in_limit"] is False
    unknown = rows["wamid.FLOW.UNKNOWN"]
    assert unknown["decision"] == "unknown" and unknown["reply_kind"] == "none"
    assert unknown["attempt_id"] is not None and unknown["user_id"] is None
    conflict = rows["wamid.FLOW.CONFLICT"]
    assert conflict["decision"] == "identity_conflict" and conflict["reply_kind"] == "none"
    failed = rows["wamid.FLOW.FAIL"]
    assert failed["send_result"] == "failed" and failed["send_error"]


async def test_corpo_so_e_guardado_para_usuario_autorizado(
    env: Env, db_pool: asyncpg.Pool
) -> None:
    await run_full_flow(env, db_pool)
    rows = {
        r["wa_message_id"]: r["body"]
        for r in await db_pool.fetch("SELECT wa_message_id, body FROM chat.message_trace")
    }
    assert rows["wamid.FLOW.TEXT"] == TEXT_ACTIVE  # autorizado
    for wamid in ("wamid.FLOW.UNKNOWN", "wamid.FLOW.CONFLICT", "wamid.FLOW.LIMIT"):
        assert rows[wamid] is None, wamid
    assert await db_pool.fetchval("SELECT count(*) FROM access.unknown_first_messages") == 0


# ------------------------------------------------------------------ AC-32: logs sem PII


async def test_logs_de_um_fluxo_completo_nao_vazam_pii_nem_segredo(
    env: Env, db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await run_full_flow(env, db_pool)
    # um e-mail de alerta tambem sai neste fluxo (conflito e limite): ainda sem vazar
    assert caplog.records, "nenhum log capturado"
    events = {r.getMessage() for r in caplog.records}
    assert {"webhook_received", "message_decision"} <= events
    leaks = find_leaks(record_dump(caplog.records) + "\n" + caplog.text)
    assert leaks == [], f"vazamentos nos logs: {sorted(set(leaks))}"


async def test_logs_formatados_em_json_tambem_sem_pii(
    env: Env, db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    from app.core.logging_json import JsonFormatter

    caplog.set_level(logging.DEBUG)
    await run_full_flow(env, db_pool)
    formatter = JsonFormatter()
    lines = [formatter.format(r) for r in caplog.records]
    assert lines and all(json.loads(line)["event"] for line in lines)
    leaks = find_leaks("\n".join(lines))
    assert leaks == [], f"vazamentos nas linhas JSON: {sorted(set(leaks))}"


async def test_log_de_decisao_so_traz_mascara_do_telefone_e_ids(
    env: Env, db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await run_full_flow(env, db_pool)
    decisions = [r for r in caplog.records if r.getMessage() == "message_decision"]
    assert len(decisions) >= 6  # uma por mensagem processada
    for rec in decisions:
        extras = rec.__dict__
        assert "decision" in extras and "msg_type" in extras
        assert "body" not in extras and "text" not in extras and "phone" not in extras


async def test_falha_de_envio_e_logada_sem_pii(
    env: Env, db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await add_user(db_pool, name=NAME_FAIL, phone=PHONE_FAIL)
    env.channel.fail_always("definitive", code="131047")
    assert env.post(payload("wamid.ONLYFAIL", wa_id=PHONE_FAIL, text=TEXT_FAIL)).status_code == 200
    await settle(db_pool)

    # O app grava o estado final no banco e so depois emite o log (no laco do app); o `settle`
    # enxerga o banco, nao o log. Espera o registro aparecer em vez de supor a ordem.
    async def logged() -> bool:
        return any(r.getMessage().startswith("outbound_") for r in list(caplog.records))

    await wait_for(logged)
    assert any(r.getMessage().startswith("outbound_") for r in caplog.records)
    assert find_leaks(record_dump(caplog.records)) == []
    assert await db_pool.fetchval("SELECT send_result FROM chat.message_trace") == "failed"


# ------------------------------------------------------------------ o detector enxerga vazamentos


@pytest.mark.parametrize(
    "dump",
    [
        f"x {PHONE_ACTIVE} y",
        f"x +{PHONE_ACTIVE} y",
        "x (11) 9999-0001 y",
        "x 11999990001 y",
        f"texto {TEXT_ACTIVE}",
        f"nome {NAME_ACTIVE}",
        f"bsuid {BSUID_OTHER}",
        f"para {RECIPIENT}",
        f"Authorization: Bearer {ACCESS_TOKEN}",
        f"segredo {SECRET}",
    ],
)
def test_detector_de_vazamento_pega_cada_forma(dump: str) -> None:
    assert find_leaks(dump) != []


def test_mascara_do_telefone_nao_e_vazamento() -> None:
    assert find_leaks("user ***0001 decision=allowed") == []
