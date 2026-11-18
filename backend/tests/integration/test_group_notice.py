"""Aviso de escalado no grupo: so escalados, formato, intervalo e WAHA do Jarvis (RH-T07).

RH-1 (so escalados), RH-2 (texto), RH-3 (um por vez, com intervalo), RH-4 (o grupo nao e
lido) e RH-21 (sai pelo WAHA do Jarvis). Dados ficticios; WAHA por httpx.MockTransport.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app.core.clock import FakeClock
from app.services import deliveries, waha_inbound
from app.services.flags import Flags
from app.services.question_tickets import create_question_ticket
from app.services.tickets import create_media_ticket
from tests.integration.test_deliveries import NOW, Recorder, _group_flag, make_settings
from tests.integration.test_schema_access_chat import add_trace, add_user

pytestmark = pytest.mark.db

GROUP = "120363000000@g.us"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "waha"


class FakeTime:
    """Relogio monotonico e sleep falsos para o intervalo do grupo."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


@pytest.fixture
def fake_time(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTime]:
    ft = FakeTime()
    monkeypatch.setattr(deliveries, "_monotonic", ft.monotonic)
    monkeypatch.setattr(deliveries, "_sleep", ft.sleep)
    monkeypatch.setattr(deliveries, "_last_group_send", None)
    yield ft


async def _seller(conn: asyncpg.Connection, wamid: str, seq: int = 1) -> tuple[int, int]:
    user_id = await add_user(
        conn, status="active", accepted_at=NOW, accepted_notice_version="v1",
        name="Ana Maria", ufs=["CE"], phone_e164=f"+55119876543{seq:02d}",
        phone_key=f"5511876543{seq:02d}",
    )  # fmt: skip
    return user_id, await add_trace(conn, wamid, user_id=user_id, msg_type="text")


async def _question(
    pool: asyncpg.Pool, flags: Flags, *, escalated: bool, wamid: str = "wamid.q1", seq: int = 1
) -> int:
    async with pool.acquire() as conn:
        user_id, trace_id = await _seller(conn, wamid, seq)
        ticket = await create_question_ticket(
            conn, user_id=user_id, trace_id=trace_id, message="Tem versão sem lactose?",
            flags=flags, clock=FakeClock(NOW), brand="jungle", escalated=escalated,
            escalation_reason="no_match" if escalated else None,
        )  # fmt: skip
    delivery: int = await pool.fetchval(
        "SELECT id FROM ops.deliveries WHERE source_type = 'ticket' AND source_id = $1", ticket.id
    )
    return delivery


async def _dispatch(
    pool: asyncpg.Pool, delivery_id: int, flags: Flags, waha: Recorder, **cfg: Any
) -> str | None:
    async with waha.client() as client:
        return await deliveries.dispatch(
            pool, delivery_id, flags=flags, settings=make_settings(**cfg),
            clock=FakeClock(NOW), waha_client=client,
        )  # fmt: skip


def _texts(waha: Recorder) -> list[str]:
    return [str(json.loads(r.content)["text"]) for r in waha.requests]


async def test_escalado_vai_ao_grupo_no_formato_novo(
    db_pool: asyncpg.Pool, fake_time: FakeTime
) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    delivery = await _question(db_pool, flags, escalated=True)
    waha = Recorder(200)
    assert await _dispatch(db_pool, delivery, flags, waha) == "sent"
    [text] = _texts(waha)
    number = await db_pool.fetchval("SELECT ticket_number FROM chat.tickets")
    assert text.startswith(f"🔔 Vendedor enviou nova pergunta (ticket {number})\n")
    assert "Marca: Jungle · Motivo: sem resposta na FAQ\nVendedor: Ana (CE)\n" in text
    assert "“Tem versão sem lactose?”" in text
    assert f"https://painel.example.com/dashboard/inbox/ticket/{number}" in text
    assert "+551198765430" not in text and "98765430" not in text and "Maria" not in text


async def test_respondido_pelo_jarvis_nao_vai_ao_grupo(
    db_pool: asyncpg.Pool, fake_time: FakeTime
) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    delivery = await _question(db_pool, flags, escalated=False)
    waha = Recorder(200)
    assert await _dispatch(db_pool, delivery, flags, waha) == "skipped"
    assert waha.requests == []
    row = await db_pool.fetchrow("SELECT status, last_error FROM ops.deliveries")
    assert (row["status"], row["last_error"]) == ("skipped", "not_escalated")


async def test_midia_usa_o_mesmo_formato(db_pool: asyncpg.Pool, fake_time: FakeTime) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    async with db_pool.acquire() as conn:
        user_id, trace_id = await _seller(conn, "wamid.audio")
        ticket = await create_media_ticket(
            conn, user_id, trace_id, "audio", flags=flags, clock=FakeClock(NOW)
        )
    delivery: int = await db_pool.fetchval(
        "SELECT id FROM ops.deliveries WHERE source_id = $1", ticket.id
    )
    waha = Recorder(200)
    assert await _dispatch(db_pool, delivery, flags, waha) == "sent"
    [text] = _texts(waha)
    assert f"(ticket {ticket.ticket_number})" in text and "Mensagem:\n[áudio]\n" in text


async def test_grupo_desligado_fica_so_no_painel(
    db_pool: asyncpg.Pool, fake_time: FakeTime
) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    delivery = await _question(db_pool, flags, escalated=True)
    await _group_flag(db_pool, flags, False)
    waha = Recorder(200)
    assert await _dispatch(db_pool, delivery, flags, waha) == "panel_only"
    assert waha.requests == []


async def test_dois_avisos_respeitam_o_intervalo(
    db_pool: asyncpg.Pool, fake_time: FakeTime
) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    first = await _question(db_pool, flags, escalated=True, wamid="wamid.a")
    second = await _question(db_pool, flags, escalated=True, wamid="wamid.b", seq=2)
    waha = Recorder(200)
    spaced = {"team_group_min_interval_seconds": 3}
    assert await _dispatch(db_pool, first, flags, waha, **spaced) == "sent"
    fake_time.now += 1.0  # o segundo chega 1 s depois
    assert await _dispatch(db_pool, second, flags, waha, **spaced) == "sent"
    assert fake_time.slept == [2.0] and len(waha.requests) == 2
    fake_time.now += 10.0  # depois de esperar o bastante, nao dorme
    third = await _question(db_pool, flags, escalated=True, wamid="wamid.c", seq=3)
    assert await _dispatch(db_pool, third, flags, waha, **spaced) == "sent"
    assert fake_time.slept == [2.0]


async def test_sai_pelo_waha_do_jarvis_so_para_o_grupo(
    db_pool: asyncpg.Pool, fake_time: FakeTime
) -> None:
    flags = Flags(db_pool, FakeClock(NOW))
    await _group_flag(db_pool, flags, True)
    delivery = await _question(db_pool, flags, escalated=True)
    waha = Recorder(200)
    await _dispatch(
        db_pool, delivery, flags, waha, jarvis_waha_api_key=SecretStr("chave-do-jarvis")
    )
    [request] = waha.requests
    body = json.loads(request.content)
    assert (request.url.host, request.url.port, request.url.path) == (
        "127.0.0.1", 3001, "/api/sendText",
    )  # fmt: skip
    assert request.headers["x-api-key"] == "chave-do-jarvis"
    assert (body["session"], body["chatId"]) == ("jarvis", GROUP)


def test_mensagem_no_grupo_da_equipe_e_descartada() -> None:
    """RH-4: nem o grupo da equipe e lido; o que a equipe escreve nao chega a ninguem."""
    body = json.loads((FIXTURES / "message_group.json").read_text(encoding="utf-8"))
    body["payload"]["from"] = GROUP
    event = waha_inbound.parse_event(body, session=body["session"])
    assert event == waha_inbound.WahaIgnored(waha_inbound.GROUP)
