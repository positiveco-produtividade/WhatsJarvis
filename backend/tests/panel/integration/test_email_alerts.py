"""E-mail de alerta novo (PN-65) e falha do Resend sem bloqueio (PN-66), em Postgres real.

Usa `deliveries.dispatch` de PF com `ResendSpy` (MockTransport): sem rede. Alertas de
sistema nascem por `raise_alert` (o mesmo caminho do monitor do painel).
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.services.alerts import raise_alert
from app.services.deliveries import dispatch
from app.services.flags import Flags
from tests.integration.test_schema_access_chat import add_user
from tests.panel.fakes import ResendSpy, WahaSpy

pytestmark = pytest.mark.db

PHONE = "+5511987654321"
NAME = "Ana"
BASE_URL = "https://painel.example.com"


def make_settings(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "resend_api_key": SecretStr("re_key"),
        "resend_from_email": "jarvis@example.com",
        "alert_recipient_email": SecretStr("destino@example.com"),
        "waha_api_key": SecretStr("waha_key"),
        "team_group_chat_id": "120363000000@g.us",
        "panel_base_url": BASE_URL,
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _new_alert(
    pool: asyncpg.Pool,
    cfg: Settings,
    clock: FakeClock,
    *,
    kind: str = "error_rate",
    dedup_key: str | None = None,
    with_user: bool = False,
) -> int:
    flags = Flags(pool, clock)
    async with pool.acquire() as conn:
        user_id = (
            await add_user(
                conn, status="active", accepted_at=clock.now(), accepted_notice_version="v1"
            )
            if with_user
            else None
        )
        alert_id = await raise_alert(
            conn, kind, "high", "Taxa de erros no atendimento acima do limite.",
            {"error_count": 9, "total_count": 20}, user_id=user_id, dedup_key=dedup_key,
            dedup_window=timedelta(hours=1) if dedup_key else None,
            flags=flags, settings=cfg, clock=clock,
        )  # fmt: skip
    assert alert_id is not None
    return alert_id


async def _email_rows(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    return list(
        await pool.fetch("SELECT * FROM ops.deliveries WHERE channel = 'email' ORDER BY id")
    )


async def _send(
    pool: asyncpg.Pool, cfg: Settings, clock: FakeClock, client: httpx.AsyncClient
) -> str | None:
    row = (await _email_rows(pool))[0]
    return await dispatch(
        pool, row["id"], flags=Flags(pool, clock), settings=cfg, clock=clock, email_client=client
    )


async def test_alerta_novo_gera_exatamente_um_email_pendente(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    await _new_alert(db_pool, make_settings(), clock)
    rows = await _email_rows(db_pool)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["source_type"] == "alert"


async def test_email_enviado_uma_vez_com_conteudo_minimo_e_sem_pii(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy
) -> None:
    cfg = make_settings()
    await _new_alert(db_pool, cfg, clock, with_user=True)
    async with resend_spy.client() as client:
        assert await _send(db_pool, cfg, clock, client) == "sent"
    assert resend_spy.calls == 1
    mail = resend_spy.emails()[0]
    assert mail["subject"] == "[ALTA] Jarvis: error_rate"
    body = str(mail["text"])
    assert "Severidade: high" in body and "Tipo: error_rate" in body
    assert "Taxa de erros no atendimento acima do limite." in body
    for secret in (PHONE, "5511987654321", "987654321", NAME):
        assert secret not in json.dumps(mail)
    assert (await _email_rows(db_pool))[0]["status"] == "sent"


async def test_email_traz_link_do_painel_com_abrir_e_id_do_alerta(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy
) -> None:
    """PN-65: link `.../alerts?abrir=<id>`.

    BUG conhecido (relatado na tarefa PN-T44): `deliveries.build_email` (PF) escreve so
    `.../dashboard/alerts`, sem `?abrir=<id>`; o painel ja entende o parametro.
    """
    cfg = make_settings()
    alert_id = await _new_alert(db_pool, cfg, clock)
    async with resend_spy.client() as client:
        await _send(db_pool, cfg, clock, client)
    body = str(resend_spy.emails()[0]["text"])
    assert f"{BASE_URL}/dashboard/alerts?abrir={alert_id}" in body


async def test_email_independe_da_chave_do_grupo(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _new_alert(db_pool, cfg, clock)  # chave do grupo desligada (padrao)
    group = await db_pool.fetchrow("SELECT status FROM ops.deliveries WHERE channel = 'group'")
    assert group is not None and group["status"] == "panel_only"
    async with resend_spy.client() as client:
        assert await _send(db_pool, cfg, clock, client) == "sent"
    assert resend_spy.calls == 1 and waha_spy.calls == 0


async def test_alerta_repetido_na_janela_nao_gera_segundo_email(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    cfg = make_settings()
    await _new_alert(db_pool, cfg, clock, dedup_key="monitor:error_rate")
    flags = Flags(db_pool, clock)
    async with db_pool.acquire() as conn:
        again = await raise_alert(
            conn, "error_rate", "high", "Taxa de erros no atendimento acima do limite.",
            {"error_count": 10}, dedup_key="monitor:error_rate",
            dedup_window=timedelta(hours=1), flags=flags, settings=cfg, clock=clock,
        )  # fmt: skip
    assert again is None
    assert len(await _email_rows(db_pool)) == 1
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts") == 1


async def test_sem_destinatario_configurado_vira_skipped_sem_chamar_o_resend(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy
) -> None:
    cfg = make_settings(alert_recipient_email=SecretStr(""))
    await _new_alert(db_pool, cfg, clock)
    rows = await _email_rows(db_pool)
    assert rows[0]["status"] == "skipped" and rows[0]["last_error"] == "not_configured"
    assert resend_spy.calls == 0


# ----------------------------------------------------------------- PN-66: falha nao bloqueia


async def test_falha_do_resend_nao_levanta_retenta_e_registra(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy
) -> None:
    cfg = make_settings(email_max_attempts=3, email_backoff_seconds="60,300")
    alert_id = await _new_alert(db_pool, cfg, clock)
    resend_spy.fail_with = 500
    async with resend_spy.client() as client:
        assert await _send(db_pool, cfg, clock, client) == "pending"  # nao levantou
    row = (await _email_rows(db_pool))[0]
    assert row["status"] == "pending" and row["attempts"] == 1
    assert row["last_error"] == "http_500"
    assert row["next_attempt_at"] == clock.now() + timedelta(seconds=60)
    # o alerta continua gravado e visivel no painel
    assert await db_pool.fetchval("SELECT count(*) FROM ops.alerts WHERE id = $1", alert_id) == 1

    clock.advance(seconds=61)
    resend_spy.fail_with = None  # Resend volta
    async with resend_spy.client() as client:
        assert await _send(db_pool, cfg, clock, client) == "sent"
    row = (await _email_rows(db_pool))[0]
    assert row["status"] == "sent" and row["attempts"] == 2


async def test_erro_de_rede_tambem_e_retentado_sem_propagar(
    db_pool: asyncpg.Pool, clock: FakeClock
) -> None:
    cfg = make_settings()
    await _new_alert(db_pool, cfg, clock)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rede (fake)", request=request)

    spy = ResendSpy(boom)
    async with spy.client() as client:
        assert await _send(db_pool, cfg, clock, client) == "pending"
    row = (await _email_rows(db_pool))[0]
    assert row["status"] == "pending" and row["last_error"]


async def test_desiste_no_limite_de_tentativas_e_registra_failed(
    db_pool: asyncpg.Pool, clock: FakeClock, resend_spy: ResendSpy
) -> None:
    cfg = make_settings(email_max_attempts=2, email_backoff_seconds="60")
    await _new_alert(db_pool, cfg, clock)
    resend_spy.fail_with = 503
    statuses: list[str | None] = []
    async with resend_spy.client() as client:
        for _ in range(3):
            statuses.append(await _send(db_pool, cfg, clock, client))
            clock.advance(seconds=120)
    assert statuses == ["pending", "failed", None]
    row = (await _email_rows(db_pool))[0]
    assert row["status"] == "failed" and row["attempts"] == 2
    assert row["last_error"] == "http_503"
    assert resend_spy.calls == 2
