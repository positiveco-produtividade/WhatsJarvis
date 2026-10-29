"""CLI administrativo (spec 4.3.3): ``python -m app.cli``, fino sobre a camada de servico.

Parte 1: ``user add|import|revoke|list`` e ``flag get|set``; parte 2: ``user resend`` e
``user swap-number``. O CLI NAO envia nada: enfileira (via ``users.register``/``resend``) e
consulta. Saidas sem PII alem do telefone mascarado (``***1234``).
``run`` e assincrona e recebe pool/settings por injecao (testes); ``main`` monta tudo a partir
de ``Settings`` (``backend/.env``) e roda com ``asyncio.run``.
Codigos de saida: 0 ok; 1 falha de execucao (nao encontrado, duplicado, envio falhou);
2 entrada invalida (campo faltando/invalido, CSV com erro, uso incorreto).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import getpass
import io
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import DuplicatePhone, InvalidValue, JarvisError, ValidationFailure
from app.core.values import BRANDS, REVOKE_REASONS
from app.db.connection import create_pool, transaction
from app.panel.services import faq_seed
from app.services import flags as flags_module
from app.services import users
from app.services.flags import Flags
from app.services.phone import InvalidPhoneError, mask_phone, normalize_phone

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_INVALID = 2
WELCOME_WAIT_SECONDS = 20.0
_POLL_SECONDS = 0.5
_STATUSES = ("pending_optin", "active", "declined", "revoked")
_FLAG_TARGETS = ("team-group", "paused", "brand")
_ON_OFF = {"on": True, "off": False}
_ERR_MSG_LIMIT = 200
DEFAULT_SEED_FILE = Path(__file__).resolve().parents[2] / "piloto" / "guia.json"


class _Rollback(Exception):
    """Interna: desfaz a transacao do lote (erro encontrado ou --dry-run)."""


@dataclass(frozen=True)
class Context:
    pool: Any
    settings: Settings
    clock: Clock
    wait_seconds: float


def _out(text: str = "") -> None:
    print(text)


def _err(text: str) -> None:
    print(text, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli", description="Administracao do Jarvis"
    )
    top = parser.add_subparsers(dest="group", required=True)

    user = top.add_parser("user", help="pessoas autorizadas").add_subparsers(
        dest="command", required=True
    )
    add = user.add_parser("add", help="cadastra uma pessoa (8 campos obrigatorios)")
    for opt in ("name", "phone", "profile", "company", "ufs", "channels", "brands", "manager"):
        add.add_argument(f"--{opt}", default=None)
    add.add_argument("--by", default=None)

    imp = user.add_parser("import", help="importa CSV (tudo ou nada)")
    imp.add_argument("--file", required=True)
    imp.add_argument("--dry-run", action="store_true")
    imp.add_argument("--by", default=None)

    rev = user.add_parser("revoke", help="revoga o acesso")
    who = rev.add_mutually_exclusive_group(required=True)
    who.add_argument("--phone")
    who.add_argument("--id", type=int)
    rev.add_argument("--reason", required=True, choices=REVOKE_REASONS)
    rev.add_argument("--by", default=None)

    res = user.add_parser("resend", help="reenvia boas-vindas / reativa (nao vale para active)")
    res_who = res.add_mutually_exclusive_group(required=True)
    res_who.add_argument("--phone")
    res_who.add_argument("--id", type=int)
    res.add_argument("--by", default=None)

    swap = user.add_parser("swap-number", help="troca o numero (revoga A e cria B)")
    swap.add_argument("--from", dest="from_phone", required=True)
    swap.add_argument("--to", dest="to_phone", required=True)
    swap.add_argument("--by", default=None)

    lst = user.add_parser("list", help="lista pessoas (telefone mascarado)")
    lst.add_argument("--status", choices=_STATUSES)

    flag = top.add_parser("flag", help="chaves operacionais").add_subparsers(
        dest="command", required=True
    )
    get = flag.add_parser("get", help="le uma chave")
    get.add_argument("target", choices=_FLAG_TARGETS)
    get.add_argument("brand", nargs="?", choices=BRANDS)
    setp = flag.add_parser("set", help="grava uma chave")
    setp.add_argument("target", choices=_FLAG_TARGETS)
    setp.add_argument("rest", nargs="+", help="[marca] on|off")
    setp.add_argument("--by", default=None)

    faq = top.add_parser("faq", help="FAQ do painel").add_subparsers(dest="command", required=True)
    seed = faq.add_parser("seed", help="carga inicial das perguntas (idempotente)")
    seed.add_argument("--file", default=str(DEFAULT_SEED_FILE))
    seed.add_argument("--by", default=None)
    return parser


def _actor(by: str | None) -> str:
    return by or getpass.getuser()


def _format_errors(errors: Sequence[users.FieldError]) -> str:
    return "; ".join(f"{e.field}: {e.reason}" for e in errors)


# --------------------------------------------------------------------------- user add


async def _welcome_result(ctx: Context, user_id: int) -> str:
    """Espera ate ``wait_seconds`` o resultado do envio da boas-vindas (so consulta)."""
    waited = 0.0
    while True:
        row = await ctx.pool.fetchrow(
            "SELECT status, error_code, discard_reason FROM chat.outbound_messages "
            "WHERE user_id = $1 AND kind = 'welcome_template' ORDER BY id DESC LIMIT 1",
            user_id,
        )
        status = row["status"] if row else None
        if status == "sent":
            return "enviado"
        if status == "failed":
            return f"falhou: {str(row['error_code'] or 'erro no envio')[:_ERR_MSG_LIMIT]}"
        if status == "discarded":
            return f"falhou: descartada ({row['discard_reason'] or 'sem motivo'})"
        if waited >= ctx.wait_seconds:
            return "na fila"
        await asyncio.sleep(_POLL_SECONDS)
        waited += _POLL_SECONDS


async def _report_welcome(ctx: Context, user_id: int) -> int:
    outcome = await _welcome_result(ctx, user_id)
    _out(f"boas-vindas: {outcome}")
    return EXIT_FAIL if outcome.startswith("falhou") else EXIT_OK


async def _cmd_user_add(ctx: Context, args: argparse.Namespace) -> int:
    row = {
        users.KEY_NAME: args.name,
        users.KEY_PHONE: args.phone,
        users.KEY_PROFILE: args.profile,
        users.KEY_COMPANY: args.company,
        users.KEY_UFS: args.ufs,
        users.KEY_CHANNELS: args.channels,
        users.KEY_BRANDS: args.brands,
        users.KEY_MANAGER: args.manager,
    }
    result = users.validate_row(row, now=ctx.clock.now())
    if isinstance(result, list):
        _err("erro: cadastro rejeitado, nada foi criado")
        for e in result:
            _err(f"  campo {e.field}: {e.reason}")
        return EXIT_INVALID
    actor = _actor(args.by)
    try:
        async with ctx.pool.acquire() as conn, transaction(conn):
            user = await users.register(
                conn, result, actor=actor, settings=ctx.settings, clock=ctx.clock
            )
    except DuplicatePhone:
        _err("erro: telefone ja cadastrado")
        return EXIT_FAIL
    _out(f"cadastrado: id={user.id} telefone={mask_phone(user.phone_e164)} status={user.status}")
    return await _report_welcome(ctx, user.id)


# ------------------------------------------------------------------------- user import


def _read_csv(path: Path) -> tuple[list[tuple[int, dict[str, str]]], str | None]:
    """Linhas (numero da linha no arquivo, valores); ou mensagem de erro de formato."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return [], "nao foi possivel ler o arquivo como UTF-8"
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    header = [h.strip().lower() for h in (reader.fieldnames or [])]
    missing = [k for k in users.FIELD_ORDER if k not in header]
    if missing:
        return [], f"cabecalho sem coluna(s): {', '.join(missing)}"
    rows: list[tuple[int, dict[str, str]]] = []
    for line_no, raw in enumerate(reader, start=2):
        values = {(k or "").strip().lower(): (v or "") for k, v in raw.items() if k is not None}
        if any(v.strip() for v in values.values()):
            rows.append((line_no, values))
    return rows, None


def _validate_rows(
    rows: Sequence[tuple[int, Mapping[str, str]]], ctx: Context
) -> tuple[list[tuple[int, users.ValidatedUser]], list[str]]:
    valid: list[tuple[int, users.ValidatedUser]] = []
    problems: list[str] = []
    for line_no, values in rows:
        result = users.validate_row(values, now=ctx.clock.now())
        if isinstance(result, list):
            problems.extend(f"linha {line_no}, campo {e.field}: {e.reason}" for e in result)
        else:
            valid.append((line_no, result))
    return valid, problems


async def _register_all(
    ctx: Context, valid: Sequence[tuple[int, users.ValidatedUser]], actor: str, dry_run: bool
) -> list[str]:
    """Uma transacao com ``register`` por linha; desfaz tudo se houver erro ou dry-run."""
    problems: list[str] = []
    try:
        async with ctx.pool.acquire() as conn, transaction(conn):
            for line_no, data in valid:
                try:
                    await users.register(
                        conn, data, actor=actor, settings=ctx.settings, clock=ctx.clock
                    )
                except DuplicatePhone:
                    problems.append(f"linha {line_no}, campo telefone: ja cadastrado")
            if problems or dry_run:
                raise _Rollback
    except _Rollback:
        pass
    return problems


async def _cmd_user_import(ctx: Context, args: argparse.Namespace) -> int:
    rows, format_error = _read_csv(Path(args.file))
    if format_error:
        _err(f"erro: {format_error}")
        return EXIT_INVALID
    if not rows:
        _err("erro: arquivo sem linhas de dados")
        return EXIT_INVALID
    valid, problems = _validate_rows(rows, ctx)
    if not problems:
        problems = await _register_all(ctx, valid, _actor(args.by), args.dry_run)
    if problems:
        _err("erro: importacao rejeitada, nada foi gravado")
        for p in problems:
            _err(f"  {p}")
        return EXIT_INVALID
    if args.dry_run:
        _out(f"dry-run: {len(valid)} linha(s) validas, nada foi gravado")
    else:
        _out(f"importadas: {len(valid)} pessoa(s)")
    return EXIT_OK


# ------------------------------------------------------------------ user revoke / list


async def _resolve_user_id(ctx: Context, args: argparse.Namespace) -> int | None:
    if args.id is not None:
        found = await ctx.pool.fetchval("SELECT id FROM access.users WHERE id = $1", args.id)
    else:
        try:
            key = normalize_phone(args.phone).match_key
        except InvalidPhoneError as exc:
            _err(f"erro: campo telefone: {exc}")
            return None
        found = await ctx.pool.fetchval("SELECT id FROM access.users WHERE phone_key = $1", key)
    return int(found) if found is not None else None


async def _cmd_user_revoke(ctx: Context, args: argparse.Namespace) -> int:
    user_id = await _resolve_user_id(ctx, args)
    if user_id is None:
        _err("erro: pessoa nao encontrada")
        return EXIT_FAIL
    async with ctx.pool.acquire() as conn, transaction(conn):
        user = await users.revoke(
            conn, user_id, args.reason, _actor(args.by), clock=ctx.clock
        )
    _out(f"revogado: id={user.id} telefone={mask_phone(user.phone_e164)} status={user.status}")
    return EXIT_OK


async def _cmd_user_resend(ctx: Context, args: argparse.Namespace) -> int:
    user_id = await _resolve_user_id(ctx, args)
    if user_id is None:
        _err("erro: pessoa nao encontrada")
        return EXIT_FAIL
    try:
        async with ctx.pool.acquire() as conn, transaction(conn):
            user = await users.resend(
                conn, user_id, _actor(args.by), settings=ctx.settings, clock=ctx.clock
            )
    except users.UserAlreadyActive:
        _err("erro: pessoa ja esta ativa; nao ha o que reenviar")
        return EXIT_FAIL
    _out(f"reenviado: id={user.id} telefone={mask_phone(user.phone_e164)} status={user.status}")
    return await _report_welcome(ctx, user.id)


async def _cmd_user_swap_number(ctx: Context, args: argparse.Namespace) -> int:
    try:
        async with ctx.pool.acquire() as conn, transaction(conn):
            revoked, created = await users.swap_number(
                conn, args.from_phone, args.to_phone, _actor(args.by),
                settings=ctx.settings, clock=ctx.clock,
            )  # fmt: skip
    except DuplicatePhone:
        _err("erro: telefone de destino ja cadastrado; nada foi alterado")
        return EXIT_FAIL
    except InvalidValue as exc:
        _err(f"erro: {exc}; nada foi alterado")
        return EXIT_FAIL if exc.field == "from" else EXIT_INVALID
    _out(
        f"trocado: origem id={revoked.id} telefone={mask_phone(revoked.phone_e164)} "
        f"status={revoked.status}"
    )
    _out(
        f"destino id={created.id} telefone={mask_phone(created.phone_e164)} "
        f"status={created.status}"
    )
    return await _report_welcome(ctx, created.id)


async def _cmd_user_list(ctx: Context, args: argparse.Namespace) -> int:
    sql = "SELECT id, phone_e164, profile, status FROM access.users"
    params: list[str] = []
    if args.status:
        sql += " WHERE status = $1"
        params.append(args.status)
    records = await ctx.pool.fetch(sql + " ORDER BY id", *params)
    for r in records:
        _out(f"{r['id']}\t{mask_phone(r['phone_e164'])}\t{r['profile']}\t{r['status']}")
    _out(f"total: {len(records)}")
    return EXIT_OK


# ------------------------------------------------------------------------------- flag


def _flag_key(target: str, brand: str | None) -> str | None:
    if target == "team-group":
        return flags_module.TEAM_GROUP_KEY
    if target == "paused":
        return flags_module.PAUSED_KEY
    return flags_module.brand_key(brand) if brand else None


def _parse_set_args(args: argparse.Namespace) -> tuple[str, bool] | None:
    """Devolve (chave, valor) de ``flag set`` ou None se o uso estiver errado."""
    rest: list[str] = list(args.rest)
    brand = rest.pop(0) if args.target == "brand" and len(rest) == 2 else None
    if len(rest) != 1 or rest[0] not in _ON_OFF:
        return None
    if args.target == "brand" and brand not in BRANDS:
        return None
    key = _flag_key(args.target, brand)
    return (key, _ON_OFF[rest[0]]) if key else None


async def _cmd_flag(ctx: Context, args: argparse.Namespace) -> int:
    flags = Flags(ctx.pool, clock=ctx.clock, team_group_initial=ctx.settings.team_group_enabled)
    if args.command == "get":
        key = _flag_key(args.target, args.brand)
        if key is None:
            _err("erro: informe a marca (flag get brand <marca>)")
            return EXIT_INVALID
        value = await flags.get_bool(key, _flag_default(key, ctx.settings))
        _out(f"{key}: {'on' if value else 'off'}")
        return EXIT_OK
    parsed = _parse_set_args(args)
    if parsed is None:
        _err("erro: uso: flag set team-group|paused on|off  ou  flag set brand <marca> on|off")
        return EXIT_INVALID
    key, value = parsed
    async with ctx.pool.acquire() as conn, transaction(conn):
        await flags.set_bool(conn, key, value, _actor(args.by), actor_type="cli")
    _out(f"{key}: {'on' if value else 'off'}")
    return EXIT_OK


def _flag_default(key: str, settings: Settings) -> bool:
    if key == flags_module.TEAM_GROUP_KEY:
        return settings.team_group_enabled
    return flags_module.default_for(key)


# -------------------------------------------------------------------------------- faq


async def _seed_in_transaction(
    ctx: Context, items: Sequence[faq_seed.SeedItem], actor: str
) -> faq_seed.SeedResult:
    """Uma transacao; desfaz tudo se algum item for recusado."""
    result = faq_seed.SeedResult()
    try:
        async with ctx.pool.acquire() as conn, transaction(conn):
            result = await faq_seed.seed(conn, items, actor=actor, clock=ctx.clock)
            if not result.ok:
                raise _Rollback
    except _Rollback:
        pass
    return result


async def _cmd_faq_seed(ctx: Context, args: argparse.Namespace) -> int:
    try:
        items = faq_seed.load_items(Path(args.file))
    except faq_seed.SeedFileError as exc:
        _err(f"erro: {exc}")
        return EXIT_INVALID
    result = await _seed_in_transaction(ctx, items, _actor(args.by))
    if not result.ok:
        positions = ", ".join(str(n) for n in result.failed)
        _err(f"erro: itens recusados na validacao (posicoes {positions}); nada foi gravado")
        return EXIT_FAIL
    _out(
        f"faq seed: total={result.total} inseridas={result.inserted} "
        f"ja existiam={result.skipped} sem resposta={result.without_answer}"
    )
    return EXIT_OK


# ----------------------------------------------------------------------------- entrada

_HANDLERS = {
    ("user", "add"): _cmd_user_add,
    ("user", "import"): _cmd_user_import,
    ("user", "revoke"): _cmd_user_revoke,
    ("user", "resend"): _cmd_user_resend,
    ("user", "swap-number"): _cmd_user_swap_number,
    ("user", "list"): _cmd_user_list,
    ("flag", "get"): _cmd_flag,
    ("flag", "set"): _cmd_flag,
    ("faq", "seed"): _cmd_faq_seed,
}


async def run(
    argv: Sequence[str],
    *,
    pool: Any,
    settings: Settings,
    clock: Clock | None = None,
    wait_seconds: float = WELCOME_WAIT_SECONDS,
) -> int:
    """Executa um comando e devolve o codigo de saida. Argumentos invalidos: SystemExit(2)."""
    args = build_parser().parse_args(list(argv))
    ctx = Context(pool, settings, clock or SystemClock(), wait_seconds)
    handler = _HANDLERS[(args.group, args.command)]
    try:
        return await handler(ctx, args)
    except ValidationFailure as exc:
        _err(f"erro: {exc}")
        return EXIT_INVALID
    except JarvisError as exc:
        _err(f"erro: {exc}")
        return EXIT_FAIL


async def _run_with_pool(argv: Sequence[str], settings: Settings) -> int:
    pool = await create_pool(settings.database_url.get_secret_value(), min_size=1, max_size=2)
    try:
        return await run(argv, pool=pool, settings=settings)
    finally:
        await pool.close()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    build_parser().parse_args(args)  # --help e erros de uso antes de tocar em configuracao
    try:
        settings = Settings()  # type: ignore[call-arg]
    except ValueError:
        _err("erro: configuracao invalida ou ausente (backend/.env)")
        return EXIT_FAIL
    return asyncio.run(_run_with_pool(args, settings))


if __name__ == "__main__":
    sys.exit(main())
