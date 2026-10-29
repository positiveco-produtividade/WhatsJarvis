"""deliveries.dispatch (AC-13, AC-37, AC-36, AC-12): claim, e-mail com backoff, grupo por chave."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services.alerts import raise_alert
from app.services.deliveries import build_email, dispatch
from app.services.flags import Flags
from app.services.tickets import create_media_ticket
from tests.integration.test_schema_access_chat import add_trace, add_user

pytestmark = pytest.mark.db

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
PHONE = "+5511987654321"
NAME = "Ana"
RECIPIENT = "destino@example.com"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "resend_api_key": SecretStr("re_key"),
        "resend_from_email": "jarvis@example.com",
        "alert_recipient_email": SecretStr(RECIPIENT),
        "waha_api_key": SecretStr("waha_key"),
        "team_group_chat_id": "120363000000@g.us",
        "panel_base_url": "https://painel.example.com/",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


class Recorder:
    """MockTransport que registra requisicoes e responde na ordem de `statuses`."""

    def __init__(self, *statuses: int) -> None:
        self.requests: list[httpx.Request] = []
        self._statuses = list(statuses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        code = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        return httpx.Response(code, json={"id": "x"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def _group_flag(pool: asyncpg.Pool, flags: Flags, value: bool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await flags.set_bool(conn, "team_group_enabled", value, "caio")


async def _alert(pool: asyncpg.Pool, flags: Flags, cfg: Settings) -> int:
    async with pool.acquire() as conn:
        user_id = await add_user(
            conn, status="active", accepted_at=NOW, accepted_notice_version="v1"
        )
        alert_id = await raise_alert(
            conn, "rate_limit", "warn", "Limite por hora atingido", {"limit": 60},
            user_id=user_id, flags=flags, settings=cfg, clock=FakeClock(NOW),
        )  # fmt: skip
    assert alert_id is not None
    return alert_id


async def _delivery(
    pool: asyncpg.Pool, channel: str, source_type: str = "alert"
) -> asyncpg.Record:
    row = await pool.fetchrow(
        "SELECT * FROM ops.deliveries WHERE channel = $1 AND source_type = $2", channel, source_type
    )
    assert row is not None
    return row


async def _run(
    pool: asyncpg.Pool,
    row: asyncpg.Record,
    flags: Flags,
    cfg: Settings,
    clock: FakeClock,
    **clients: httpx.AsyncClient,
) -> str | None:
    return await dispatch(pool, row["id"], flags=flags, settings=cfg, clock=clock, **clients)


async def test_email_enviado_e_sem_pii(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)
    mail = Recorder(200)
    async with mail.client() as client:
        status = await _run(
            db_pool, await _delivery(db_pool, "email"), flags, cfg, clock, email_client=client
        )
    assert status == "sent"
    row = await _delivery(db_pool, "email")
    assert row["status"] == "sent" and row["sent_at"] == NOW and row["attempts"] == 1
    payload = json.loads(mail.requests[0].content)
    assert payload["subject"] == "[ALTA] Jarvis: rate_limit"
    body = payload["text"]
    assert "Severidade: warn" in body and "Usuário #1" in body
    assert "23/10/2026 12:00 (America/Sao_Paulo)" in body
    assert "https://painel.example.com/dashboard/alerts" in body
    for secret in (PHONE, NAME, "5511987654321"):
        assert secret not in body


async def test_email_falha_transitoria_faz_backoff_e_desiste(db_pool: asyncpg.Pool) -> None:
    cfg = make_settings(email_max_attempts=3, email_backoff_seconds="60,300")
    flags, clock = Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)
    mail = Recorder(503)
    results: list[str | None] = []
    async with mail.client() as client:
        for wait in (60, 300):
            row = await _delivery(db_pool, "email")
            results.append(await _run(db_pool, row, flags, cfg, clock, email_client=client))
            row = await _delivery(db_pool, "email")
            assert row["status"] == "pending"
            assert row["next_attempt_at"] == clock.now() + timedelta(seconds=wait)
            assert row["last_error"] == "http_503"
        for _ in range(2):
            row = await _delivery(db_pool, "email")
            results.append(await _run(db_pool, row, flags, cfg, clock, email_client=client))
    assert results == ["pending", "pending", "failed", None]
    assert (await _delivery(db_pool, "email"))["attempts"] == 3
    assert len(mail.requests) == 3  # failed nao gera novo e-mail


async def test_email_permanente_falha_na_hora(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)
    mail = Recorder(401)
    async with mail.client() as client:
        status = await _run(
            db_pool, await _delivery(db_pool, "email"), flags, cfg, clock, email_client=client
        )
    assert status == "failed"
    assert (await _delivery(db_pool, "email"))["last_error"] == "http_401"


async def test_email_sem_configuracao_vira_skipped(db_pool: asyncpg.Pool) -> None:
    flags, clock = Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, make_settings())
    bare = make_settings(resend_api_key=SecretStr(""))
    status = await _run(db_pool, await _delivery(db_pool, "email"), flags, bare, clock)
    assert status == "skipped"
    assert (await _delivery(db_pool, "email"))["status"] == "skipped"


async def test_claim_atomico_nao_despacha_duas_vezes(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)
    row = await _delivery(db_pool, "email")
    mail = Recorder(200)
    async with mail.client() as client:
        first = await _run(db_pool, row, flags, cfg, clock, email_client=client)
        second = await _run(db_pool, row, flags, cfg, clock, email_client=client)
    assert (first, second) == ("sent", None)
    assert len(mail.requests) == 1


async def test_email_independe_da_chave_do_grupo(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _group_flag(db_pool, flags, False)
    await _alert(db_pool, flags, cfg)
    mail, waha = Recorder(200), Recorder(200)
    async with mail.client() as mail_client, waha.client() as waha_client:
        status = await _run(
            db_pool, await _delivery(db_pool, "email"), flags, cfg, clock,
            email_client=mail_client, waha_client=waha_client,
        )  # fmt: skip
    assert status == "sent" and len(mail.requests) == 1 and not waha.requests


async def test_grupo_desligado_na_hora_do_envio_vira_panel_only(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _group_flag(db_pool, flags, True)
    await _alert(db_pool, flags, cfg)
    assert (await _delivery(db_pool, "group"))["status"] == "pending"
    await _group_flag(db_pool, flags, False)
    flags = Flags(db_pool, FakeClock(NOW))  # cache novo
    waha = Recorder(200)
    async with waha.client() as client:
        status = await _run(
            db_pool, await _delivery(db_pool, "group"), flags, cfg, clock, waha_client=client
        )
    assert status == "panel_only" and not waha.requests
    assert (await _delivery(db_pool, "group"))["status"] == "panel_only"


async def test_grupo_ligado_envia_texto_curto_sem_pii(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _group_flag(db_pool, flags, True)
    await _alert(db_pool, flags, cfg)
    waha = Recorder(201)
    async with waha.client() as client:
        status = await _run(
            db_pool, await _delivery(db_pool, "group"), flags, cfg, clock, waha_client=client
        )
    assert status == "sent"
    text = json.loads(waha.requests[0].content)["text"]
    assert "usuário #1" in text and "rate_limit" in text
    assert PHONE not in text and NAME not in text


async def test_waha_com_falha_mantem_registro_e_retenta(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _group_flag(db_pool, flags, True)
    await _alert(db_pool, flags, cfg)
    waha = Recorder(500, 200)
    async with waha.client() as client:
        row = await _delivery(db_pool, "group")
        first = await _run(db_pool, row, flags, cfg, clock, waha_client=client)
        row = await _delivery(db_pool, "group")
        assert first == "pending" and row["next_attempt_at"] == NOW + timedelta(seconds=60)
        second = await _run(db_pool, row, flags, cfg, clock, waha_client=client)
    assert second == "sent"


async def test_ticket_vai_so_ao_grupo_nunca_email(db_pool: asyncpg.Pool) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _group_flag(db_pool, flags, True)
    async with db_pool.acquire() as conn:
        await conn.execute("SELECT setval('ops.ticket_seq', 11, false)")
        user_id = await add_user(
            conn, status="active", accepted_at=NOW, accepted_notice_version="v1"
        )
        trace_id = await add_trace(conn, user_id=user_id, msg_type="audio")
        ticket = await create_media_ticket(
            conn, user_id, trace_id, "audio", flags=flags, clock=FakeClock(NOW)
        )
    rows = await db_pool.fetch("SELECT channel FROM ops.deliveries")
    assert [r["channel"] for r in rows] == ["group"]
    waha, mail = Recorder(200), Recorder(200)
    async with waha.client() as w, mail.client() as m:
        status = await _run(
            db_pool, await _delivery(db_pool, "group", "ticket"), flags, cfg, clock,
            waha_client=w, email_client=m,
        )  # fmt: skip
    assert status == "sent" and not mail.requests
    text = json.loads(waha.requests[0].content)["text"]
    assert text == f"Mídia de usuário #{user_id} (áudio) aguardando a equipe — ticket J-0011"
    assert ticket.ticket_number == "J-0011"


async def test_sem_entrega_para_tentativa_de_acesso(db_pool: asyncpg.Pool) -> None:
    # AC-12: tentativas de acesso nao passam por raise_alert; nada a despachar.
    result = await dispatch(db_pool, 999, flags=Flags(db_pool), settings=make_settings())
    assert result is None
    assert await db_pool.fetchval("SELECT count(*) FROM ops.deliveries") == 0


async def test_erro_inesperado_nao_propaga_e_devolve_a_fila(
    db_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)

    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("falha qualquer")

    monkeypatch.setattr("app.services.deliveries.send_email", boom)
    status = await _run(db_pool, await _delivery(db_pool, "email"), flags, cfg, clock)
    row = await _delivery(db_pool, "email")
    assert status == "pending" and row["last_error"] == "unexpected_error"


async def test_logs_sem_pii_nem_endereco(
    db_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    cfg, flags, clock = make_settings(), Flags(db_pool, FakeClock(NOW)), FakeClock(NOW)
    await _alert(db_pool, flags, cfg)
    mail = Recorder(503)
    caplog.set_level(logging.DEBUG)
    async with mail.client() as client:
        await _run(
            db_pool, await _delivery(db_pool, "email"), flags, cfg, clock, email_client=client
        )
    logged = " ".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (RECIPIENT, PHONE, NAME, "re_key"):
        assert secret not in logged


def test_build_email_sem_usuario_nem_painel() -> None:
    mail = build_email(
        severity="high", kind="send_failure", message="Falhas de envio", count=2, at=NOW,
        user_id=None,
    )  # fmt: skip
    assert mail.subject == "[ALTA] Jarvis: send_failure"
    assert "Usuário" not in mail.body and "Painel" not in mail.body
    assert "12:00 (America/Sao_Paulo)" in mail.body
