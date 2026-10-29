"""Leitura do Inbox e do chat (PN-10, PN-12, PN-6, PN-59) contra Postgres real."""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta

import asyncpg
import pytest

from app.panel.services import inbox_read
from app.panel.services.inbox_read import (
    CHAT_LIMIT,
    PAGE_SIZE,
    get_chat,
    inbox_counts,
    inbox_page,
    parse_tab,
)
from app.panel.services.scope import Scope
from tests.panel import factories
from tests.panel.factories import BRAND_A, BRAND_B, PanelWorld

pytestmark = pytest.mark.db

ALL = Scope(None)


async def add_trace(
    conn: asyncpg.Connection,
    user_id: int,
    at: datetime,
    body: str | None = "Oi",
    msg_type: str = "text",
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, user_id, "
            "access_decision, decision, body) "
            "VALUES (gen_random_uuid()::text, $1, $2, $3, 'allowed', 'allowed', $4) RETURNING id",
            at, msg_type, user_id, body,
        )
    )


async def add_outbound(
    conn: asyncpg.Connection,
    user_id: int,
    at: datetime,
    *,
    kind: str = "media_ack",
    body: str | None = "Equipe avisada.",
    status: str = "sent",
    trace_id: int | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO chat.outbound_messages (user_id, to_phone, kind, required_status, body, "
        "inbound_trace_id, status, next_attempt_at, created_at, sent_at) "
        "VALUES ($1, '+5511900000000', $2, 'active', $3, $4, $5, $6, $6, $6)",
        user_id, kind, body, trace_id, status, at,
    )


async def names(
    conn: asyncpg.Connection, scope: Scope, world: PanelWorld, **kw: object
) -> list[str]:
    page = await inbox_page(conn, scope, now=world.now, **kw)  # type: ignore[arg-type]
    return [r.name for r in page.rows]


def test_parse_tab() -> None:
    assert parse_tab("midias") == "midias"
    assert parse_tab("x") == "conversas"
    assert parse_tab(None) == "conversas"


def test_usa_lateral() -> None:
    assert "LATERAL" in inspect.getsource(inbox_read)


async def test_abas_separam_conversas_e_midias(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    conv = await names(db_conn, ALL, world, tab="conversas")
    media = await names(db_conn, ALL, world, tab="midias")
    assert sorted(conv) == ["Vendedor Duas Marcas", "Vendedor Uma Marca"]
    assert media == ["Vendedor So Midia"]


async def test_escopo_de_marca_e_midia_sem_marca_em_todas(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    assert sorted(await names(db_conn, Scope(BRAND_A), world)) == [
        "Vendedor Duas Marcas", "Vendedor Uma Marca",
    ]
    assert await names(db_conn, Scope(BRAND_B), world) == ["Vendedor Duas Marcas"]
    for brand in (BRAND_A, BRAND_B):  # PN-6: midia sem marca aparece em toda marca
        assert await names(db_conn, Scope(brand), world, tab="midias") == ["Vendedor So Midia"]
    assert await names(db_conn, Scope("jungle"), world) == []


async def test_so_aguardando_e_contagem_por_aba(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    aguard = await names(db_conn, ALL, world, awaiting_only=True)
    assert sorted(aguard) == ["Vendedor Duas Marcas", "Vendedor Uma Marca"]
    counts = await inbox_counts(db_conn, ALL)
    assert (counts.conversas, counts.midias) == (2, 1)
    b = await inbox_counts(db_conn, Scope(BRAND_B))
    assert (b.conversas, b.midias) == (1, 1)
    page = await inbox_page(db_conn, ALL, now=world.now, tab="midias", awaiting_only=True)
    assert page.counts == counts
    assert [r.awaiting_count for r in page.rows] == [1]


async def test_aguardando_mostra_tempo_e_classificacao(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    page = await inbox_page(db_conn, Scope(BRAND_B), now=world.now)
    row = page.rows[0]
    assert row.awaiting_count == 1
    assert row.awaiting_label == "7 h"  # J-0007 criado 7 h antes de `now`
    assert row.classification == "PRECO"  # ticket mais recente da marca B


async def test_ultima_mensagem_e_placeholder_de_midia(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.media_only
    await add_trace(db_conn, uid, world.now - timedelta(hours=3), body="Texto antigo")
    await add_trace(db_conn, uid, world.now - timedelta(hours=1), body=None, msg_type="audio")
    page = await inbox_page(db_conn, ALL, now=world.now, tab="midias")
    row = page.rows[0]
    assert row.last_text == "[áudio]"
    assert row.last_time == "08:00"  # 11:00 UTC em Sao Paulo
    assert row.initial == "V"


async def test_ordena_pela_mensagem_mais_recente(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await add_trace(db_conn, world.sellers.one_brand, world.now - timedelta(hours=5))
    await add_trace(db_conn, world.sellers.two_brands, world.now - timedelta(hours=1))
    assert await names(db_conn, ALL, world) == ["Vendedor Duas Marcas", "Vendedor Uma Marca"]
    await add_trace(db_conn, world.sellers.one_brand, world.now - timedelta(minutes=1))
    assert await names(db_conn, ALL, world) == ["Vendedor Uma Marca", "Vendedor Duas Marcas"]


async def test_apenas_cadastrados_e_retirados_com_historico(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    revoked_hist = await factories.insert_user(
        db_conn, name="Retirado Com Historico", seq=20, brands=[BRAND_A], now=world.now
    )
    no_hist = await factories.insert_user(
        db_conn, name="Retirado Sem Historico", seq=21, brands=[BRAND_A], now=world.now
    )
    await db_conn.execute(
        "UPDATE access.users SET status = 'revoked', revoked_at = $1, revoked_by = 'teste', "
        "revoked_reason = 'other' WHERE id = ANY($2::bigint[])",
        world.now, [revoked_hist, no_hist],
    )
    await factories.insert_user(
        db_conn, name="Em Aceite", seq=22, brands=[BRAND_A], now=world.now,
        status="pending_optin",
    )
    await add_trace(db_conn, revoked_hist, world.now - timedelta(days=2))
    # numero desconhecido: so tentativa de acesso, nunca aparece no Inbox
    attempt = int(
        await db_conn.fetchval(
            "INSERT INTO access.access_attempts (phone_key, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type) "
            "VALUES ('5511977777777', 'unknown', 1, $1, $1, 'text') RETURNING id",
            world.now,
        )
    )
    await db_conn.execute(
        "INSERT INTO chat.message_trace (wa_message_id, received_at, msg_type, attempt_id, "
        "access_decision, decision, body) VALUES ('w-unk', $1, 'text', $2, 'blocked', 'x', "
        "'segredo do desconhecido')",
        world.now, attempt,
    )
    page = await inbox_page(db_conn, ALL, now=world.now)
    got = [r.name for r in page.rows]
    assert "Retirado Com Historico" in got
    assert "Em Aceite" in got
    assert "Retirado Sem Historico" not in got
    assert all("desconhecido" not in (r.last_text or "") for r in page.rows)


async def test_busca_por_nome_sem_acento_e_por_telefone(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    await factories.insert_user(
        db_conn, name="José Álvares", seq=30, brands=[BRAND_A], now=world.now
    )
    assert await names(db_conn, ALL, world, query="jose alv") == ["José Álvares"]
    assert await names(db_conn, ALL, world, query="ÁLVARES") == ["José Álvares"]
    assert await names(db_conn, ALL, world, query="55119000 00030") == ["José Álvares"]
    assert await names(db_conn, ALL, world, query="+55 (11) 9000-00030") == ["José Álvares"]
    assert await names(db_conn, ALL, world, query="nao existe") == []
    assert await names(db_conn, ALL, world, query="%") == []
    assert await names(db_conn, ALL, world, query="_") == []


async def test_busca_com_letras_nao_casa_telefone(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    assert await names(db_conn, ALL, world, query="vendedor 1") == []


async def test_paginacao_de_50(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    for i in range(55):
        await factories.insert_user(
            db_conn, name=f"Extra {i:02d}", seq=100 + i, brands=[BRAND_A], now=world.now
        )
    p1 = await inbox_page(db_conn, ALL, now=world.now, page=1)
    p2 = await inbox_page(db_conn, ALL, now=world.now, page=2)
    p9 = await inbox_page(db_conn, ALL, now=world.now, page=9)
    assert PAGE_SIZE == 50
    # 55 extras + uma marca + duas marcas (a de so midia fica fora da aba Conversas)
    assert p1.total == p2.total == p9.total == 57
    assert (len(p1.rows), len(p2.rows), len(p9.rows)) == (50, 7, 0)
    ids = {r.user_id for r in p1.rows} | {r.user_id for r in p2.rows}
    assert len(ids) == 57


async def test_chat_usuario_inexistente(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    assert await get_chat(db_conn, 999999, now=world.now) is None


async def test_chat_bolhas_bot_humano_e_media_ack(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.media_only  # tem ticket answered_human (autor Admin Teste)
    t0 = world.now - timedelta(hours=2)
    trace = await add_trace(db_conn, uid, t0, body=None, msg_type="audio")
    await add_outbound(db_conn, uid, t0 + timedelta(seconds=45), trace_id=trace)
    await add_outbound(
        db_conn, uid, t0 + timedelta(seconds=50), kind="under_construction", status="discarded"
    )
    chat = await get_chat(db_conn, uid, now=world.now)
    assert chat is not None
    seller = next(e for e in chat.entries if e.role == "seller")
    assert (seller.author, seller.body, seller.responded_in) == ("Vendedor", "[áudio]", None)
    bots = [e for e in chat.entries if e.role == "bot"]
    assert len(bots) == 1  # a descartada fica fora
    assert (bots[0].author, bots[0].kind, bots[0].body) == ("Bot", "media_ack", "Equipe avisada.")
    assert bots[0].responded_in == "respondeu em 45 s"
    humans = [e for e in chat.entries if e.role == "human"]
    assert [(h.author, h.kind) for h in humans] == [("Humano", "Admin Teste")]


async def test_media_ack_sem_corpo_tem_texto_padrao(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.one_brand
    await add_outbound(db_conn, uid, world.now - timedelta(hours=1), body=None)
    chat = await get_chat(db_conn, uid, now=world.now)
    assert chat is not None
    bot = next(e for e in chat.entries if e.role == "bot")
    assert bot.author == "Bot" and bot.body


async def test_chat_separadores_de_dia_e_tempo_de_resposta(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.two_brands
    base = world.now  # 26/10 12:00 UTC = 09:00 em Sao Paulo
    q1, q2, q3 = base - timedelta(days=3), base - timedelta(days=1), base - timedelta(hours=1)
    await add_trace(db_conn, uid, q1, body="Pergunta antiga")
    await add_outbound(db_conn, uid, q1 + timedelta(seconds=59), body="R1")
    await add_trace(db_conn, uid, q2, body="Pergunta de ontem")
    await add_outbound(db_conn, uid, q2 + timedelta(minutes=125), body="R2")
    await add_trace(db_conn, uid, q3, body="Pergunta de hoje")
    await add_outbound(db_conn, uid, q3 + timedelta(seconds=3600), body="R3")
    chat = await get_chat(db_conn, uid, now=world.now)
    assert chat is not None
    by_body = {e.body: e for e in chat.entries if e.body}
    assert by_body["Pergunta antiga"].day == "23/10/2026"
    assert by_body["Pergunta antiga"].new_day and not by_body["R1"].new_day
    assert by_body["Pergunta de ontem"].day == "Ontem" and by_body["Pergunta de ontem"].new_day
    assert by_body["Pergunta de hoje"].day == "Hoje" and by_body["Pergunta de hoje"].new_day
    assert by_body["R1"].responded_in == "respondeu em 59 s"
    assert by_body["R2"].responded_in == "respondeu em 2 h 5 min"
    assert by_body["R3"].responded_in == "respondeu em 1 h"
    assert by_body["Pergunta de hoje"].time == "08:00"


async def test_chat_limita_a_200_ultimas_em_ordem(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.one_brand
    for i in range(CHAT_LIMIT + 20):
        await add_trace(db_conn, uid, world.now - timedelta(minutes=300 - i), body=f"m{i:03d}")
    chat = await get_chat(db_conn, uid, now=world.now)
    assert chat is not None
    assert len(chat.entries) == CHAT_LIMIT
    ats = [e.at for e in chat.entries]
    assert ats == sorted(ats)
    assert chat.entries[-1].body == f"m{CHAT_LIMIT + 19:03d}"


async def test_chat_cabecalho_tickets_recentes_e_aguardando(
    db_conn: asyncpg.Connection, world: PanelWorld
) -> None:
    uid = world.sellers.one_brand
    await add_trace(db_conn, uid, world.now - timedelta(hours=1), body="a")
    await add_trace(db_conn, uid, world.now - timedelta(hours=2), body="b")
    chat = await get_chat(db_conn, uid, now=world.now)
    assert chat is not None
    h = chat.header
    assert (h.name, h.initial, h.profile, h.ufs, h.message_count) == (
        "Vendedor Uma Marca", "V", "vendedor", ["SP"], 2,
    )
    assert h.phone_masked == "+55 11 9••••-0001"
    assert len(chat.recent_tickets) == 4
    assert {t.status for t in chat.recent_tickets} == {
        "answered_bot", "answered_human", "awaiting", "send_error",
    }
    assert len(chat.awaiting_ticket_numbers) == 1


async def test_leitura_nao_envia_nada(db_conn: asyncpg.Connection, world: PanelWorld) -> None:
    before = await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages")
    await get_chat(db_conn, world.sellers.one_brand, now=world.now)
    await inbox_page(db_conn, ALL, now=world.now)
    assert await db_conn.fetchval("SELECT count(*) FROM chat.outbound_messages") == before
