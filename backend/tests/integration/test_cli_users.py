"""CLI parte 1: user add|import|revoke|list e flag (AC-15, 16, 17, 34, 13, R4)."""
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from pydantic import SecretStr

from app import cli
from app.config import Settings
from app.core.clock import FakeClock

CLOCK = FakeClock(datetime(2026, 10, 23, 12, 0, tzinfo=UTC))
PHONE = "+55 11 90000-0001"
HEADER = "nome,telefone,perfil,empresa,ufs,canais,marcas,gestor"
UTF8_BOM = b"\xef\xbb\xbf"


def make_settings() -> Settings:
    base: dict[str, Any] = {
        "database_url": SecretStr("postgresql://x"),
        "whatsapp_app_secret": SecretStr("s"),
        "whatsapp_verify_token": SecretStr("v"),
        "whatsapp_access_token": SecretStr("t"),
        "whatsapp_phone_number_id": "1",
        "welcome_template_name": "boas_vindas",
    }
    return Settings(_env_file=None, **base)  # type: ignore[call-arg]


SETTINGS = make_settings()


def add_args(**over: str | None) -> list[str]:
    fields: dict[str, str | None] = {
        "name": "Fulana", "phone": PHONE, "profile": "vendedor", "company": "Acme",
        "ufs": "SP|RJ", "channels": "farma", "brands": "jungle|zaya", "manager": "Gestora",
    }  # fmt: skip
    fields.update(over)
    argv = ["user", "add", "--by", "caio"]
    for key, value in fields.items():
        if value is not None:
            argv += [f"--{key}", value]
    return argv


async def go(pool: asyncpg.Pool, argv: list[str]) -> int:
    return await cli.run(argv, pool=pool, settings=SETTINGS, clock=CLOCK, wait_seconds=0)


async def count(pool: asyncpg.Pool, table: str) -> int:
    value: int = await pool.fetchval(f"SELECT count(*) FROM {table}")  # noqa: S608
    return value


def write_csv(tmp_path: Path, lines: list[str], sep: str = ",", bom: bool = False) -> Path:
    path = tmp_path / "pessoas.csv"
    text = "\n".join(line.replace(",", sep) for line in lines) + "\n"
    path.write_bytes((UTF8_BOM if bom else b"") + text.encode("utf-8"))
    return path


def csv_row(n: int, perfil: str = "vendedor") -> str:
    phone = f"+55119000000{n:02d}"
    return f"Fulana,{phone},{perfil},Acme,SP|RJ,farma,jungle|zaya,Gestora"


@pytest.mark.db
async def test_user_add_completo(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, add_args()) == 0
    out = capsys.readouterr().out
    assert "***0001" in out and "na fila" in out
    assert "Fulana" not in out and "90000" not in out
    row = await db_pool.fetchrow("SELECT status, created_by FROM access.users")
    assert row is not None and row["status"] == "pending_optin" and row["created_by"] == "caio"
    assert await count(db_pool, "chat.outbound_messages") == 1
    assert await db_pool.fetchval("SELECT count(*) FROM ops.audit_log WHERE actor_type = 'cli'")


@pytest.mark.db
async def test_user_add_resultado_do_envio(db_pool: asyncpg.Pool) -> None:
    await go(db_pool, add_args())
    await go(db_pool, add_args(phone="+55 11 90000-0002"))
    await db_pool.execute(
        "UPDATE chat.outbound_messages SET status = 'failed', error_code = '131026' "
        "WHERE user_id = 1"
    )
    await db_pool.execute("UPDATE chat.outbound_messages SET status = 'sent' WHERE user_id = 2")
    ctx = cli.Context(db_pool, SETTINGS, CLOCK, 0)
    assert await cli._welcome_result(ctx, 1) == "falhou: 131026"
    assert await cli._welcome_result(ctx, 2) == "enviado"


@pytest.mark.db
@pytest.mark.parametrize(
    ("option", "field"),
    [
        ("name", "nome"), ("phone", "telefone"), ("profile", "perfil"), ("company", "empresa"),
        ("ufs", "ufs"), ("channels", "canais"), ("brands", "marcas"), ("manager", "gestor"),
    ],
)  # fmt: skip
async def test_user_add_campo_faltando_nao_cria(
    db_pool: asyncpg.Pool,
    capsys: pytest.CaptureFixture[str],
    option: str,
    field: str,
) -> None:
    assert await go(db_pool, add_args(**{option: None})) == 2
    assert f"campo {field}" in capsys.readouterr().err
    assert await count(db_pool, "access.users") == 0
    assert await count(db_pool, "chat.outbound_messages") == 0


@pytest.mark.db
async def test_user_add_duplicado_rejeitado(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, add_args()) == 0
    assert await go(db_pool, add_args(phone="+5511900000001")) == 1
    assert "ja cadastrado" in capsys.readouterr().err
    assert await count(db_pool, "access.users") == 1


@pytest.mark.db
@pytest.mark.parametrize(("sep", "bom"), [(",", False), (";", True)])
async def test_import_valido(db_pool: asyncpg.Pool, tmp_path: Path, sep: str, bom: bool) -> None:
    path = write_csv(tmp_path, [HEADER, csv_row(1), csv_row(2)], sep=sep, bom=bom)
    assert await go(db_pool, ["user", "import", "--file", str(path), "--by", "caio"]) == 0
    assert await count(db_pool, "access.users") == 2
    assert await count(db_pool, "chat.outbound_messages") == 2


@pytest.mark.db
async def test_import_erro_na_linha_3_nao_grava(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_csv(tmp_path, [HEADER, csv_row(1), csv_row(2, perfil="chefe"), csv_row(3)])
    assert await go(db_pool, ["user", "import", "--file", str(path)]) == 2
    err = capsys.readouterr().err
    assert "linha 3" in err and "campo perfil" in err and "chefe" in err
    for table in ("access.users", "access.optin_requests", "chat.outbound_messages"):
        assert await count(db_pool, table) == 0


@pytest.mark.db
async def test_import_duplicata_no_arquivo_desfaz_tudo(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_csv(tmp_path, [HEADER, csv_row(1), csv_row(2), csv_row(1)])
    assert await go(db_pool, ["user", "import", "--file", str(path)]) == 2
    assert "linha 4, campo telefone" in capsys.readouterr().err
    assert await count(db_pool, "access.users") == 0
    assert await count(db_pool, "chat.outbound_messages") == 0


@pytest.mark.db
async def test_import_dry_run_nao_grava(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_csv(tmp_path, [HEADER, csv_row(1)])
    assert await go(db_pool, ["user", "import", "--file", str(path), "--dry-run"]) == 0
    assert "dry-run" in capsys.readouterr().out
    assert await count(db_pool, "access.users") == 0
    assert await count(db_pool, "ops.audit_log") == 0


@pytest.mark.db
async def test_import_cabecalho_incompleto_e_arquivo_ausente(
    db_pool: asyncpg.Pool, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = write_csv(tmp_path, ["nome,telefone", "Ana,+5511900000001"])
    assert await go(db_pool, ["user", "import", "--file", str(path)]) == 2
    assert "cabecalho" in capsys.readouterr().err
    assert await go(db_pool, ["user", "import", "--file", str(tmp_path / "x.csv")]) == 2


@pytest.mark.db
async def test_revoke_por_telefone_e_por_id_idempotente(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    await go(db_pool, add_args(phone="+55 11 90000-0002"))
    capsys.readouterr()
    argv = ["user", "revoke", "--phone", PHONE, "--reason", "left_company", "--by", "caio"]
    assert await go(db_pool, argv) == 0
    assert "***0001" in capsys.readouterr().out
    assert await go(db_pool, ["user", "revoke", "--id", "2", "--reason", "other"]) == 0
    assert await go(db_pool, ["user", "revoke", "--id", "1", "--reason", "other"]) == 0
    rows = await db_pool.fetch(
        "SELECT status, revoked_reason, revoked_by FROM access.users ORDER BY id"
    )
    assert [r["status"] for r in rows] == ["revoked", "revoked"]
    assert rows[0]["revoked_reason"] == "left_company" and rows[0]["revoked_by"] == "caio"
    audited = await db_pool.fetchval(
        "SELECT count(*) FROM ops.audit_log WHERE action = 'user.revoked'"
    )
    assert audited == 2


@pytest.mark.db
async def test_revoke_inexistente_e_motivo_invalido(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, ["user", "revoke", "--id", "99", "--reason", "other"]) == 1
    assert "nao encontrada" in capsys.readouterr().err
    assert await go(db_pool, ["user", "revoke", "--phone", "123", "--reason", "other"]) == 1
    with pytest.raises(SystemExit) as exc:
        await go(db_pool, ["user", "revoke", "--id", "1", "--reason", "porque_sim"])
    assert exc.value.code == 2


@pytest.mark.db
async def test_list_mascara_telefone_e_filtra(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    await go(db_pool, add_args())
    await go(db_pool, add_args(phone="+55 11 90000-0002"))
    await go(db_pool, ["user", "revoke", "--id", "2", "--reason", "other"])
    capsys.readouterr()
    assert await go(db_pool, ["user", "list"]) == 0
    out = capsys.readouterr().out
    assert "***0001" in out and "***0002" in out and "total: 2" in out
    assert "90000" not in out and "Fulana" not in out
    assert await go(db_pool, ["user", "list", "--status", "revoked"]) == 0
    out = capsys.readouterr().out
    assert "***0002" in out and "***0001" not in out and "total: 1" in out


@pytest.mark.db
async def test_flag_set_paused_grava_e_audita(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, ["flag", "set", "paused", "on", "--by", "caio"]) == 0
    stored = await db_pool.fetchval("SELECT value::text FROM ops.system_state WHERE key = 'paused'")
    assert stored == "true"
    audit = await db_pool.fetchrow("SELECT actor_type, actor, target_id FROM ops.audit_log")
    assert audit is not None and audit["actor_type"] == "cli" and audit["actor"] == "caio"
    assert audit["target_id"] == "paused"
    capsys.readouterr()
    assert await go(db_pool, ["flag", "get", "paused"]) == 0
    assert "paused: on" in capsys.readouterr().out


@pytest.mark.db
async def test_flag_team_group_e_brand(
    db_pool: asyncpg.Pool, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await go(db_pool, ["flag", "get", "team-group"]) == 0
    assert "team_group_enabled: off" in capsys.readouterr().out
    assert await go(db_pool, ["flag", "set", "team-group", "on"]) == 0
    assert await go(db_pool, ["flag", "get", "team-group"]) == 0
    assert "team_group_enabled: on" in capsys.readouterr().out
    assert await go(db_pool, ["flag", "set", "brand", "jungle", "off"]) == 0
    assert await go(db_pool, ["flag", "get", "brand", "jungle"]) == 0
    assert "brand_enabled:jungle: off" in capsys.readouterr().out
    assert await count(db_pool, "ops.audit_log") == 2


@pytest.mark.db
async def test_flag_uso_incorreto_nao_grava(db_pool: asyncpg.Pool) -> None:
    bad = (
        ["flag", "set", "paused", "talvez"],
        ["flag", "set", "brand", "on"],
        ["flag", "set", "brand", "jungle", "on", "extra"],
        ["flag", "get", "brand"],
    )
    for argv in bad:
        assert await go(db_pool, argv) == 2
    assert await count(db_pool, "ops.system_state") == 0


def test_help_lista_comandos(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "user" in out and "flag" in out
