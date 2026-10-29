"""Chave do grupo da equipe (PN-49, PN-60, PN-61): desligada = nada ao WAHA; ligada = aviso.

Postgres real; WAHA e Resend como espioes (`WahaSpy`/`ResendSpy`, sem rede). O resumo das
08:05 (PN-61) ainda nao existe no codigo: o teste dele esta marcado xfail(strict).
"""

from __future__ import annotations

from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.core.clock import FakeClock
from app.panel.services.workers import discover_loops
from app.services.alerts import raise_alert
from app.services.deliveries import dispatch
from app.services.flags import Flags
from app.services.tickets import create_media_ticket
from tests.integration.test_app_e2e import GROUP_ON, add_user, fixture_bytes, start_env, wait_for
from tests.integration.test_schema_access_chat import add_user as add_pf_user
from tests.panel.fakes import WahaSpy

pytestmark = pytest.mark.db

PHONE = "+5511987654321"
NAME = "Ana"


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
        "panel_base_url": "https://painel.example.com",
    }
    base.update(over)
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


async def _set_group(pool: asyncpg.Pool, value: bool) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await Flags(pool).set_bool(conn, "team_group_enabled", value, "caio")


async def _make_alert(pool: asyncpg.Pool, cfg: Settings, clock: FakeClock) -> int:
    async with pool.acquire() as conn:
        user_id = await add_pf_user(
            conn, status="active", accepted_at=clock.now(), accepted_notice_version="v1"
        )
        alert_id = await raise_alert(
            conn, "api_failure", "high", "Falhas definitivas de envio pela API do WhatsApp.",
            {"failure_count": 5}, user_id=user_id,
            flags=Flags(pool, clock), settings=cfg, clock=clock,
        )  # fmt: skip
    assert alert_id is not None
    return alert_id


async def _make_media_ticket(pool: asyncpg.Pool, clock: FakeClock) -> int:
    async with pool.acquire() as conn, conn.transaction():
        user_id = await add_pf_user(
            conn, status="active", accepted_at=clock.now(), accepted_notice_version="v1"
        )
        ticket = await create_media_ticket(
            conn, user_id, None, "audio", flags=Flags(pool, clock), clock=clock
        )
    return ticket.id


async def _group_rows(pool: asyncpg.Pool, source_type: str) -> list[asyncpg.Record]:
    return list(
        await pool.fetch(
            "SELECT * FROM ops.deliveries WHERE channel = 'group' AND source_type = $1 ORDER BY id",
            source_type,
        )
    )


async def _dispatch_group(
    pool: asyncpg.Pool, cfg: Settings, clock: FakeClock, waha: WahaSpy, source_type: str
) -> str | None:
    row = (await _group_rows(pool, source_type))[0]
    async with waha.client() as client:
        return await dispatch(
            pool, row["id"], flags=Flags(pool, clock), settings=cfg, clock=clock,
            waha_client=client,
        )  # fmt: skip


# ----------------------------------------------------------------- PN-60: desligada


async def test_desligada_alerta_fica_so_no_painel_e_nada_vai_ao_waha(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _make_alert(db_pool, cfg, clock)
    assert (await _group_rows(db_pool, "alert"))[0]["status"] == "panel_only"
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "alert") is None
    assert waha_spy.calls == 0


async def test_desligada_aviso_de_midia_fica_so_no_painel(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    await _make_media_ticket(db_pool, clock)
    assert (await _group_rows(db_pool, "ticket"))[0]["status"] == "panel_only"
    cfg = make_settings()
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "ticket") is None
    assert waha_spy.calls == 0


async def test_desligada_depois_de_enfileirar_vira_panel_only_sem_enviar(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _set_group(db_pool, True)
    await _make_alert(db_pool, cfg, clock)
    assert (await _group_rows(db_pool, "alert"))[0]["status"] == "pending"
    await _set_group(db_pool, False)  # desligada antes do envio
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "alert") == "panel_only"
    assert waha_spy.calls == 0
    assert (await _group_rows(db_pool, "alert"))[0]["status"] == "panel_only"


async def test_chave_padrao_e_desligada(db_pool: asyncpg.Pool) -> None:
    assert await Flags(db_pool).team_group_enabled() is False


# ----------------------------------------------------------------- PN-61: ligada


async def test_ligada_alerta_vai_ao_grupo_sem_pii(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _set_group(db_pool, True)
    await _make_alert(db_pool, cfg, clock)
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "alert") == "sent"
    assert waha_spy.calls == 1
    body = waha_spy.bodies()[0]
    assert isinstance(body, dict)
    text = str(body["text"])
    assert text.startswith("Alerta high: api_failure")
    for secret in (PHONE, "987654321", NAME):
        assert secret not in text
    assert (await _group_rows(db_pool, "alert"))[0]["status"] == "sent"


async def test_ligada_aviso_de_midia_vai_ao_grupo_sem_telefone(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _set_group(db_pool, True)
    await _make_media_ticket(db_pool, clock)
    assert (await _group_rows(db_pool, "ticket"))[0]["status"] == "pending"
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "ticket") == "sent"
    body = waha_spy.bodies()[0]
    assert isinstance(body, dict)
    text = str(body["text"])
    assert "aguardando a equipe" in text and "[áudio]".strip("[]") in text
    assert PHONE not in text and NAME not in text


async def test_ligada_falha_do_waha_nao_levanta_e_fica_para_retentar(
    db_pool: asyncpg.Pool, clock: FakeClock, waha_spy: WahaSpy
) -> None:
    cfg = make_settings()
    await _set_group(db_pool, True)
    await _make_alert(db_pool, cfg, clock)
    waha_spy.fail_with = 500
    assert await _dispatch_group(db_pool, cfg, clock, waha_spy, "alert") == "pending"
    assert (await _group_rows(db_pool, "alert"))[0]["last_error"] == "http_500"


# ----------------------------------------------------------------- ponta a ponta (webhook)


async def _media_via_webhook(pool: asyncpg.Pool, **over: Any) -> tuple[list[str], int]:
    """Midia pelo webhook real; devolve (paths HTTP de saida, entregas de grupo `sent`)."""
    await add_user(pool)
    env = start_env(**over)
    try:
        assert env.post(fixture_bytes("image")).status_code == 200

        async def done() -> bool:
            rows = await pool.fetch("SELECT status FROM ops.deliveries")
            return bool(env.channel.calls) and bool(rows) and rows[0]["status"] != "pending"

        await wait_for(done)
        paths = [call.url.path for call in env.http_calls]
        sent = int(await pool.fetchval("SELECT count(*) FROM ops.deliveries WHERE status = 'sent'"))
        return paths, sent
    finally:
        env.client.__exit__(None, None, None)


async def test_webhook_midia_com_chave_desligada_nada_ao_waha(db_pool: asyncpg.Pool) -> None:
    paths, sent = await _media_via_webhook(db_pool)
    assert paths == [] and sent == 0
    assert await db_pool.fetchval("SELECT status FROM ops.deliveries") == "panel_only"


async def test_webhook_midia_com_chave_ligada_avisa_o_grupo(db_pool: asyncpg.Pool) -> None:
    paths, sent = await _media_via_webhook(db_pool, **GROUP_ON)
    assert paths == ["/api/sendText"] and sent == 1


# ----------------------------------------------------------------- PN-61: resumo 08:05


@pytest.mark.xfail(strict=True, reason="resumo 08:05 ainda nao implementado")
def test_resumo_diario_das_0805_existe_como_laco_do_painel() -> None:
    """Spec PN-61: com a chave ligada, o grupo recebe o resumo diario as 08:05.

    Espera um laco do painel (`LOOP` em `app.panel.services.*`) de resumo diario.
    """
    names = [loop.name for loop in discover_loops()]
    assert any("resumo" in n or "summary" in n for n in names), names
