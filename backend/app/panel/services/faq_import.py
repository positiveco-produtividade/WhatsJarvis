"""Leitura e classificacao da planilha da FAQ (PN-27, PN-28, PN-29; spec 4.7).

Fluxo: ``load_upload`` (le .xlsx/.csv em ``asyncio.to_thread``, sem banco) ->
``create_preview`` (classifica cada linha e grava o lote em ``preview``) -> conferencia ->
``commit_import`` (uma transacao: reivindica o lote, revalida contra a FAQ atual, insere as
que entram, versoes e auditoria; qualquer falha reverte tudo e o lote segue em ``preview``).

Situacoes: ``entra`` | ``fica_de_fora`` (ja existe ou repetida na planilha) | ``erro``
(regras de ``faq_validation``). Nunca coloca texto de linhas em logs ou excecoes.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import asyncpg
from openpyxl import Workbook, load_workbook  # type: ignore[import-untyped]

from app.core.clock import Clock
from app.panel.services import faq_admin
from app.panel.services import faq_validation as fv
from app.panel.services import import_batches as ib
from app.panel.services.formatting import fold_text
from app.panel.values import BRANDS, CLASSIFICATION_LABELS
from app.services import audit

__all__ = [
    "ENTRA",
    "ERRO",
    "FICA_DE_FORA",
    "MAX_BYTES",
    "MAX_ROWS",
    "SITUATION_LABELS",
    "TEMPLATE_FILENAME",
    "ImportCommitError",
    "ImportFileError",
    "ImportResult",
    "ParsedUpload",
    "classify_rows",
    "commit_import",
    "create_preview",
    "load_upload",
    "parse_table",
    "template_xlsx",
]

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 200
TEMPLATE_FILENAME = "faq-modelo-jarvis.xlsx"

ENTRA = "entra"
FICA_DE_FORA = "fica_de_fora"
ERRO = "erro"
SITUATION_LABELS: dict[str, str] = {ENTRA: "Entra", FICA_DE_FORA: "Fica de fora", ERRO: "Erro"}

DEFAULT_CATEGORY = "PRODUTO"
_ACTOR_TYPE = "panel"
_NO_WORDS = frozenset({"nao", "n", "false", "0", "no"})
_XLSX_MAGIC = b"PK"
_TEMPLATE_HEADER = ("pergunta", "resposta", "categoria", "bot responde")
_TEMPLATE_ROWS = (
    ("Jungle tem açúcar?", "Low Carb e Electrolytes usam stevia.", "Produto", "sim"),
    ("Ajuda a emagrecer?", None, "Produto", "não"),
)
_COL_NAMES: dict[str, tuple[str, ...]] = {
    "question": ("pergunta",),
    "answer": ("resposta",),
    "category": ("categoria", "assunto"),
    "bot": ("bot responde", "bot"),
}
_HEADER_MSG = (
    "A planilha precisa das colunas pergunta e resposta na primeira linha. Baixe a planilha modelo."
)
_MESSAGES: dict[str, str] = {
    "bad_format": "Envie um arquivo .xlsx ou .csv.",
    "too_large": "O arquivo passa de 2 MB.",
    "unreadable": "Não consegui ler a planilha. Confira se é um .xlsx ou .csv válido.",
    "bad_header": _HEADER_MSG,
    "empty": "A planilha não tem nenhuma pergunta.",
    "too_many_rows": f"A planilha passa do limite de {MAX_ROWS} perguntas por vez.",
    "bad_brand": "Marca inválida.",
}
_REASONS: dict[str, str] = {
    fv.QUESTION_EMPTY: "Pergunta vazia.",
    fv.QUESTION_TOO_LONG: "Pergunta com mais de 500 caracteres.",
    fv.CONTACT_PHONE: "Texto com telefone.",
    fv.CONTACT_EMAIL: "Texto com e-mail.",
    fv.CONTACT_LINK: "Texto com link.",
    fv.ANSWER_REQUIRED: "Resposta obrigatória.",
}
_REASON_EXISTS = "Já existe na FAQ."
_REASON_REPEATED = "Repetida na própria planilha."


class ImportFileError(Exception):
    """Arquivo recusado (esperado, mostrado ao usuario). ``code`` identifica o motivo."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(code)


class ImportCommitError(Exception):
    """Falha ao gravar: a transacao inteira e desfeita. Sem texto de linhas."""

    def __init__(self, code: str, line: int | None = None) -> None:
        self.code = code
        self.line = line
        super().__init__(code if line is None else f"{code}@{line}")


@dataclass(frozen=True, slots=True)
class ParsedUpload:
    """Tabela lida: ``header`` normalizado e linhas de dados (numero da linha na planilha)."""

    sha256: str
    size_bytes: int
    column_index: dict[str, int]
    rows: tuple[tuple[int, tuple[str, ...]], ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: str
    imported: int
    skipped: int
    item_ids: tuple[int, ...]


# ---------------------------------------------------------------- leitura


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _blank(row: Iterable[str]) -> bool:
    return not any(c.strip() for c in row)


def _read_xlsx(data: bytes) -> list[list[str]]:
    if not data.startswith(_XLSX_MAGIC):
        raise ImportFileError("unreadable")
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheet = wb.worksheets[0]
        rows: list[list[str]] = []
        for raw in sheet.iter_rows(values_only=True):
            row = [_cell(v) for v in raw]
            if _blank(row):
                continue
            rows.append(row)
            if len(rows) > MAX_ROWS + 1:  # cabecalho + limite + 1: ja estourou
                break
        return rows
    finally:
        wb.close()


def _read_csv(data: bytes) -> list[list[str]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ImportFileError("unreadable") from exc
    first = next((ln for ln in text.splitlines() if ln.strip()), "")
    delimiter = ";" if first.count(";") >= first.count(",") else ","
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(text), delimiter=delimiter)]
    return [r for r in rows if not _blank(r)]


def parse_table(data: bytes, filename: str) -> ParsedUpload:
    """Le o arquivo (sincrono, uso em thread). So a extensao do nome importa."""
    if len(data) > MAX_BYTES:
        raise ImportFileError("too_large")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "xlsx":
            table = _read_xlsx(data)
        elif ext == "csv":
            table = _read_csv(data)
        else:
            raise ImportFileError("bad_format")
    except (ImportFileError, MemoryError):
        raise
    except Exception as exc:  # noqa: BLE001 - openpyxl/zip/csv levantam tipos variados
        raise ImportFileError("unreadable") from exc
    return _to_upload(data, table)


def _find_columns(header: list[str]) -> dict[str, int]:
    folded = [" ".join(fold_text(h).split()) for h in header]
    found: dict[str, int] = {}
    for key, names in _COL_NAMES.items():
        idx = next((i for i, h in enumerate(folded) if h in names), -1)
        if idx >= 0:
            found[key] = idx
    return found


def _to_upload(data: bytes, table: list[list[str]]) -> ParsedUpload:
    if not table:
        raise ImportFileError("bad_header")
    columns = _find_columns(table[0])
    if "question" not in columns or "answer" not in columns:
        raise ImportFileError("bad_header")
    body = table[1:]
    if not body:
        raise ImportFileError("empty")
    if len(body) > MAX_ROWS:
        raise ImportFileError("too_many_rows")
    rows = tuple((i + 2, tuple(r)) for i, r in enumerate(body))
    return ParsedUpload(
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        column_index=columns,
        rows=rows,
    )


async def load_upload(data: bytes, filename: str) -> ParsedUpload:
    """Le e valida cabecalho, tamanho e limite de linhas fora do loop de eventos."""
    if len(data) > MAX_BYTES:
        raise ImportFileError("too_large")
    return await asyncio.to_thread(parse_table, data, filename)


# ---------------------------------------------------------------- classificacao


def _category_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key in fv.FAQ_CATEGORIES:
        lookup[fold_text(key)] = key
        lookup[fold_text(CLASSIFICATION_LABELS[key])] = key
    return lookup


def _field(row: tuple[str, ...], index: int | None) -> str:
    return row[index] if index is not None and index < len(row) else ""


def _bot_flag(raw: str, answer: str) -> bool:
    if not answer:
        return False
    return fold_text(raw).strip() not in _NO_WORDS if raw.strip() else True


def _reason(problems: Iterable[fv.FaqProblem]) -> str:
    return " ".join(_REASONS.get(p.code, p.message) for p in problems)


def _classify_one(
    line: int,
    raw: tuple[str, ...],
    cols: Mapping[str, int],
    brand: str,
    existing: set[str],
    seen: set[str],
) -> dict[str, Any]:
    question = _field(raw, cols.get("question"))
    answer = _field(raw, cols.get("answer"))
    cat_raw = _field(raw, cols.get("category"))
    category = _category_lookup().get(fold_text(cat_raw))
    bot = _bot_flag(_field(raw, cols.get("bot")), answer)
    result = fv.validate_faq(
        brand=brand,
        category=category or DEFAULT_CATEGORY,
        question=question,
        answer=answer,
        variations=(),
        bot_responde=bot,
        active_keys=existing,
    )
    real = [p for p in result.problems if p.code != fv.DUPLICATE_QUESTION]
    situacao, motivo = ENTRA, ""
    if real:
        situacao, motivo = ERRO, _reason(real)
    elif result.problems:
        situacao, motivo = FICA_DE_FORA, _REASON_EXISTS
    elif result.question_key in seen:
        situacao, motivo = FICA_DE_FORA, _REASON_REPEATED
    if situacao == ENTRA:
        seen.add(result.question_key)
    return {
        "linha": line,
        "pergunta": result.question or question.strip(),
        "resposta": result.answer or "",
        "categoria": category or DEFAULT_CATEGORY,
        "conferir_categoria": category is None,
        "bot_responde": bot,
        "situacao": situacao,
        "motivo": motivo,
    }


def classify_rows(
    upload: ParsedUpload, *, brand: str, existing_keys: Iterable[str]
) -> list[dict[str, Any]]:
    """Classifica todas as linhas (funcao pura). ``existing_keys``: ativas da marca e de 'todas'."""
    existing = set(existing_keys)
    seen: set[str] = set()
    return [
        _classify_one(line, raw, upload.column_index, brand, existing, seen)
        for line, raw in upload.rows
    ]


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {s: sum(1 for r in rows if r["situacao"] == s) for s in SITUATION_LABELS}
    counts["total"] = len(rows)
    counts["conferir"] = sum(1 for r in rows if r["situacao"] == ENTRA and r["conferir_categoria"])
    return counts


async def _existing_keys(conn: asyncpg.Connection, brand: str) -> list[str]:
    rows = await conn.fetch(
        "SELECT question_key FROM content.faq_items WHERE active AND brand = ANY($1::text[])",
        sorted({brand, fv.ALL_BRANDS_KEY}),
    )
    return [r["question_key"] for r in rows]


def _check_brand(brand: str) -> None:
    if brand != fv.ALL_BRANDS_KEY and brand not in BRANDS:
        raise ImportFileError("bad_brand")


async def create_preview(
    conn: asyncpg.Connection,
    upload: ParsedUpload,
    *,
    filename: str,
    brand: str,
    actor: str,
    clock: Clock | None = None,
) -> ib.BatchRecord:
    """Classifica contra a FAQ atual e grava o lote em ``preview`` (nada vai a ``faq_items``)."""
    _check_brand(brand)
    rows = classify_rows(upload, brand=brand, existing_keys=await _existing_keys(conn, brand))
    safe_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:200] or "planilha"
    return await ib.create_batch(
        conn,
        kind="faq",
        actor=actor,
        filename=safe_name,
        sha256=upload.sha256,
        size_bytes=upload.size_bytes,
        rows=rows,
        summary=_summary(rows),
        brand=brand,
        clock=clock,
    )


# ---------------------------------------------------------------- gravacao


def _apply_categories(
    rows: list[dict[str, Any]], categories: Mapping[int, str] | None
) -> list[dict[str, Any]]:
    chosen = dict(categories or {})
    out = [dict(r) for r in rows]
    for row in out:
        pick = chosen.pop(row["linha"], None)
        if pick is None:
            continue
        if pick not in fv.FAQ_CATEGORIES:
            raise ImportCommitError("bad_category", row["linha"])
        row["categoria"] = pick
    if chosen:
        raise ImportCommitError("unknown_line")
    return out


async def _insert_row(
    conn: asyncpg.Connection,
    row: dict[str, Any],
    *,
    brand: str,
    batch_id: str,
    actor: str,
    clock: Clock | None,
) -> int | None:
    """Insere uma linha. ``None`` = virou duplicata desde a conferencia (fica de fora)."""
    data = faq_admin.FaqInput(
        brand=brand,
        category=row["categoria"],
        question=row["pergunta"],
        answer=row["resposta"] or None,
        variations=(),
        bot_responde=bool(row["bot_responde"]),
    )
    res = await faq_admin.create_item(
        conn,
        data,
        actor=actor,
        clock=clock,
        origin="import",
        origin_ref=batch_id,
        change_kind="import",
    )
    if res.ok and res.item is not None:
        return res.item.id
    if res.problems and all(p.code == fv.DUPLICATE_QUESTION for p in res.problems):
        return None
    raise ImportCommitError("revalidation_failed", row["linha"])


async def commit_import(
    conn: asyncpg.Connection,
    batch_id: Any,
    *,
    actor: str,
    categories: Mapping[int, str] | None = None,
    clock: Clock | None = None,
) -> ImportResult | None:
    """Grava o lote numa transacao so. ``None`` se ja gravado/cancelado/expirado (nao duplica).

    ``categories``: ``{linha: CATEGORIA}`` escolhidas na conferencia. Cada linha que entra e
    revalidada contra a FAQ atual; duplicata nova fica de fora; qualquer outra falha levanta
    ``ImportCommitError`` e desfaz tudo (o lote continua em ``preview``).
    """
    async with conn.transaction():
        batch = await ib.claim_commit(conn, batch_id, clock=clock)
        if batch is None:
            return None
        if batch.kind != "faq" or batch.brand is None:
            raise ImportCommitError("wrong_batch")
        rows = _apply_categories(batch.rows, categories)
        ids: list[int] = []
        for row in rows:
            if row["situacao"] != ENTRA:
                continue
            new_id = await _insert_row(
                conn, row, brand=batch.brand, batch_id=str(batch.id), actor=actor, clock=clock
            )
            if new_id is not None:
                ids.append(new_id)
        result = ImportResult(
            batch_id=str(batch.id),
            imported=len(ids),
            skipped=len(rows) - len(ids),
            item_ids=tuple(ids),
        )
        await audit.write(
            conn,
            _ACTOR_TYPE,
            actor,
            "faq.import",
            "import_batch",
            str(batch.id),
            None,
            {"brand": batch.brand, "imported": result.imported, "skipped": result.skipped},
            clock=clock,
        )
        return result


# ---------------------------------------------------------------- modelo


def template_xlsx() -> bytes:
    """Planilha modelo (.xlsx) com o cabecalho e duas linhas de exemplo."""
    wb = Workbook()
    ws = wb.active
    if ws is None:  # pragma: no cover - Workbook() sempre cria a aba
        raise RuntimeError("workbook sem aba")
    ws.title = "FAQ"
    ws.append(list(_TEMPLATE_HEADER))
    for sample in _TEMPLATE_ROWS:
        ws.append(list(sample))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
