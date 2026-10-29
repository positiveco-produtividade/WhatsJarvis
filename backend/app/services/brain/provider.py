"""`BrainProvider`: o cerebro como `AnswerProvider` (CEREBRO-1, spec 4.4, 4.5, 4.8, 4.10).

Guardas sem custo, marca e acesso, recuperacao na fonte (so marcas permitidas), decisao por
nota, IA (T28, spec 4.4.1), regra de preco e montagem. A IA entra em `_ai_stage`, depois do
atalho direto (nota >= DIRECT) e antes do corte por palavras; uma chamada por mensagem, sem
retentativa. Sem cliente ou sem banco a IA fica desligada (so palavras). O alerta
`brain_api_failure` sai dos efeitos (`_ProviderEffects.apply`), dentro da transacao 2.
Nao importa `app.panel` nem `httpx`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.core.values import BrainOutcome, ReplyKind
from app.services.alerts import raise_alert
from app.services.answering import Answer, AnswerContext, AppliedEffects
from app.services.brain import assembly, fidelity, intents, matching, memory, prompt
from app.services.brain.effects import (
    REASON_BRAND_DISABLED,
    BrainEffects,
)
from app.services.brain.llm import LlmError, LlmRequest, LlmResult
from app.services.brain.meter import BrainMeter
from app.services.brain.source import KnowledgeItem, KnowledgeSource, Scored, to_searchable
from app.services.flags import Flags

logger = logging.getLogger(__name__)

PATH_WORDS = "palavras"
PATH_AI = "ia"
PATH_NONE = "nenhum"
REASON_NO_MATCH = "no_match"
REASON_NOT_ANSWERABLE = "not_answerable"
REASON_NO_PRICE = "no_price_in_source"
REASON_PAUSED = "paused"
API_FAILURE_KIND = "brain_api_failure"
API_FAILURE_WINDOW = timedelta(hours=1)
FOLLOW_UP_MAX_TOKENS = 2  # ate N tokens uteis + janela de contexto = follow-up (spec 4.4 e 3)
FLAG_FOLLOW_UP = "follow_up"
FLAG_AI_FAILED = "ai_failed"
FLAG_BUDGET = "budget_blocked"
FLAG_USAGE_MISSING = "usage_missing"
FLAG_PRICE_UNSET = "price_unconfigured"

# Rotulos para o ticket de acesso (o cerebro nao importa `app.panel`).
_BRAND_LABELS: dict[str, str] = {
    "jungle": "Jungle",
    "plant_power": "Plant Power",
    "possible": "Possible",
    "a_tal_da_castanha": "A Tal da Castanha",
    "zaya": "Zaya",
}


class LlmPort(Protocol):
    """O que o cerebro usa do cliente Gemini (`LlmClient` cumpre)."""

    async def complete(self, request: LlmRequest) -> LlmResult: ...


@dataclass(frozen=True)
class _Pick:
    """Item escolhido para responder e a nota que o levou ate aqui.

    `text` so existe na reescrita fundamentada (IA, fidelidade `ok`); `cited` lista todos os
    itens citados (o primeiro e o melhor)."""

    item: KnowledgeItem
    score: float
    text: str | None = None
    cited: tuple[KnowledgeItem, ...] = ()

    @property
    def items(self) -> tuple[KnowledgeItem, ...]:
        return self.cited or (self.item,)


@dataclass
class _Run:
    """Estado de uma mensagem: o que `BrainEffects` precisa e o relogio dos estagios."""

    ctx: AnswerContext
    started: float = field(default_factory=time.monotonic)
    timings: dict[str, int] = field(default_factory=dict)
    path: str = PATH_WORDS
    flags: list[str] = field(default_factory=list)
    ai_ready: bool | None = None
    history: list[memory.Exchange] | None = None
    follow_up: bool = False
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal(0)
    fidelity: str | None = None
    ai_verdict: str | None = None  # intent do modelo que encerra sem resposta
    api_failure: str | None = None  # tipo da falha da IA (vira alerta nos efeitos)

    def mark(self, stage: str, since: float) -> None:
        self.timings[stage] = int((time.monotonic() - since) * 1000)


@dataclass
class _ProviderEffects(BrainEffects):
    """`BrainEffects` + alerta `brain_api_failure` (dedup global de 1 h), na transacao 2."""

    api_failure: str | None = None

    async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects:
        applied = await super().apply(conn, trace_id, user_id)
        if self.api_failure is not None:
            await raise_alert(
                conn, API_FAILURE_KIND, "warn", "Falha repetida da IA (Gemini)",
                {"trace_id": trace_id, "error_kind": self.api_failure},
                dedup_key=API_FAILURE_KIND, dedup_window=API_FAILURE_WINDOW, email=True,
                flags=self.flags_service, settings=self.settings, clock=self.clock,
            )  # fmt: skip
        return applied


class BrainProvider:
    def __init__(
        self,
        source: KnowledgeSource,
        flags: Flags,
        settings: Settings,
        clock: Clock | None = None,
        *,
        llm: LlmPort | None = None,
        meter: BrainMeter | None = None,
        db: Any | None = None,
    ) -> None:
        """`llm` + `db` (pool ou conexao: memoria e medidor) ligam a IA; sem eles, so palavras."""
        self._source = source
        self._flags = flags
        self._settings = settings
        self._clock = clock
        self._llm = llm
        self._db = db
        self._meter = meter or (BrainMeter(settings, clock or SystemClock()) if llm else None)

    async def answer(self, ctx: AnswerContext) -> Answer:
        run = _Run(ctx)
        if await self._flags.paused():  # CB-22: pausado nao busca nem chama o Gemini (custo zero)
            return self._no_answer(run, REASON_PAUSED, best_score=None)
        guard = intents.check_guards(ctx.text)
        if guard is not None:
            return self._from_guard(run, guard)
        cited = matching.detect_brand(ctx.text)
        early = await self._brand_stage(run, cited)
        if early is not None:
            return early
        return await self._retrieve_and_decide(run, cited)

    # ------------------------------------------------------------------ estagio 1

    def _from_guard(self, run: _Run, guard: intents.GuardResult) -> Answer:
        outcome = guard.outcome
        greeting = (
            self._settings.text_brain_thanks if guard.thanks else self._settings.text_brain_greeting
        )
        text = {
            BrainOutcome.GREETING: greeting,
            BrainOutcome.NO_ANSWER: self._settings.text_brain_no_answer,
        }.get(outcome, self._settings.text_brain_refusal)
        return self._notice(
            run, text, outcome, classification=guard.classification,
            reason=guard.escalation_reason if outcome == BrainOutcome.NO_ANSWER else None,
        )  # fmt: skip

    # ------------------------------------------------------------------ estagio 2

    async def _brand_stage(self, run: _Run, cited: str | None) -> Answer | None:
        if cited is None:
            return None
        if cited not in run.ctx.brands:
            return self._notice(
                run, self._settings.text_brain_access_request, BrainOutcome.ACCESS_REQUEST,
                brand=cited,
            )  # fmt: skip
        if not await self._flags.brand_enabled(cited):
            return self._notice(
                run, self._settings.text_brain_no_answer, BrainOutcome.BRAND_DISABLED,
                brand=cited,
            )  # fmt: skip
        return None

    # ------------------------------------------------------------------ estagios 3 e 4

    async def _retrieve_and_decide(self, run: _Run, cited: str | None) -> Answer:
        cfg = self._settings
        text = run.ctx.text
        query = await self._query(run)
        t0 = time.monotonic()
        found = await self._source.search(
            query,
            brands=list(run.ctx.brands),
            limit=cfg.brain_candidates_k,
            floor=cfg.brain_candidate_floor,
        )
        run.mark("retrieval", t0)
        best = found[0] if found else None
        best_score = best.score if best else None
        if best is None:
            return self._no_answer(run, REASON_NO_MATCH, best_score=None)
        if not best.item.answerable:
            return self._no_answer(run, REASON_NOT_ANSWERABLE, best_score, pick=best)
        if cited is None and (self._is_tie(found) or self._is_generic_tie(run, found)):
            return self._notice(
                run, cfg.text_brain_ask_brand, BrainOutcome.ASK_BRAND, best_score=best_score,
                item_refs=[_ref(best.item)], path=PATH_WORDS,
            )  # fmt: skip
        if intents.check_calc(text) is not None and best.score < cfg.brain_direct_threshold:
            return self._no_answer(run, intents.REASON_CALC, best_score, pick=best)
        pick = await self._decide(run, found)
        if pick is None:
            return self._declined(run, best, cited)
        return await self._finish(run, pick, cited)

    def _declined(self, run: _Run, best: Scored, cited: str | None) -> Answer:
        """Sem item escolhido: o veredito da IA (ask_brand/refusal) ou `no_answer`."""
        if run.ai_verdict == prompt.INTENT_ASK_BRAND:
            return self._notice(
                run, self._settings.text_brain_ask_brand, BrainOutcome.ASK_BRAND,
                best_score=best.score, item_refs=[_ref(best.item)], path=run.path,
            )  # fmt: skip
        if run.ai_verdict == prompt.INTENT_OUT_OF_SCOPE:
            return self._notice(
                run, self._settings.text_brain_refusal, BrainOutcome.REFUSAL,
                brand=cited, best_score=best.score, path=run.path,
                classification=intents.classify(run.ctx.text),
            )  # fmt: skip
        reason = (
            REASON_NO_PRICE
            if run.fidelity == fidelity.FidelityStatus.FAILED.value
            and intents.has_price_intent(run.ctx.text)
            else REASON_NO_MATCH
        )
        return self._no_answer(run, reason, best.score, pick=best)

    def _is_tie(self, found: list[Scored]) -> bool:
        matches = [matching.Match(s.item.ref, s.item.brand, s.score) for s in found]
        return matching.is_brand_tie(matches, threshold=self._settings.brain_match_threshold)

    def _is_generic_tie(self, run: _Run, found: list[Scored]) -> bool:
        """Pergunta generica de vendedor com 2+ marcas: melhores de marcas diferentes empatam."""
        if run.follow_up or len(set(run.ctx.brands)) < 2:
            return False
        matches = [matching.Match(s.item.ref, s.item.brand, s.score) for s in found]
        items = {s.item.ref: to_searchable(s.item) for s in found}
        return matching.is_generic_brand_tie(
            run.ctx.text, matches, items, threshold=self._settings.brain_match_threshold
        )

    async def _decide(self, run: _Run, found: list[Scored]) -> _Pick | None:
        """Escolhe o item. Direta com nota >= DIRECT (fora follow-up); senao IA; senao palavras."""
        best = found[0]
        if best.score >= self._settings.brain_direct_threshold and not run.follow_up:
            return _Pick(best.item, best.score)
        ai = await self._ai_stage(run, found)
        if ai is not None:
            return ai
        if run.ai_verdict is not None or run.fidelity == fidelity.FidelityStatus.FAILED.value:
            return None  # a IA decidiu que nao ha resposta: nao ressuscitar pelo limiar
        if best.score >= self._settings.brain_match_threshold:
            return _Pick(best.item, best.score)
        return None

    # ------------------------------------------------------------------ IA (spec 4.4.1, 4.5, 4.8)

    async def _ai_ready(self, run: _Run) -> bool:
        """Cliente + banco presentes e orcamento do mes < 100% (resultado guardado na mensagem)."""
        if run.ai_ready is None:
            ready = self._llm is not None and self._db is not None and self._meter is not None
            if ready and self._meter is not None and not await self._meter.ai_allowed(self._db):
                run.flags.append(FLAG_BUDGET)
                ready = False
            run.ai_ready = ready
        return run.ai_ready

    async def _history(self, run: _Run) -> list[memory.Exchange]:
        if run.history is None:
            ctx = run.ctx
            run.history = []
            if ctx.trace_id is not None and await self._ai_ready(run):
                run.history = await memory.recent_exchanges(
                    self._db, user_id=ctx.user_id, trace_id=ctx.trace_id, now=self._now(run),
                    exchanges=self._settings.brain_context_exchanges,
                    minutes=self._settings.brain_context_minutes,
                )  # fmt: skip
        return run.history

    def _now(self, run: _Run) -> Any:
        return self._clock.now() if self._clock is not None else run.ctx.received_at

    async def _query(self, run: _Run) -> str:
        """Texto da busca; follow-up curto soma os tokens da ultima pergunta do vendedor."""
        text = run.ctx.text
        if len(matching.tokens(text)) > FOLLOW_UP_MAX_TOKENS or self._llm is None:
            return text
        history = await self._history(run)
        if not history:
            return text
        run.follow_up = True
        run.flags.append(FLAG_FOLLOW_UP)
        # So palavras: a UF/"no Brasil" da busca vem desta mensagem, nunca do historico.
        return f"{text} {matching.strip_ufs(history[-1].question)}"

    async def _ai_stage(self, run: _Run, found: list[Scored]) -> _Pick | None:
        """Uma chamada ao Gemini; `None` = sem IA, falha (reserva por palavras) ou veredito."""
        if not await self._ai_ready(run) or self._llm is None:
            return None
        items = [s.item for s in found if s.item.answerable]
        history = await self._history(run)
        mode = self._settings.brain_answer_mode
        request = prompt.build_request(run.ctx.text, history, items, mode=mode)
        t0 = time.monotonic()
        try:
            result = await self._llm.complete(request)
        except LlmError as exc:
            run.mark("ai", t0)
            self._ai_failed(run, exc.kind)
            return None
        run.mark("ai", t0)
        self._record_usage(run, result)
        output = prompt.parse_output(result.data, {i.ref for i in items}, mode=mode)
        if output is None:
            self._ai_failed(run, "invalid_output")
            return None
        return self._verify(run, output, found, items)

    def _ai_failed(self, run: _Run, kind: str) -> None:
        run.llm_calls = 1
        run.flags.append(FLAG_AI_FAILED)
        run.api_failure = kind
        logger.warning("brain_llm_call", extra={"state": "failed", "kind": kind})

    def _record_usage(self, run: _Run, result: LlmResult) -> None:
        run.llm_calls = 1
        run.tokens_in, run.tokens_out = result.tokens_in, result.tokens_out
        if not result.usage_present:
            run.flags.append(FLAG_USAGE_MISSING)
        try:
            if self._meter is not None:
                run.cost_usd = self._meter.cost_usd(result.tokens_in, result.tokens_out)
        except InvalidValue:
            run.flags.append(FLAG_PRICE_UNSET)
        logger.info(
            "brain_llm_call",
            extra={"state": "ok", "tokens_in": result.tokens_in, "tokens_out": result.tokens_out},
        )

    def _verify(
        self, run: _Run, output: prompt.ModelOutput, found: list[Scored], items: list[KnowledgeItem]
    ) -> _Pick | None:
        if output.intent != prompt.INTENT_ANSWER:
            run.ai_verdict = output.intent
            run.path = PATH_AI
            return None
        t0 = time.monotonic()
        candidates = [fidelity.Candidate(i.ref, i.text) for i in items]
        price = intents.has_price_intent(run.ctx.text)
        if self._settings.brain_answer_mode == prompt.MODE_VERBATIM:
            result = fidelity.check_verbatim(output.item_refs, candidates, price_intent=price)
        else:
            result = fidelity.check(
                output.answer_text, output.item_refs, candidates,
                question=run.ctx.text, price_intent=price,
            )  # fmt: skip
        run.mark("verification", t0)
        run.fidelity = result.status.value
        run.path = PATH_AI
        if result.status == fidelity.FidelityStatus.FAILED or result.text is None:
            run.fidelity = fidelity.FidelityStatus.FAILED.value
            return None
        by_ref = {i.ref: i for i in items}
        cited = tuple(by_ref[r] for r in result.refs)
        score = next(s.score for s in found if s.item.ref == cited[0].ref)
        verbatim = result.status != fidelity.FidelityStatus.OK or result.text == cited[0].text
        return _Pick(cited[0], score, None if verbatim else result.text, cited)

    # ------------------------------------------------------------------ estagios 5 e 6

    async def _finish(self, run: _Run, pick: _Pick, cited: str | None) -> Answer:
        item = pick.item
        text = run.ctx.text
        body = pick.text if pick.text is not None else item.text
        price = intents.has_price_intent(text)
        if price and _lacks_price(item.ref, body):
            return self._no_answer(run, REASON_NO_PRICE, pick.score, pick=Scored(item, pick.score))
        brand = None if item.brand == matching.ALL_BRANDS else item.brand
        if brand is not None and not await self._flags.brand_enabled(brand):
            return self._notice(
                run, self._settings.text_brain_no_answer, BrainOutcome.BRAND_DISABLED,
                brand=brand, best_score=pick.score, path=run.path,
            )  # fmt: skip
        t0 = time.monotonic()
        parts = assembly.assemble(body, brands=list(dict.fromkeys(i.brand for i in pick.items)))
        run.mark("assembly", t0)
        effects = self._effects(
            run, BrainOutcome.ANSWERED, path=run.path, brand=brand or cited,
            classification=intents.classify(text), item_refs=[_ref(i) for i in pick.items],
            best_score=pick.score, confidence=pick.score,
        )  # fmt: skip
        return Answer(
            kind=ReplyKind.BRAIN_ANSWER_RATE.value, text=parts[-1], parts=parts, effects=effects
        )

    # ------------------------------------------------------------------ saidas

    def _no_answer(
        self, run: _Run, reason: str, best_score: float | None, pick: Scored | None = None
    ) -> Answer:
        classification = (
            intents.PRECO
            if reason in (REASON_NO_PRICE, intents.REASON_CALC)
            else intents.classify(run.ctx.text)
        )
        return self._notice(
            run, self._settings.text_brain_no_answer, BrainOutcome.NO_ANSWER,
            reason=reason, classification=classification, best_score=best_score,
            item_refs=[_ref(pick.item)] if pick else [], path=run.path,
            brand=_brand_of(pick.item) if pick else None,
        )  # fmt: skip

    def _notice(
        self,
        run: _Run,
        text: str,
        outcome: BrainOutcome,
        *,
        classification: str = intents.NAO_IDENTIFICADO,
        reason: str | None = None,
        brand: str | None = None,
        best_score: float | None = None,
        item_refs: list[str] | None = None,
        path: str = PATH_NONE,
    ) -> Answer:
        effects = self._effects(
            run, outcome, path=path, brand=brand, classification=classification,
            reason=reason if outcome != BrainOutcome.BRAND_DISABLED else REASON_BRAND_DISABLED,
            item_refs=item_refs or [], best_score=best_score,
        )  # fmt: skip
        return Answer(kind=ReplyKind.BRAIN_NOTICE.value, text=text, parts=(text,), effects=effects)

    def _effects(
        self,
        run: _Run,
        outcome: BrainOutcome,
        *,
        path: str,
        brand: str | None,
        classification: str,
        reason: str | None = None,
        item_refs: list[str] | None = None,
        best_score: float | None = None,
        confidence: float | None = None,
    ) -> BrainEffects:
        run.timings["total"] = int((time.monotonic() - run.started) * 1000)
        called = run.llm_calls > 0
        return _ProviderEffects(
            outcome=outcome,
            flags_service=self._flags,
            question=run.ctx.text,
            path=path,
            classification=classification,
            brand=brand,
            brand_label=_BRAND_LABELS.get(brand or ""),
            reason=reason,
            confidence=confidence,
            item_refs=item_refs or [],
            best_score=best_score,
            llm_calls=run.llm_calls,
            tokens_in=run.tokens_in,
            tokens_out=run.tokens_out,
            cost_usd=float(run.cost_usd),
            context_turns=len(run.history or []),
            fidelity=run.fidelity,
            run_flags=list(run.flags),
            prompt_version=prompt.PROMPT_VERSION if called else None,
            model=self._settings.gemini_model if called else None,
            timings_ms=dict(run.timings),
            clock=self._clock,
            settings=self._settings,
            api_failure=run.api_failure,
        )


def _ref(item: KnowledgeItem) -> str:
    return f"{item.ref}@v{item.version}"


def _brand_of(item: KnowledgeItem) -> str | None:
    return None if item.brand == matching.ALL_BRANDS else item.brand


def _lacks_price(ref: str, text: str) -> bool:
    """Regra de preco (CB-8): a resposta final precisa trazer ao menos um valor R$."""
    result = fidelity.check_verbatim([ref], [fidelity.Candidate(ref, text)], price_intent=True)
    return result.status == fidelity.FidelityStatus.FAILED
