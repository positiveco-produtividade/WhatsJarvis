"""Runner minimo de migrations SQL numeradas (ADR 2026-10-23, spec 4.2.6).

Uso (a partir de backend/, com DATABASE_URL no ambiente):
    python -m app.db.migrate            aplica as pendentes
    python -m app.db.migrate --status   lista aplicadas/pendentes
    python -m app.db.migrate --check    sai com codigo 1 se faltar migration

Regras: ordem pelo prefixo numerico; prefixo duplicado e recusado; uma transacao
por arquivo; pg_advisory_lock; versao e SHA-256 em ops.schema_migrations; arquivo
ja aplicado e depois alterado e recusado. Nunca roda sozinho na subida do servico.
So SQL compativel com PostgreSQL 16.
"""

import argparse
import asyncio
import hashlib
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import asyncpg

DEFAULT_DIR = Path(__file__).resolve().parent.parent.parent / "migrations"
DEFAULT_TABLE = "ops.schema_migrations"
_LOCK_KEY = 7_422_001_001  # chave arbitraria do lock consultivo do runner
_FILE_RE = re.compile(r"^(\d{3,})_[a-z0-9_]+\.sql$")
_TABLE_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")

EXIT_OK = 0
EXIT_PENDING = 1
EXIT_ERROR = 2


class MigrationError(Exception):
    """Problema nos arquivos de migration ou divergencia com o banco."""


@dataclass(frozen=True)
class Migration:
    version: str  # nome do arquivo sem .sql, ex.: 001_schemas_ops_base
    number: int
    checksum: str
    sql: str


def _normalize(text: str) -> str:
    # CRLF vs LF (autocrlf no Windows) nao pode mudar o checksum.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def discover(directory: Path) -> list[Migration]:
    """Le e ordena as migrations; recusa nome invalido e prefixo duplicado."""
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        match = _FILE_RE.match(path.name)
        if match is None:
            raise MigrationError(f"nome de migration invalido: {path.name}")
        number = int(match.group(1))
        if number in found:
            raise MigrationError(
                f"prefixo duplicado {number:03d}: {found[number].version} e {path.stem}"
            )
        sql = _normalize(path.read_text(encoding="utf-8"))
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        found[number] = Migration(path.stem, number, checksum, sql)
    return [found[n] for n in sorted(found)]


def _check_table(table: str) -> str:
    if not _TABLE_RE.match(table):
        raise MigrationError(f"nome de tabela invalido: {table!r}")
    return table


async def _ensure_table(conn: Any, table: str) -> None:
    schema = _check_table(table).split(".")[0]
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    await conn.execute(
        f"CREATE TABLE IF NOT EXISTS {table} ("
        "version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL)"
    )


async def _applied(conn: Any, table: str) -> dict[str, str]:
    rows = await conn.fetch(f"SELECT version, checksum FROM {_check_table(table)}")
    return {r["version"]: r["checksum"] for r in rows}


def _verify_applied(migrations: Sequence[Migration], applied: dict[str, str]) -> None:
    by_version = {m.version: m for m in migrations}
    for version, checksum in applied.items():
        current = by_version.get(version)
        if current is None:
            raise MigrationError(f"migration aplicada sem arquivo: {version}")
        if current.checksum != checksum:
            raise MigrationError(f"checksum alterado em migration ja aplicada: {version}")


async def apply_pending(
    conn: Any, directory: Path = DEFAULT_DIR, table: str = DEFAULT_TABLE
) -> list[str]:
    """Aplica as pendentes em ordem; devolve as versoes aplicadas agora."""
    migrations = discover(directory)
    await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_KEY)
    try:
        await _ensure_table(conn, table)
        applied = await _applied(conn, table)
        _verify_applied(migrations, applied)
        done: list[str] = []
        for mig in migrations:
            if mig.version in applied:
                continue
            async with conn.transaction():
                await conn.execute(mig.sql)
                await conn.execute(
                    f"INSERT INTO {table} (version, checksum, applied_at) VALUES ($1, $2, now())",
                    mig.version,
                    mig.checksum,
                )
            done.append(mig.version)
        return done
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_KEY)


async def _table_exists(conn: Any, table: str) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", _check_table(table)))


async def pending_versions(
    conn: Any, directory: Path = DEFAULT_DIR, table: str = DEFAULT_TABLE
) -> list[str]:
    """Versoes ainda nao aplicadas (somente leitura; recusa checksum alterado)."""
    migrations = discover(directory)
    applied = await _applied(conn, table) if await _table_exists(conn, table) else {}
    _verify_applied(migrations, applied)
    return [m.version for m in migrations if m.version not in applied]


async def is_up_to_date(
    conn: Any, directory: Path = DEFAULT_DIR, table: str = DEFAULT_TABLE
) -> bool:
    """Para o /health: True se nao falta migration e nenhum checksum divergiu."""
    try:
        return not await pending_versions(conn, directory, table)
    except MigrationError:
        return False


async def status_lines(
    conn: Any, directory: Path = DEFAULT_DIR, table: str = DEFAULT_TABLE
) -> list[str]:
    migrations = discover(directory)
    applied = await _applied(conn, table) if await _table_exists(conn, table) else {}
    lines = []
    for mig in migrations:
        if mig.version not in applied:
            lines.append(f"pendente  {mig.version}")
        elif applied[mig.version] != mig.checksum:
            lines.append(f"ALTERADA  {mig.version}")
        else:
            lines.append(f"aplicada  {mig.version}")
    return lines


async def _run(mode: str, dsn: str, directory: Path, table: str = DEFAULT_TABLE) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        if mode == "status":
            print("\n".join(await status_lines(conn, directory, table)))
            return EXIT_OK
        if mode == "check":
            pending = await pending_versions(conn, directory, table)
            for version in pending:
                print(f"pendente  {version}")
            return EXIT_PENDING if pending else EXIT_OK
        for version in await apply_pending(conn, directory, table):
            print(f"aplicada  {version}")
        return EXIT_OK
    finally:
        await conn.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db.migrate")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--status", action="store_true", help="lista aplicadas e pendentes")
    group.add_argument("--check", action="store_true", help="codigo 1 se faltar migration")
    args = parser.parse_args(argv)
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL nao definida no ambiente", file=sys.stderr)
        return EXIT_ERROR
    mode = "status" if args.status else "check" if args.check else "apply"
    try:
        return asyncio.run(_run(mode, dsn, DEFAULT_DIR))
    except (MigrationError, asyncpg.PostgresError, OSError) as exc:
        print(f"erro: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
