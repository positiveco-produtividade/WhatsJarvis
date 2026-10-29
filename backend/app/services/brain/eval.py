"""Gate de avaliacao do cerebro (CB-21, spec 4.14): `python -m app.services.brain.eval`.

Le `tests/brain/eval/cases/*.jsonl` e roda o `BrainProvider` real contra uma fonte em memoria
(`guia:NNN` = posicao 1-based em `piloto/guia.json`, a mesma ordem da carga `faq seed`; itens
inline `items` do caso valem so para ele). Dois modos:

- `palavras`: sem rede e deterministico (nada de Gemini; roda no CI). Casos com `history`
  (follow-up) dependem da memoria da IA e ficam FORA da nota (relatados como `pulado`).
- `ia`: Gemini real (chave e precos nas configuracoes). O historico do caso entra por um banco
  falso em memoria (so responde a janela de contexto e gasto zero); imprime o custo.

Nota factual = resultado certo E itens citados / `must_contain`. "Nao sei" conta como acerto
quando o caso escala (`no_answer`). Item da fonte sem resposta aprovada (vazia/`bot_responde`
falso) em caso `responde` e relatado como `pulado`, sem quebrar. Saida 1 se factual < 98% ou
QUALQUER caso de preco falhar. O console mostra so ids, contagens e custo; perguntas e respostas
ficam apenas no relatorio local (`--report`). Nao importa `app.panel` nem `httpx`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import SecretStr

from app.config import Settings
from app.core.values import BrainOutcome
from app.services.answering import Answer, AnswerContext
from app.services.brain import matching
from app.services.brain.llm import ERR_TIMEOUT, LlmClient, LlmError, LlmRequest, LlmResult
from app.services.brain.provider import BrainProvider, LlmPort
from app.services.brain.source import KnowledgeItem, Scored, to_searchable
from app.services.flags import Flags

__all__ = ["CaseResult", "Summary", "load_cases", "load_guia", "main", "run_eval"]

MODE_WORDS = "palavras"
MODE_AI = "ia"
FACTUAL_MIN = 0.98
BACKEND_DIR = Path(__file__).resolve().parents[3]
DEFAULT_CASES = BACKEND_DIR / "tests" / "brain" / "eval" / "cases"
DEFAULT_GUIA = BACKEND_DIR.parent / "piloto" / "guia.json"
_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
_EXPECTED_OUTCOMES: dict[str, frozenset[BrainOutcome]] = {
    "responde": frozenset({BrainOutcome.ANSWERED}),
    "saudacao": frozenset({BrainOutcome.GREETING}),
    "nao_sei": frozenset({BrainOutcome.NO_ANSWER}),
    "pede_marca": frozenset({BrainOutcome.ASK_BRAND}),
    "acesso": frozenset({BrainOutcome.ACCESS_REQUEST}),
    # Recusa tambem aceita `no_answer`: nao responde e escala (sem IA, so as guardas recusam).
    "recusa": frozenset({BrainOutcome.REFUSAL, BrainOutcome.INJECTION, BrainOutcome.NO_ANSWER}),
}
_BRAND_MAP = {
    "jungle": "jungle",
    "plant power": "plant_power",
    "possible": "possible",
    "a tal da castanha": "a_tal_da_castanha",
    "zaya": "zaya",
    "todas": matching.ALL_BRANDS,
}
# Mesma regra de contato da carga (`faq_validation.find_contact`), copiada: o cerebro nao
# importa o painel.
_EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
_LINK = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|app|me|co|ly|link|gov|edu|br)\b"
    r"|\bwa\.me\b",
    re.IGNORECASE,
)
_PHONE_RUN = re.compile(r"\+?\d(?:[\d\s().-]*\d)?")
_PHONE_MIN_DIGITS = 8


class EvalConfigError(Exception):
    """Configuracao ou arquivo invalido (mensagem sem segredo nem texto de caso)."""


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    question: str
    brands: tuple[str, ...]
    ufs: tuple[str, ...]
    expected: str
    refs: tuple[str, ...]
    history: tuple[tuple[str, str], ...] = ()
    items: tuple[KnowledgeItem, ...] = ()
    must_contain: tuple[str, ...] = ()
    price_in_source: bool | None = None

    @property
    def is_price(self) -> bool:
        return self.category == "preco" or self.price_in_source is not None


@dataclass
class CaseResult:
    case: EvalCase
    outcome: str = ""
    ok: bool = False
    skipped: str | None = None
    problems: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    question: str = ""
    answer: str = ""
    refs: list[str] = field(default_factory=list)  # itens citados (sem versao)


@dataclass
class LlmStats:
    """Contagem das chamadas ao Gemini no modo `ia` (so numeros, nunca texto)."""

    calls: int = 0
    timeouts: int = 0
    errors: int = 0  # falhas que nao sao timeout


class _CountingLlm:
    """Envolve o cliente e conta chamadas, timeouts e erros; repassa tudo sem alterar."""

    def __init__(self, inner: LlmPort, stats: LlmStats) -> None:
        self._inner = inner
        self._stats = stats

    async def complete(self, request: LlmRequest) -> LlmResult:
        self._stats.calls += 1
        try:
            return await self._inner.complete(request)
        except LlmError as exc:
            if exc.kind == ERR_TIMEOUT:
                self._stats.timeouts += 1
            else:
                self._stats.errors += 1
            raise


@dataclass
class Summary:
    results: list[CaseResult]
    llm: LlmStats = field(default_factory=LlmStats)

    @property
    def scored(self) -> list[CaseResult]:
        return [r for r in self.results if r.skipped is None]

    @property
    def factual(self) -> float:
        scored = self.scored
        return sum(r.ok for r in scored) / len(scored) if scored else 0.0

    @property
    def price_failures(self) -> list[str]:
        return [r.case.id for r in self.scored if r.case.is_price and not r.ok]

    @property
    def cost_usd(self) -> float:
        return sum(r.cost_usd for r in self.results)

    def by_category(self) -> dict[str, tuple[int, int, int]]:
        """categoria -> (acertos, avaliados, pulados)."""
        table: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
        for r in self.results:
            row = table[r.case.category]
            if r.skipped is not None:
                row[2] += 1
            else:
                row[1] += 1
                row[0] += int(r.ok)
        return {k: (v[0], v[1], v[2]) for k, v in sorted(table.items())}

    @property
    def passed(self) -> bool:
        return self.factual >= FACTUAL_MIN and not self.price_failures


# ---------------------------------------------------------------------- fonte em memoria


class MemorySource:
    """`KnowledgeSource` deterministico: mesma busca por palavras da `FaqSource`, sem banco."""

    name = "eval"

    def __init__(self, items: Sequence[KnowledgeItem]) -> None:
        self._items = list(items)

    async def search(
        self,
        query: str,
        *,
        brands: Sequence[str],
        limit: int,
        floor: float,
        conn: Any | None = None,
    ) -> list[Scored]:
        by_ref = {i.ref: i for i in self._items}
        matches = matching.rank(
            query,
            [to_searchable(i) for i in self._items],
            brand_filter=tuple(brands),
            limit=limit,
            floor=floor,
        )
        return [Scored(item=by_ref[m.ref], score=m.score) for m in matches]


class _AllBrandsOn:
    """Flags de avaliacao: toda marca ligada."""

    async def brand_enabled(self, brand: str, conn: Any | None = None) -> bool:
        return True

    async def paused(self, *, conn: Any | None = None) -> bool:
        return False


class _HistoryDb:
    """Banco falso da IA: gasto do mes zero e a janela de contexto do caso (`memory`)."""

    def __init__(self, history: tuple[tuple[str, str], ...]) -> None:
        self._rows = _exchanges(history)

    async def fetchval(self, sql: str, *args: Any) -> int:
        return 0

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        limit = int(args[-1])
        return list(reversed(self._rows))[:limit]  # a consulta real devolve do mais novo


def _exchanges(history: tuple[tuple[str, str], ...]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pending: str | None = None
    for role, text in history:
        if role == "user":
            pending = text
        elif pending is not None:
            rows.append({
                "trace_id": len(rows) + 1, "received_at": _NOW,
                "question": pending, "answer": text,
            })
            pending = None
    return rows


# ---------------------------------------------------------------------- carga


def _has_contact(text: str) -> bool:
    if any(
        sum(c.isdigit() for c in m.group()) >= _PHONE_MIN_DIGITS for m in _PHONE_RUN.finditer(text)
    ):
        return True
    return bool(_EMAIL.search(text) or _LINK.search(_EMAIL.sub(" ", text)))


def load_guia(path: Path) -> list[KnowledgeItem]:
    """Itens do guia na ordem da carga (`guia:NNN`, NNN = posicao 1-based).

    Resposta vazia ou com telefone/e-mail/link entra sem resposta (`answerable=False`), como
    na carga (`bot_responde=false`)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        raw_items = data["itens"]
        assert isinstance(raw_items, list) and raw_items
    except (OSError, ValueError, KeyError, TypeError, AssertionError) as exc:
        raise EvalConfigError("guia ilegivel ou sem a lista 'itens'") from exc
    return [_guia_item(n, raw) for n, raw in enumerate(raw_items, start=1)]


def _guia_item(position: int, raw: Any) -> KnowledgeItem:
    brand = _BRAND_MAP.get(str(raw.get("marca", "")).strip().casefold())
    question = str(raw.get("q", "")).strip()
    if brand is None or not question:
        raise EvalConfigError(f"item {position} do guia invalido")
    answer = str(raw.get("a", "")).strip()
    approved = answer if answer and not _has_contact(answer) else ""
    is_argument = str(raw.get("cat", "")).strip().casefold() == "argumento"
    category = "ARGUMENTO" if is_argument else "PRODUTO"
    return KnowledgeItem(
        ref=f"guia:{position:03d}", version=1, brand=brand, category=category,
        title=question, text=approved, origin="seed", answerable=bool(approved),
    )  # fmt: skip


def _inline_item(raw: dict[str, Any]) -> KnowledgeItem:
    return KnowledgeItem(
        ref=str(raw["ref"]), version=1, brand=str(raw["brand"]),
        category=str(raw.get("category", "PRODUTO")), title=str(raw["title"]),
        text=str(raw["text"]), origin=str(raw.get("origin", "manual")), answerable=True,
        variations=tuple(str(v) for v in raw.get("variations", ())),
    )  # fmt: skip


def _parse_case(raw: dict[str, Any]) -> EvalCase:
    reg = raw["registration"]
    return EvalCase(
        id=str(raw["id"]), category=str(raw["category"]), question=str(raw["question"]),
        brands=tuple(reg["brands"]), ufs=tuple(reg.get("ufs", ())),
        expected=str(raw["expected"]), refs=tuple(raw.get("refs", ())),
        history=tuple((str(h["role"]), str(h["text"])) for h in raw.get("history", ())),
        items=tuple(_inline_item(i) for i in raw.get("items", ())),
        must_contain=tuple(raw.get("must_contain", ())),
        price_in_source=raw.get("price_in_source"),
    )  # fmt: skip


def load_cases(directory: Path) -> list[EvalCase]:
    """Todos os `*.jsonl` do diretorio, em ordem de arquivo e linha."""
    cases: list[EvalCase] = []
    try:
        for path in sorted(directory.glob("*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    cases.append(_parse_case(json.loads(line)))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise EvalConfigError(f"casos ilegiveis em {directory.name}/") from exc
    if not cases:
        raise EvalConfigError("nenhum caso encontrado")
    return cases


# ---------------------------------------------------------------------- avaliacao


def _answer_text(answer: Answer) -> str:
    return "\n".join(answer.parts) if answer.parts else answer.text


def score_case(case: EvalCase, outcome: BrainOutcome, refs: list[str], text: str) -> list[str]:
    """Problemas do caso (lista vazia = acerto)."""
    problems: list[str] = []
    if outcome not in _EXPECTED_OUTCOMES.get(case.expected, frozenset()):
        problems.append(f"desfecho {outcome.value}, esperado {case.expected}")
        return problems
    if case.expected == "saudacao":  # ex.: agradecimento responde "por nada", nao "oi"
        return _missing_snippets(case, text)
    if case.expected != "responde":
        return problems
    cited = {r.split("@", 1)[0] for r in refs}
    # "guia:013|guia:018" = qualquer um dos dois serve (itens que respondem a mesma coisa).
    problems += [f"nao citou {r}" for r in case.refs if not cited & set(r.split("|"))]
    return problems + _missing_snippets(case, text)


def _missing_snippets(case: EvalCase, text: str) -> list[str]:
    folded = text.casefold()
    missing = [i for i, m in enumerate(case.must_contain, 1) if m.casefold() not in folded]
    return [f"faltou trecho {i}" for i in missing]


def _unscorable(case: EvalCase, guia: dict[str, KnowledgeItem], mode: str) -> str | None:
    if case.history and mode == MODE_WORDS:
        return "followup_precisa_ia"
    if case.expected == "responde" and any(
        r in guia and not guia[r].answerable for r in case.refs
    ):
        return "fonte_sem_resposta_aprovada"
    return None


def _settings_words() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        database_url=SecretStr("postgresql://eval"),
        whatsapp_app_secret=SecretStr("eval"),
        whatsapp_verify_token=SecretStr("eval"),
        whatsapp_access_token=SecretStr("eval"),
        whatsapp_phone_number_id="0",
    )


def _settings_ai() -> Settings:
    settings = Settings()  # type: ignore[call-arg]
    missing = []
    if not settings.gemini_api_key.get_secret_value().strip():
        missing.append("GEMINI_API_KEY")
    if settings.brain_price_in_per_mtok_usd is None:
        missing.append("BRAIN_PRICE_IN_PER_MTOK_USD")
    if settings.brain_price_out_per_mtok_usd is None:
        missing.append("BRAIN_PRICE_OUT_PER_MTOK_USD")
    if missing:
        raise EvalConfigError("modo ia exige: " + ", ".join(missing))
    return settings


async def _run_case(
    case: EvalCase,
    guia: Sequence[KnowledgeItem],
    settings: Settings,
    llm: LlmPort | None,
) -> CaseResult:
    source = MemorySource([*guia, *case.items])
    db = _HistoryDb(case.history) if llm is not None else None
    provider = BrainProvider(
        source, cast(Flags, _AllBrandsOn()), settings, None, llm=llm, db=db
    )
    ctx = AnswerContext(
        user_id=0, profile="vendedor", ufs=list(case.ufs), channels=[], brands=list(case.brands),
        text=case.question, inbound_wa_message_id="eval", received_at=_NOW,
        trace_id=1 if llm is not None else None,
    )  # fmt: skip
    answer = await provider.answer(ctx)
    effects: Any = answer.effects
    text = _answer_text(answer)
    problems = score_case(case, effects.outcome, list(effects.item_refs), text)
    return CaseResult(
        case=case, outcome=effects.outcome.value, ok=not problems, problems=problems,
        cost_usd=float(effects.cost_usd), question=case.question, answer=text,
        refs=[str(r).split("@", 1)[0] for r in effects.item_refs],
    )  # fmt: skip


async def run_eval(
    cases: Sequence[EvalCase],
    guia: Sequence[KnowledgeItem],
    *,
    mode: str,
    settings: Settings,
    llm: LlmPort | None = None,
) -> Summary:
    """Roda todos os casos; `llm` so e usado no modo `ia`."""
    by_ref = {i.ref: i for i in guia}
    stats = LlmStats()
    counted = _CountingLlm(llm, stats) if llm is not None else None
    results: list[CaseResult] = []
    for case in cases:
        reason = _unscorable(case, by_ref, mode)
        if reason is not None:
            results.append(CaseResult(case=case, skipped=reason, question=case.question))
            continue
        results.append(
            await _run_case(case, guia, settings, counted if mode == MODE_AI else None)
        )
    return Summary(results, stats)


# ---------------------------------------------------------------------- saida


def write_report(summary: Summary, path: Path, mode: str) -> None:
    """Relatorio LOCAL: unico lugar com pergunta e resposta."""
    body = {
        "mode": mode,
        "factual": round(summary.factual, 4),
        "price_failures": summary.price_failures,
        "cost_usd": round(summary.cost_usd, 6),
        "llm": {
            "calls": summary.llm.calls, "timeouts": summary.llm.timeouts,
            "errors": summary.llm.errors,
        },
        "categories": {
            k: {"ok": v[0], "scored": v[1], "skipped": v[2]}
            for k, v in summary.by_category().items()
        },
        "cases": [
            {
                "id": r.case.id, "category": r.case.category, "expected": r.case.expected,
                "outcome": r.outcome, "ok": r.ok, "skipped": r.skipped, "problems": r.problems,
                "refs": r.refs, "question": r.question, "answer": r.answer,
            }
            for r in summary.results
        ],
    }  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(summary: Summary, mode: str, report: Path | None) -> None:
    """Console: so ids, contagens e custo (nunca pergunta, resposta ou telefone)."""
    print(f"modo={mode} casos={len(summary.results)} avaliados={len(summary.scored)}")
    for cat, (ok, scored, skipped) in summary.by_category().items():
        pct = f"{ok / scored:.1%}" if scored else "n/a"
        print(f"  {cat}: {ok}/{scored} ({pct}) pulados={skipped}")
    print(f"factual={summary.factual:.1%} (minimo {FACTUAL_MIN:.0%})")
    prices = summary.price_failures
    print(f"preco: {'ok' if not prices else 'FALHOU ' + ', '.join(prices)}")
    failed = [r.case.id for r in summary.scored if not r.ok]
    if failed:
        print("falhas: " + ", ".join(failed))
    if mode == MODE_AI:
        print(f"custo_usd={summary.cost_usd:.6f}")
        llm = summary.llm
        print(f"llm_chamadas={llm.calls} timeouts={llm.timeouts} erros={llm.errors}")
    if report is not None:
        print(f"relatorio: {report}")
    print("RESULTADO: " + ("APROVADO" if summary.passed else "REPROVADO"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.services.brain.eval")
    parser.add_argument("--mode", choices=(MODE_WORDS, MODE_AI), required=True)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--guia", type=Path, default=DEFAULT_GUIA)
    parser.add_argument("--report", type=Path, default=None)
    return parser


async def _execute(args: argparse.Namespace) -> Summary:
    cases = load_cases(args.cases)
    guia = load_guia(args.guia)
    if args.mode == MODE_WORDS:
        return await run_eval(cases, guia, mode=MODE_WORDS, settings=_settings_words())
    settings = _settings_ai()
    return await run_eval(
        cases, guia, mode=MODE_AI, settings=settings, llm=LlmClient.from_settings(settings)
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    default = Path(tempfile.gettempdir()) / f"jarvis-brain-eval-{args.mode}.json"
    report: Path = args.report or default
    try:
        summary = asyncio.run(_execute(args))
    except EvalConfigError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    write_report(summary, report, args.mode)
    print_summary(summary, args.mode, report)
    return 0 if summary.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
