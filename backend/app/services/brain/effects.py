"""Efeitos de banco do desfecho do cerebro (CEREBRO-1, spec 4.7 e 4.10).

Um `BrainEffects` por mensagem: o provedor o preenche (desfecho, marca, refs, tokens, custo...)
e o devolve em `Answer.effects`; `apply` roda DENTRO da transacao 2 de `answering`, antes de
enfileirar a saida. Cria o ticket conforme a tabela 4.7, o alerta `injection_attempt` e a linha
de `chat.brain_runs`. `chat.brain_runs` NUNCA guarda texto de pergunta nem de resposta; o alerta
nunca carrega o texto do atacante. Escalados e pedidos de acesso nunca viram alerta.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import InvalidValue
from app.core.values import BrainOutcome
from app.services.alerts import raise_alert
from app.services.answering import AppliedEffects
from app.services.brain.intents import NAO_IDENTIFICADO
from app.services.flags import Flags
from app.services.question_tickets import (
    QuestionTicket,
    create_access_request_ticket,
    create_question_ticket,
)

REASON_BRAND_DISABLED = "brand_disabled"
REASON_INJECTION = "injection"
REASON_OUT_OF_SCOPE = "out_of_scope"
NO_ANSWER_REASONS: frozenset[str] = frozenset(
    {"no_match", "not_answerable", "no_price_in_source", "commercial_condition",
     "calc_not_supported", "paused"}
)  # fmt: skip
INJECTION_ALERT_KIND = "injection_attempt"
INJECTION_ALERT_WINDOW = timedelta(hours=1)
_NO_TICKET = frozenset({BrainOutcome.GREETING, BrainOutcome.ASK_BRAND})

_INSERT_RUN = (
    "INSERT INTO chat.brain_runs (trace_id, ticket_id, outcome, path, brand, item_refs, "
    "best_score, llm_calls, tokens_in, tokens_out, cost_usd, context_turns, fidelity, flags, "
    "prompt_version, model, timings_ms, created_at) "
    "VALUES ($1, $2, $3, $4, $5, $6::text[], $7, $8, $9, $10, $11, $12, $13, $14::text[], "
    "$15, $16, $17::jsonb, $18) ON CONFLICT (trace_id) DO NOTHING"
)


@dataclass
class BrainEffects:
    """Efeitos de um desfecho. `question` e `summary` vao so para o ticket (LGPD), nunca para
    `brain_runs` nem para o alerta."""

    outcome: BrainOutcome
    flags_service: Flags
    question: str | None = None
    path: str = "nenhum"
    classification: str = NAO_IDENTIFICADO
    brand: str | None = None
    brand_label: str | None = None
    reason: str | None = None  # no_answer: um de NO_ANSWER_REASONS
    summary: str | None = None
    confidence: float | None = None
    item_refs: list[str] = field(default_factory=list)  # 'faq:123@v4'
    best_score: float | None = None
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    context_turns: int = 0
    fidelity: str | None = None
    run_flags: list[str] = field(default_factory=list)
    prompt_version: str | None = None
    model: str | None = None
    timings_ms: dict[str, int] = field(default_factory=dict)
    clock: Clock | None = None
    settings: Settings | None = None

    async def apply(self, conn: Any, trace_id: int, user_id: int) -> AppliedEffects:
        now = (self.clock or SystemClock()).now()
        ticket = await self._ticket(conn, trace_id, user_id)
        if self.outcome == BrainOutcome.INJECTION:
            await self._injection_alert(conn, trace_id, user_id)
        await self._insert_run(conn, trace_id, ticket, now)
        return AppliedEffects(
            ticket_id=ticket.id if ticket else None,
            ticket_number=ticket.ticket_number if ticket else None,
            rate=self.outcome == BrainOutcome.ANSWERED and ticket is not None,
        )

    async def _ticket(self, conn: Any, trace_id: int, user_id: int) -> QuestionTicket | None:
        outcome = self.outcome
        if outcome in _NO_TICKET:
            return None
        if outcome == BrainOutcome.ACCESS_REQUEST:
            if not self.brand:
                raise InvalidValue("brand", "pedido de acesso exige a marca")
            return await create_access_request_ticket(
                conn, user_id=user_id, trace_id=trace_id, message=self.question,
                brand=self.brand, brand_label=self.brand_label or self.brand,
                flags=self.flags_service, clock=self.clock,
            )  # fmt: skip
        escalated, reason = self._escalation()
        return await create_question_ticket(
            conn, user_id=user_id, trace_id=trace_id, message=self.question,
            flags=self.flags_service, clock=self.clock, classification=self.classification,
            brand=self.brand, summary=self.summary, escalation_reason=reason,
            escalated=escalated, outcome=outcome, confidence=self.confidence,
            meta={"refs": list(self.item_refs), "prompt": self.prompt_version,
                  "model": self.model},
        )  # fmt: skip

    def _escalation(self) -> tuple[bool, str | None]:
        outcome = self.outcome
        if outcome == BrainOutcome.ANSWERED:
            return False, None
        if outcome == BrainOutcome.NO_ANSWER:
            if self.reason not in NO_ANSWER_REASONS:
                raise InvalidValue("reason", "motivo de no_answer desconhecido")
            return True, self.reason
        if outcome == BrainOutcome.BRAND_DISABLED:
            return True, REASON_BRAND_DISABLED
        if outcome == BrainOutcome.INJECTION:
            return False, REASON_INJECTION
        return False, REASON_OUT_OF_SCOPE  # refusal

    async def _injection_alert(self, conn: Any, trace_id: int, user_id: int) -> None:
        await raise_alert(
            conn,
            INJECTION_ALERT_KIND,
            "warn",
            "Tentativa de manipular o assistente detectada",
            {"trace_id": trace_id, "user_id": user_id},
            user_id=user_id,
            dedup_key=f"injection:{user_id}",
            dedup_window=INJECTION_ALERT_WINDOW,
            email=True,
            flags=self.flags_service,
            settings=self.settings,
            clock=self.clock,
        )

    async def _insert_run(
        self, conn: Any, trace_id: int, ticket: QuestionTicket | None, now: Any
    ) -> None:
        await conn.execute(
            _INSERT_RUN, trace_id, ticket.id if ticket else None, self.outcome.value,
            self.path, self.brand, list(self.item_refs), self.best_score, self.llm_calls,
            self.tokens_in, self.tokens_out, self.cost_usd, self.context_turns, self.fidelity,
            list(self.run_flags), self.prompt_version, self.model,
            json.dumps(self.timings_ms), now,
        )  # fmt: skip
