"""Leitura de Pessoas (PN-31, PN-37, PN-67) contra Postgres real."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.core.clock import FakeClock
from app.panel.services import people_read
from app.panel.services.people_read import (
    PAGE_SIZE,
    UNKNOWN_FIRST_TEXT_KEY,
    parse_tab,
    pending_requests,
    people_counts,
    people_page,
)
from app.services.flags import Flags
from tests.panel.factories import BRAND_A, insert_user

pytestmark = pytest.mark.db

SECRET_TEXT = "texto secreto da primeira mensagem"
APP_DIR = Path(people_read.__file__).resolve().parents[2]
FRONTEND_DIR = APP_DIR.parents[1] / "frontend"


async def add_attempt(
    conn: asyncpg.Connection,
    seq: int,
    now: datetime,
    *,
    count: int = 1,
    body: str | None = None,
    msg_type: str = "text",
    dismissed: bool = False,
    user_id: int | None = None,
    age_minutes: int = 0,
) -> int:
    at = now - timedelta(minutes=age_minutes)
    attempt_id = int(
        await conn.fetchval(
            "INSERT INTO access.access_attempts (phone_key, user_id, reason, attempt_count, "
            "first_attempt_at, last_attempt_at, first_msg_type, first_msg_chars, dismissed_at) "
            "VALUES ($1, $2, 'unknown', $3, $4, $4, $5, $6, $7) RETURNING id",
            f"55859000{seq:05d}",
            user_id,
            count,
            at,
            msg_type,
            len(body) if body else None,
            now if dismissed else None,
        )
    )
    if body is not None:
        await conn.execute(
            "INSERT INTO access.unknown_first_messages (attempt_id, body, stored_at) "
            "VALUES ($1, $2, $3)",
            attempt_id,
            body,
            at,
        )
    return attempt_id


async def set_status(conn: asyncpg.Connection, user_id: int, status: str, now: datetime) -> None:
    if status == "revoked":
        await conn.execute(
            "UPDATE access.users SET status='revoked', revoked_at=$2, revoked_by='teste', "
            "revoked_reason='left_company', status_changed_at=$2 WHERE id=$1",
            user_id,
            now,
        )
    else:  # declined
        await conn.execute(
            "UPDATE access.users SET status='declined', accepted_at=NULL, "
            "accepted_notice_version=NULL, declined_at=$2, status_changed_at=$2 WHERE id=$1",
            user_id,
            now,
        )


async def set_display(conn: asyncpg.Connection, value: bool, now: datetime) -> None:
    await conn.execute(
        "INSERT INTO ops.system_state (key, value, updated_at, updated_by) "
        "VALUES ($1, $2::jsonb, $3, 'teste') ON CONFLICT (key) DO UPDATE SET value = "
        "EXCLUDED.value",
        UNKNOWN_FIRST_TEXT_KEY,
        json.dumps(value),
        now,
    )


def flags_for(pool: asyncpg.Pool, clock: FakeClock) -> Flags:
    return Flags(pool, clock)


async def seed_people(conn: asyncpg.Connection, now: datetime) -> dict[str, int]:
    jose = await insert_user(conn, name="José Álvares", seq=1, brands=[BRAND_A], now=now)
    maria = await insert_user(conn, name="Maria Souza", seq=2, brands=[BRAND_A], now=now)
    pend = await insert_user(
        conn, name="Pedro Pendente", seq=3, brands=[BRAND_A], now=now, status="pending_optin"
    )
    gone = await insert_user(conn, name="Rita Revogada", seq=4, brands=[BRAND_A], now=now)
    nope = await insert_user(conn, name="Davi Declinou", seq=5, brands=[BRAND_A], now=now)
    await set_status(conn, gone, "revoked", now)
    await set_status(conn, nope, "declined", now - timedelta(days=1))
    await conn.execute(
        "UPDATE access.users SET ufs='{CE}', company='Farmácia Boa' WHERE id=$1", jose
    )
    return {"jose": jose, "maria": maria, "pend": pend, "gone": gone, "nope": nope}


def test_parse_tab() -> None:
    assert parse_tab("pedindo") == "pedindo"
    assert parse_tab("retiradas") == "retiradas"
    assert parse_tab("autorizadas") == "autorizadas"
    assert parse_tab("x") == "autorizadas"
    assert parse_tab(None) == "autorizadas"


async def test_three_tabs_and_counts(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    ids = await seed_people(db_conn, clock.now())
    await add_attempt(db_conn, 1, clock.now())
    await add_attempt(db_conn, 2, clock.now(), dismissed=True)
    await add_attempt(db_conn, 3, clock.now(), user_id=ids["gone"])  # com usuario: fora

    auth = await people_page(db_conn, "autorizadas")
    assert {r.id for r in auth.rows} == {ids["jose"], ids["maria"], ids["pend"]}
    assert (auth.counts.autorizadas, auth.counts.pedindo, auth.counts.retiradas) == (3, 1, 2)
    assert auth.total == 3

    removed = await people_page(db_conn, "retiradas")
    assert [r.id for r in removed.rows] == [ids["gone"], ids["nope"]]  # mais recente primeiro
    assert removed.rows[0].revoked_reason == "left_company"
    assert removed.total == 2


async def test_dados_excluidos_somem_das_abas_e_contagens(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    """Pessoa anonimizada pela LGPD (`anon:<id>`) fica so no banco, fora de toda aba."""
    ids = await seed_people(db_conn, clock.now())
    anon = f"anon:{ids['gone']}"
    await db_conn.execute(
        "UPDATE access.users SET name=$2, phone_e164=$2, phone_key=$2 WHERE id=$1",
        ids["gone"],
        anon,
    )
    removed = await people_page(db_conn, "retiradas")
    assert [r.id for r in removed.rows] == [ids["nope"]]
    assert removed.counts.retiradas == 1 and removed.total == 1
    assert (await people_page(db_conn, "retiradas", q="anon")).total == 0


async def test_search_accent_company_uf_phone(
    db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    ids = await seed_people(db_conn, clock.now())

    async def found(q: str) -> list[int]:
        return [r.id for r in (await people_page(db_conn, "autorizadas", q=q)).rows]

    assert await found("jose") == [ids["jose"]]
    assert await found("ÁLVARES") == [ids["jose"]]
    assert await found("farmacia boa") == [ids["jose"]]
    assert await found("ce") == [ids["jose"]]  # UF
    assert await found("zz") == []
    assert await found("%") == []  # curinga escapado
    assert await found("900000002") == [ids["maria"]]  # so digitos
    assert await found("+55 (11) 9000-00002") == [ids["maria"]]  # com mascara
    assert await found("+55 11 9000-00002") == [ids["maria"]]
    # busca vazia lista todos; contagens seguem a busca
    assert len(await found("")) == 3
    counts = await people_counts(db_conn, "rita")
    assert (counts.autorizadas, counts.pedindo, counts.retiradas) == (0, 0, 1)


async def test_phone_match_key_ninth_digit(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    uid = await insert_user(db_conn, name="Sem Nove", seq=9, brands=[BRAND_A], now=clock.now())
    # phone_key do PF e a match_key: celular BR de 13 digitos sem o 9o digito
    await db_conn.execute("UPDATE access.users SET phone_key='558599991234' WHERE id=$1", uid)
    page = await people_page(db_conn, "autorizadas", q="+55 85 99999-1234")
    assert [r.id for r in page.rows] == [uid]
    page = await people_page(db_conn, "autorizadas", q="+55 85 9999-1234")
    assert [r.id for r in page.rows] == [uid]


async def test_pagination_50_x_de_y(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    for i in range(120):
        await insert_user(
            db_conn, name=f"Pessoa {i:03d}", seq=100 + i, brands=[BRAND_A], now=clock.now()
        )
    p1 = await people_page(db_conn, "autorizadas", page=1)
    p3 = await people_page(db_conn, "autorizadas", page=3)
    p9 = await people_page(db_conn, "autorizadas", page=9)
    assert PAGE_SIZE == 50
    assert (len(p1.rows), p1.total, p1.pages) == (50, 120, 3)
    assert len(p3.rows) == 20
    assert p9.rows == [] and p9.total == 120
    assert p1.rows[0].name == "Pessoa 000"
    assert p1.rows[0].phone_masked == "+55 11 9••••-0100"


async def test_phone_is_masked_in_rows(db_conn: asyncpg.Connection, clock: FakeClock) -> None:
    await seed_people(db_conn, clock.now())
    page = await people_page(db_conn, "autorizadas")
    for row in page.rows:
        assert "•" in row.phone_masked and "9000" not in row.phone_masked


async def test_pending_hides_text_when_flag_off(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    now = clock.now()
    await add_attempt(db_conn, 1, now, count=3, body=SECRET_TEXT, age_minutes=5)
    await add_attempt(db_conn, 2, now, msg_type="audio", age_minutes=1)
    await add_attempt(db_conn, 3, now, count=1, dismissed=True)
    page = await pending_requests(db_conn, flags_for(db_pool, clock))
    assert page.text_display is False
    assert page.total == 2
    assert [r.first_text for r in page.rows] == [None, None]
    assert [r.attempt_count for r in page.rows] == [1, 3]  # ultima tentativa primeiro
    assert [r.highlight for r in page.rows] == [False, True]
    assert page.rows[0].first_msg_type == "audio"
    assert SECRET_TEXT not in repr(page.rows)


async def test_pending_hides_numbers_already_registered(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    """Quem escreveu antes de ser cadastrado pelo "+ Cadastrar" (sem Autorizar) nao
    continua em Pedindo acesso; o pedido de um numero desconhecido segue la."""
    now = clock.now()
    await add_attempt(db_conn, 1, now)
    await add_attempt(db_conn, 2, now)
    await insert_user(db_conn, name="Ja Cadastrado", seq=1, brands=[BRAND_A], now=now)
    await db_conn.execute(
        "UPDATE access.users SET phone_key = '5585900000001' WHERE name = 'Ja Cadastrado'"
    )
    page = await pending_requests(db_conn, flags_for(db_pool, clock))
    assert page.total == 1
    assert (await people_counts(db_conn)).pedindo == 1


async def test_pending_shows_only_first_text_when_flag_on(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    now = clock.now()
    await set_display(db_conn, True, now)
    await add_attempt(db_conn, 1, now, count=3, body=SECRET_TEXT, age_minutes=5)
    await add_attempt(db_conn, 2, now, msg_type="audio", age_minutes=1)
    page = await pending_requests(db_conn, flags_for(db_pool, clock))
    assert page.text_display is True
    by_type = {r.first_msg_type: r for r in page.rows}
    assert by_type["text"].first_text == SECRET_TEXT
    assert by_type["audio"].first_text is None  # midia: so o tipo


async def test_pending_search_by_phone_only(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    now = clock.now()
    await add_attempt(db_conn, 1, now)
    await add_attempt(db_conn, 2, now)
    flags = flags_for(db_pool, clock)
    assert (await pending_requests(db_conn, flags, q="900000001")).total == 1
    assert (await pending_requests(db_conn, flags, q="maria")).total == 0
    assert (await pending_requests(db_conn, flags, q="")).total == 2
    page = await pending_requests(db_conn, flags, q="900000001")
    assert page.counts.pedindo == 1


async def test_unknown_numbers_create_no_alert_and_read_is_pure(
    db_pool: asyncpg.Pool, db_conn: asyncpg.Connection, clock: FakeClock
) -> None:
    await set_display(db_conn, True, clock.now())
    await add_attempt(db_conn, 1, clock.now(), count=3, body=SECRET_TEXT)
    before = await db_conn.fetchval("SELECT count(*) FROM ops.alerts")
    await pending_requests(db_conn, flags_for(db_pool, clock))
    await people_counts(db_conn)
    assert await db_conn.fetchval("SELECT count(*) FROM ops.alerts") == before == 0
    assert await db_conn.fetchval("SELECT count(*) FROM ops.audit_log") == 0


def test_only_pending_requests_references_first_messages() -> None:
    """PN-37: nenhuma outra funcao, modulo, template ou JS referencia a tabela."""
    needle = "unknown_first_messages"
    offenders: list[str] = []
    for base in (APP_DIR / "panel", APP_DIR / "services", FRONTEND_DIR):
        for path in base.rglob("*"):
            if path.suffix not in {".py", ".html", ".js"} or not path.is_file():
                continue
            if needle in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(path.relative_to(APP_DIR.parents[1]).as_posix())
    # Exibir o texto: so people_read. Gravar: attempts (PF). Apagar (nunca ler o texto):
    # lgpd (anonimizacao) e retention (expurgo de 12 meses). Rotulo de contagem, sem
    # consulta: logs_retencao.
    allowed = {
        "backend/app/panel/services/people_read.py",
        "backend/app/services/attempts.py",
        "backend/app/panel/services/lgpd.py",
        "backend/app/panel/services/retention.py",
        "backend/app/panel/api/logs_retencao.py",
    }
    assert set(offenders) - allowed == set()
    source = Path(people_read.__file__).read_text(encoding="utf-8")
    assert source.count("JOIN access.unknown_first_messages") == 1  # so em _TEXT_JOIN
    assert source.count("_TEXT_JOIN") == 2  # definicao + uso
    assert "_TEXT_JOIN" in source.split("async def pending_requests")[1]
