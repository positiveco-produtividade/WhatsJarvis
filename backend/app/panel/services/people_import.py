"""Leitura e validacao do CSV de pessoas (PN-33, PN-40; spec 4.8).

Fluxo: ``load_upload`` (le o .csv fora do loop de eventos, sem banco) -> ``create_preview``
(valida cada linha com ``users.validate_row`` de PF, separa duplicatas no arquivo e na base
por ``phone_key`` e grava o lote ``kind='people'`` em ``preview``) -> conferencia ->
``commit_import`` (politica do painel: grava SO as validas, cada uma por ``users.register``
dentro de um savepoint proprio, e reporta as demais; o lote perde as linhas ao terminar).

Situacoes: ``entra`` | ``fica_de_fora`` (telefone repetido no arquivo ou ja cadastrado) |
``erro`` (campo invalido ou faltando). As linhas do lote sao PII: so existem no lote
(apagado ao gravar, cancelar ou expirar) e no relatorio devolvido uma vez a quem gravou.
Nunca coloca nomes, telefones ou linhas em logs ou excecoes. Uma conexao so por vez.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import asyncpg

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import DuplicatePhone, InvalidValue
from app.panel.services import import_batches as ib
from app.panel.services.formatting import fold_text
from app.services import audit, users

__all__ = [
    "ENTRA",
    "ERRO",
    "FICA_DE_FORA",
    "HEADER",
    "MAX_BYTES",
    "MAX_ROWS",
    "SITUATION_LABELS",
    "ImportFileError",
    "ParsedUpload",
    "PeopleImportResult",
    "RowReport",
    "classify_rows",
    "commit_import",
    "create_preview",
    "load_upload",
    "parse_table",
]

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 500
HEADER: tuple[str, ...] = users.FIELD_ORDER  # nome,telefone,perfil,empresa,ufs,canais,marcas,gestor

ENTRA = "entra"
FICA_DE_FORA = "fica_de_fora"
ERRO = "erro"
SITUATION_LABELS: dict[str, str] = {ENTRA: "Entra", FICA_DE_FORA: "Fica de fora", ERRO: "Erro"}

_ACTOR_TYPE = "panel"
_REASON_REPEATED = "Telefone repetido no arquivo."
_REASON_EXISTS = "Telefone já cadastrado."
_REASON_RACE = "Telefone cadastrado enquanto você conferia."
_REASON_REVALIDATION = "Dados não passaram na validação final."
_MESSAGES: dict[str, str] = {
    "bad_format": "Envie um arquivo .csv.",
    "too_large": "O arquivo passa de 2 MB.",
    "unreadable": "Não consegui ler o arquivo. Confira se é um .csv válido, em UTF-8.",
    "bad_header": (
        "A primeira linha precisa ter as colunas nome, telefone, perfil, empresa, ufs, "
        "canais, marcas e gestor."
    ),
    "empty": "O arquivo não tem nenhuma pessoa.",
    "too_many_rows": f"O arquivo passa do limite de {MAX_ROWS} pessoas por vez.",
}


class ImportFileError(Exception):
    """Arquivo recusado (esperado, mostrado ao usuario). ``code`` identifica o motivo."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ParsedUpload:
    """Tabela lida: ``rows`` = (numero da linha no arquivo, ``{coluna: texto}``)."""

    sha256: str
    size_bytes: int
    rows: tuple[tuple[int, dict[str, str]], ...]


@dataclass(frozen=True, slots=True)
class RowReport:
    """Linha que NAO entrou, para o relatorio mostrado uma vez ao administrador."""

    linha: int
    nome: str
    situacao: str
    motivo: str


@dataclass(frozen=True, slots=True)
class PeopleImportResult:
    batch_id: str
    imported: int
    skipped: int
    invalid: int
    user_ids: tuple[int, ...]
    not_imported: tuple[RowReport, ...]


# ---------------------------------------------------------------- leitura


def _blank(row: Iterable[str]) -> bool:
    return not any(c.strip() for c in row)


def _delimiter(text: str) -> str:
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    return ";" if first.count(";") > first.count(",") else ","


def _read_csv(data: bytes) -> list[tuple[int, list[str]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError("unreadable") from exc
    reader = csv.reader(io.StringIO(text), delimiter=_delimiter(text))
    table: list[tuple[int, list[str]]] = []
    for raw in reader:
        row = [c.strip() for c in raw]
        if not _blank(row):
            table.append((reader.line_num, row))
    return table


def _header_index(header: Sequence[str]) -> dict[str, int]:
    folded = [" ".join(fold_text(h).split()) for h in header]
    index: dict[str, int] = {}
    for key in HEADER:
        if key not in folded:
            raise ImportFileError("bad_header")
        index[key] = folded.index(key)
    return index


def _to_upload(data: bytes, table: list[tuple[int, list[str]]]) -> ParsedUpload:
    if not table:
        raise ImportFileError("bad_header")
    index = _header_index(table[0][1])
    body = table[1:]
    if not body:
        raise ImportFileError("empty")
    if len(body) > MAX_ROWS:
        raise ImportFileError("too_many_rows")
    rows = tuple(
        (line, {k: (cells[i] if i < len(cells) else "") for k, i in index.items()})
        for line, cells in body
    )
    return ParsedUpload(hashlib.sha256(data).hexdigest(), len(data), rows)


def parse_table(data: bytes, filename: str) -> ParsedUpload:
    """Le o arquivo (sincrono, uso em thread). So a extensao do nome importa."""
    if len(data) > MAX_BYTES:
        raise ImportFileError("too_large")
    if not filename.lower().endswith(".csv"):
        raise ImportFileError("bad_format")
    try:
        table = _read_csv(data)
    except ImportFileError:
        raise
    except Exception as exc:  # noqa: BLE001 - csv levanta tipos variados
        raise ImportFileError("unreadable") from exc
    return _to_upload(data, table)


async def load_upload(data: bytes, filename: str) -> ParsedUpload:
    """Le e valida cabecalho, tamanho e limite de linhas fora do loop de eventos."""
    if len(data) > MAX_BYTES:
        raise ImportFileError("too_large")
    return await asyncio.to_thread(parse_table, data, filename)


# ---------------------------------------------------------------- classificacao


def _error_text(errors: Sequence[users.FieldError]) -> str:
    return " ".join(f"{e.field}: {e.reason}." for e in errors)


def _valid_entry(line: int, v: users.ValidatedUser) -> dict[str, Any]:
    return {
        "linha": line,
        "nome": v.name,
        "telefone": v.phone_e164,
        "perfil": v.profile,
        "empresa": v.company,
        "ufs": list(v.ufs),
        "canais": list(v.channels),
        "marcas": list(v.brands),
        "gestor": v.manager_name,
        "situacao": ENTRA,
        "motivo": "",
        "campos_com_erro": [],
    }


def _invalid_entry(
    line: int, raw: Mapping[str, str], errors: Sequence[users.FieldError]
) -> dict[str, Any]:
    entry: dict[str, Any] = {"linha": line, **{k: raw.get(k, "") for k in HEADER}}
    entry.update(
        situacao=ERRO, motivo=_error_text(errors), campos_com_erro=[e.field for e in errors]
    )
    return entry


def classify_rows(
    upload: ParsedUpload, *, existing_keys: Iterable[str], clock: Clock | None = None
) -> list[dict[str, Any]]:
    """Classifica todas as linhas (pura). ``existing_keys``: ``phone_key`` ja cadastrados."""
    now = (clock or SystemClock()).now()
    existing = set(existing_keys)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for line, raw in upload.rows:
        result = users.validate_row(raw, now=now)
        if isinstance(result, list):
            out.append(_invalid_entry(line, raw, result))
            continue
        entry = _valid_entry(line, result)
        if result.phone_key in existing:
            entry.update(situacao=FICA_DE_FORA, motivo=_REASON_EXISTS)
        elif result.phone_key in seen:
            entry.update(situacao=FICA_DE_FORA, motivo=_REASON_REPEATED)
        else:
            seen.add(result.phone_key)
        out.append(entry)
    return out


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {
        s: sum(1 for r in rows if r["situacao"] == s) for s in SITUATION_LABELS
    }
    counts["total"] = len(rows)
    by_field: dict[str, int] = {}
    for row in rows:
        for field in row["campos_com_erro"]:
            by_field[field] = by_field.get(field, 0) + 1
    counts["erros_por_campo"] = by_field
    return counts


async def _existing_keys(
    conn: asyncpg.Connection, upload: ParsedUpload, clock: Clock | None
) -> set[str]:
    """``phone_key`` do arquivo que ja estao na base (qualquer status)."""
    now = (clock or SystemClock()).now()
    keys: set[str] = set()
    for _, raw in upload.rows:
        result = users.validate_row(raw, now=now)
        if not isinstance(result, list):
            keys.add(result.phone_key)
    if not keys:
        return set()
    found = await conn.fetch(
        "SELECT phone_key FROM access.users WHERE phone_key = ANY($1::text[])", sorted(keys)
    )
    return {r["phone_key"] for r in found}


async def create_preview(
    conn: asyncpg.Connection,
    upload: ParsedUpload,
    *,
    filename: str,
    actor: str,
    clock: Clock | None = None,
) -> ib.BatchRecord:
    """Classifica contra a base atual e grava o lote ``people`` em ``preview`` (sem cadastrar)."""
    existing = await _existing_keys(conn, upload, clock)
    rows = classify_rows(upload, existing_keys=existing, clock=clock)
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or "pessoas.csv"
    return await ib.create_batch(
        conn,
        kind="people",
        actor=actor,
        filename=safe_name,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        rows=rows,
        summary=_summary(rows),
        clock=clock,
    )


# ---------------------------------------------------------------- gravacao


async def _register_row(
    conn: asyncpg.Connection,
    row: dict[str, Any],
    *,
    actor: str,
    settings: Settings,
    clock: Clock | None,
) -> tuple[int | None, str]:
    """Cadastra uma linha num savepoint proprio. Devolve (user_id, motivo se nao entrou)."""
    now = (clock or SystemClock()).now()
    data = users.validate_row({k: row[k] for k in HEADER}, now=now)
    if isinstance(data, list):
        return None, _REASON_REVALIDATION
    try:
        async with conn.transaction():
            user = await users.register(
                conn, data, actor=actor, actor_type=_ACTOR_TYPE, settings=settings, clock=clock
            )
    except DuplicatePhone:
        return None, _REASON_RACE
    except InvalidValue:
        return None, _REASON_REVALIDATION
    return user.id, ""


def _report(row: dict[str, Any], situacao: str, motivo: str) -> RowReport:
    return RowReport(int(row["linha"]), str(row["nome"]), situacao, motivo)


async def commit_import(
    conn: asyncpg.Connection,
    batch_id: Any,
    *,
    actor: str,
    settings: Settings,
    clock: Clock | None = None,
) -> PeopleImportResult | None:
    """Grava so as validas; ``None`` se o lote ja foi gravado/cancelado/expirado.

    Reivindica o lote (que apaga a PII) na mesma transacao; cada linha que entra e
    revalidada e cadastrada num savepoint (um ``DuplicatePhone`` nao aborta o lote). Falha
    inesperada desfaz tudo e o lote segue em ``preview`` com as linhas intactas.
    """
    async with conn.transaction():
        batch = await ib.claim_commit(conn, batch_id, clock=clock)
        if batch is None:
            return None
        if batch.kind != "people":
            raise ValueError("lote de outro tipo")
        ids: list[int] = []
        left: list[RowReport] = []
        for row in batch.rows:
            if row["situacao"] != ENTRA:
                left.append(_report(row, row["situacao"], row["motivo"]))
                continue
            uid, why = await _register_row(conn, row, actor=actor, settings=settings, clock=clock)
            if uid is None:
                left.append(_report(row, FICA_DE_FORA, why))
            else:
                ids.append(uid)
        invalid = sum(1 for r in left if r.situacao == ERRO)
        result = PeopleImportResult(
            batch_id=str(batch.id),
            imported=len(ids),
            skipped=len(left) - invalid,
            invalid=invalid,
            user_ids=tuple(ids),
            not_imported=tuple(left),
        )
        await audit.write(
            conn, _ACTOR_TYPE, actor, "people.import", "import_batch", str(batch.id), None,
            {"imported": result.imported, "skipped": result.skipped, "invalid": result.invalid},
            clock=clock,
        )  # fmt: skip
        return result
