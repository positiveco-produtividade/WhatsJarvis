"""Fabricas de dados do painel (dono: PN-T05). Tudo entra pelas tabelas reais.

Dados falsos, sem PII real. Duas marcas, tres vendedores (uma marca, duas marcas,
so midia), tickets de todos os status em `chat.tickets` (valores da migration 005)
e FAQ de 25 itens.

FAQ: `content.faq_items` nasce na migration 101 (PN-T08). Enquanto a tabela nao
existe, `create_faq` nao falha: devolve `FaqSeed(available=False)` e os testes que
dependem dela devem usar `skip_without_faq`.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import asyncpg
import pytest

__all__ = [
    "BRAND_A",
    "BRAND_B",
    "BRANDS",
    "FAQ_SIZE",
    "FaqSeed",
    "PanelWorld",
    "SeededTicket",
    "Sellers",
    "create_faq",
    "create_sellers",
    "create_tickets",
    "faq_table_exists",
    "insert_ticket",
    "insert_user",
    "normalize_question",
    "seed_world",
    "skip_without_faq",
    "truncate_content",
]

BRAND_A = "plant_power"
BRAND_B = "a_tal_da_castanha"
BRANDS = (BRAND_A, BRAND_B)
FAQ_SIZE = 25
TICKET_STATUSES = ("answered_bot", "answered_human", "awaiting", "send_error")
_FAQ_CATEGORIES = ("PRODUTO", "PRECO", "ARGUMENTO", "MATERIAL", "CAMPANHA")
_FAQ_BRANDS = ("todas", BRAND_A, BRAND_B)


@dataclass(frozen=True)
class Sellers:
    """Ids dos tres vendedores de teste."""

    one_brand: int
    two_brands: int
    media_only: int


@dataclass(frozen=True)
class SeededTicket:
    id: int
    ticket_number: str
    user_id: int
    status: str
    kind: str
    brand: str | None
    escalated: bool


@dataclass(frozen=True)
class FaqSeed:
    available: bool
    ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class PanelWorld:
    now: datetime
    brands: tuple[str, ...]
    sellers: Sellers
    tickets: list[SeededTicket] = field(default_factory=list)
    faq: FaqSeed = field(default_factory=lambda: FaqSeed(available=False))


def normalize_question(text: str) -> str:
    """Chave de pergunta: sem acento, minuscula, espacos colapsados (spec 4.3.2)."""
    decomposed = unicodedata.normalize("NFD", text)
    plain = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(plain.lower().split())


async def insert_user(
    conn: asyncpg.Connection,
    *,
    name: str,
    seq: int,
    brands: list[str],
    now: datetime,
    status: str = "active",
) -> int:
    """Insere um usuario (ativo por padrao, com aceite) direto em `access.users`."""
    phone_key = f"55119000{seq:05d}"
    active = status == "active"
    return int(
        await conn.fetchval(
            "INSERT INTO access.users (name, phone_e164, phone_key, profile, company, ufs, "
            "channels, brands, manager_name, status, status_changed_at, accepted_at, "
            "accepted_notice_version, created_at, created_by, updated_at) "
            "VALUES ($1, $2, $3, 'vendedor', 'Empresa Teste', '{SP}', '{farma}', $4, "
            "'Gestora Teste', $5, $6, $7, $8, $6, 'teste', $6) RETURNING id",
            name,
            f"+{phone_key}",
            phone_key,
            brands,
            status,
            now,
            now if active else None,
            "v1" if active else None,
        )
    )


async def create_sellers(conn: asyncpg.Connection, now: datetime) -> Sellers:
    """Vendedor de uma marca, de duas marcas e so de midia (sem marca nos tickets)."""
    one = await insert_user(conn, name="Vendedor Uma Marca", seq=1, brands=[BRAND_A], now=now)
    two = await insert_user(
        conn, name="Vendedor Duas Marcas", seq=2, brands=[BRAND_A, BRAND_B], now=now
    )
    media = await insert_user(conn, name="Vendedor So Midia", seq=3, brands=[BRAND_A], now=now)
    return Sellers(one_brand=one, two_brands=two, media_only=media)


async def insert_ticket(
    conn: asyncpg.Connection,
    *,
    number: int,
    user_id: int,
    status: str,
    created_at: datetime,
    brand: str | None = None,
    kind: str = "question",
    classification: str = "PRODUTO",
    escalated: bool = False,
    urgency: str | None = None,
    message: str | None = None,
) -> SeededTicket:
    """Insere um ticket respeitando os CHECKs da migration 005."""
    if escalated and urgency is None:
        urgency = "urgente"
    answered = status in ("answered_bot", "answered_human")
    answered_by = {"answered_bot": "bot", "answered_human": "Admin Teste"}.get(status)
    ticket_number = f"J-{number:04d}"
    text = message if message is not None else f"Pergunta de teste {ticket_number}"
    ticket_id = int(
        await conn.fetchval(
            "INSERT INTO chat.tickets (ticket_number, user_id, kind, brand, classification, "
            "message, summary, escalated, status, urgency, answered_by, answered_at, "
            "created_at, updated_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $6, $7, $8, $9, $10, $11, $12, $12) RETURNING id",
            ticket_number,
            user_id,
            kind,
            brand,
            classification,
            text,
            escalated,
            status,
            urgency,
            answered_by,
            created_at + timedelta(minutes=5) if answered else None,
            created_at,
        )
    )
    return SeededTicket(
        id=ticket_id,
        ticket_number=ticket_number,
        user_id=user_id,
        status=status,
        kind=kind,
        brand=brand,
        escalated=escalated,
    )


async def create_tickets(
    conn: asyncpg.Connection, sellers: Sellers, now: datetime
) -> list[SeededTicket]:
    """Tickets de todos os status; midia sem marca; escalados com urgencia."""
    out: list[SeededTicket] = []

    async def add(
        user_id: int,
        status: str,
        brand: str | None = None,
        *,
        kind: str = "question",
        classification: str = "PRODUTO",
        escalated: bool = False,
        urgency: str | None = None,
    ) -> None:
        number = len(out) + 1
        out.append(
            await insert_ticket(
                conn,
                number=number,
                user_id=user_id,
                status=status,
                created_at=now - timedelta(hours=number),
                brand=brand,
                kind=kind,
                classification=classification,
                escalated=escalated,
                urgency=urgency,
                message="[midia]" if kind == "media" else None,
            )
        )

    for status in TICKET_STATUSES:  # vendedor de uma marca: um de cada status
        await add(sellers.one_brand, status, BRAND_A)
    for brand in BRANDS:  # duas marcas: um aguardando escalado e um respondido por marca
        await add(
            sellers.two_brands,
            "awaiting",
            brand,
            classification="PRECO",
            escalated=True,
            urgency="muito_urgente",
        )
        await add(sellers.two_brands, "answered_bot", brand, classification="ARGUMENTO")
    await add(sellers.two_brands, "send_error", BRAND_B, classification="CAMPANHA")
    await add(sellers.two_brands, "answered_human", BRAND_B, classification="MATERIAL")
    for status in ("awaiting", "answered_human"):  # so midia: kind media, sem marca
        await add(sellers.media_only, status, kind="media", classification="MIDIA")
    return out


async def faq_table_exists(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('content.faq_items') IS NOT NULL"))


async def truncate_content(conn: asyncpg.Connection) -> None:
    """Limpa `content.*` (o TRUNCATE de tests/db_support.py nao cobre este schema)."""
    if await faq_table_exists(conn):
        await conn.execute("TRUNCATE content.faq_items RESTART IDENTITY CASCADE")


async def create_faq(conn: asyncpg.Connection, now: datetime, *, count: int = FAQ_SIZE) -> FaqSeed:
    """Insere `count` itens ativos em `content.faq_items`; sem a tabela (101), nao faz nada."""
    if not await faq_table_exists(conn):
        return FaqSeed(available=False)
    await truncate_content(conn)
    ids: list[int] = []
    for i in range(count):
        question = f"Pergunta de teste numero {i + 1}?"
        has_answer = i % 10 != 9  # 1 em 10 sem resposta, com bot_responde=false
        ids.append(
            int(
                await conn.fetchval(
                    "INSERT INTO content.faq_items (brand, category, question, question_key, "
                    "answer, variations, bot_responde, active, origin, version, created_at, "
                    "created_by, updated_at, updated_by) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, true, 'seed', 1, $8, 'teste', $8, "
                    "'teste') RETURNING id",
                    _FAQ_BRANDS[i % len(_FAQ_BRANDS)],
                    _FAQ_CATEGORIES[i % len(_FAQ_CATEGORIES)],
                    question,
                    normalize_question(question),
                    f"Resposta de teste {i + 1}." if has_answer else None,
                    [f"variacao {i + 1}"],
                    has_answer,
                    now,
                )
            )
        )
    return FaqSeed(available=True, ids=tuple(ids))


async def seed_world(conn: asyncpg.Connection, now: datetime) -> PanelWorld:
    """Monta o cenario completo: vendedores, tickets e (se existir a tabela) FAQ."""
    sellers = await create_sellers(conn, now)
    tickets = await create_tickets(conn, sellers, now)
    faq = await create_faq(conn, now)
    return PanelWorld(now=now, brands=BRANDS, sellers=sellers, tickets=tickets, faq=faq)


def skip_without_faq(world: PanelWorld) -> None:
    """Pula o teste quando a migration 101 ainda nao existe no banco de teste."""
    if not world.faq.available:
        pytest.skip("content.faq_items inexistente (migration 101 vem em PN-T08)")
