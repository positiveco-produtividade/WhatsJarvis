"""Pipeline por mensagem (spec 4.4.1 passos 5 e 6, 8; tabelas 4.4.2 e 4.4.5).

Implementa o `PayloadProcessor` do webhook. `handle_message` roda em UMA transacao por
mensagem: rastro (idempotencia) -> identidade (trava a linha) -> decisao de acesso ->
tratador da decisao -> rastro final -> COMMIT. Tratadores: `unknown`, `declined`,
`revoked` e `identity_conflict` (silencio + tentativa; so o conflito alerta);
`pending_optin`/`optin_*` (aceite, recusa, lembrete); usuario `active` (limite de 60/h,
"sair", texto, midia, tipo ignorado). Nada externo acontece antes do COMMIT: o tratador
so grava linhas na fila de saida/entregas e devolve os ids no `MessageOutcome`.
Decisoes sem tratador caem em `_pending_hook` (`NotImplementedError` explicito).

Erros: transitorio de banco sobe como `TransientDbError` (webhook responde 503); erro
nao transitorio vira log ERROR + rastro `error` em transacao separada, e a mensagem
devolve um resultado vazio (200, Q22). Nenhuma excecao daqui carrega texto de mensagem,
telefone ou BSUID; o log traz so a classe do erro.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.core.clock import Clock, SystemClock
from app.core.errors import TransientDbError
from app.core.values import (
    AccessDecision,
    AttemptReason,
    Decision,
    MessageOutcome,
    OutboundKind,
    ReplyKind,
    SendResult,
    parse_optin_payload,
    parse_rating_payload,
)
from app.db.connection import is_transient, transaction, translate_db_errors
from app.services import (
    access,
    alerts,
    answering,
    attempts,
    audit,
    identity,
    limits,
    optin,
    outbox,
    ratings,
    tickets,
    trace,
)
from app.services.flags import Flags
from app.services.inbound import InboundMessage, InboundStatus

logger = logging.getLogger(__name__)

IDENTITY_CONFLICT_DEDUP = timedelta(hours=24)
RATE_LIMIT_ALERT_DEDUP = timedelta(hours=1)
OPT_OUT_REASON = "proactive_opt_out"
_IGNORED_STATUSES = frozenset({"sent", "delivered", "read"})

_FAIL_OUTBOUND = (
    "UPDATE chat.outbound_messages SET status = 'failed', error_code = $2::text "
    "WHERE wa_message_id = $1::text AND status <> 'failed' RETURNING inbound_trace_id"
)
_LOAD_USER = (
    "SELECT id, name, phone_e164, profile, ufs, channels, brands, rate_limit_notice_at "
    "FROM access.users WHERE id = $1::bigint"
)
_SET_OPT_OUT = (
    "UPDATE access.users SET proactive_opt_out = true, "
    "proactive_opt_out_at = COALESCE(proactive_opt_out_at, $2), updated_at = $2 "
    "WHERE id = $1::bigint"
)
_SET_LIMIT_NOTICE = (
    "UPDATE access.users SET rate_limit_notice_at = $2, updated_at = $2 WHERE id = $1::bigint"
)
_DELIVERIES_OF = (
    "SELECT id FROM ops.deliveries WHERE source_type = $1::text "
    "AND source_id = $2::bigint ORDER BY id"
)
_TRACE_BY_REPLY = (
    "SELECT id FROM chat.message_trace WHERE reply_wa_message_id = $1::text LIMIT 1"
)


@dataclass(frozen=True)
class MessageContext:
    """Tudo que um tratador precisa de uma mensagem em curso (na transacao dela)."""

    conn: Any
    msg: InboundMessage
    trace_id: int
    ident: identity.Identity
    decision: AccessDecision
    now: datetime


Handler = Callable[[MessageContext], Awaitable[MessageOutcome]]


@dataclass(frozen=True)
class _User:
    """Usuario como o opt-in, a fila de saida e o cerebro o enxergam (linha ja travada)."""

    id: int
    name: str
    phone_e164: str
    profile: str
    ufs: list[str]
    channels: list[str]
    brands: list[str]
    rate_limit_notice_at: datetime | None


def _wa_datetime(timestamp: int | None) -> datetime | None:
    if timestamp is None:
        return None
    try:
        return datetime.fromtimestamp(timestamp, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _conflict_dedup_key(ident: identity.Identity) -> str:
    """Chave por BSUID (ou, sem ele, pelo telefone), em hash: o alerta nao guarda PII."""
    raw = ident.bsuid or ident.phone_key or ""
    return "identity_conflict:" + hashlib.sha256(raw.encode()).hexdigest()[:16]


class Pipeline:
    """`PayloadProcessor` do webhook."""

    def __init__(
        self,
        pool: Any,
        settings: Settings,
        flags: Flags,
        clock: Clock | None = None,
    ) -> None:
        self._pool = pool
        self._settings = settings
        self._flags = flags
        self._clock: Clock = clock or SystemClock()
        # Ganchos das proximas tarefas: basta acrescentar a decisao aqui.
        self._handlers: dict[AccessDecision, Handler] = {
            AccessDecision.UNKNOWN: self._on_unknown,
            AccessDecision.DECLINED: self._on_declined,
            AccessDecision.REVOKED: self._on_revoked,
            AccessDecision.IDENTITY_CONFLICT: self._on_conflict,
            AccessDecision.PENDING_OPTIN: self._on_pending_optin,
            AccessDecision.OPTIN_ACCEPTED: self._on_pending_optin,
            AccessDecision.OPTIN_DECLINED: self._on_pending_optin,
            AccessDecision.ALLOWED: self._on_active,
            AccessDecision.ESCALATED_MEDIA: self._on_active,
            AccessDecision.IGNORED_TYPE: self._on_active,
        }

    # ---- mensagens ----------------------------------------------------------------

    async def handle_message(self, msg: InboundMessage) -> MessageOutcome:
        try:
            with translate_db_errors():
                return await self._process(msg)
        except TransientDbError:
            raise
        except Exception as exc:
            if is_transient(exc):
                raise TransientDbError(type(exc).__name__) from exc
            await self._record_failure(msg, exc)
            return MessageOutcome()

    async def _process(self, msg: InboundMessage) -> MessageOutcome:
        async with self._pool.acquire() as conn, transaction(conn):
            trace_id = await trace.begin(
                conn,
                msg.wa_message_id,
                msg_type=msg.kind.value,
                wa_timestamp=_wa_datetime(msg.timestamp),
                clock=self._clock,
            )
            if trace_id is None:
                logger.info("message_duplicate", extra={"msg_type": msg.kind.value})
                return MessageOutcome()
            ident = await identity.resolve(conn, msg.sender)
            now = self._clock.now()
            decision = access.decide(ident.state, msg, now)
            ctx = MessageContext(conn, msg, trace_id, ident, decision, now)
            outcome = await self._handlers.get(decision, self._pending_hook)(ctx)
            await trace.ensure_finalized(conn, trace_id)
            return outcome

    async def _record_failure(self, msg: InboundMessage, exc: Exception) -> None:
        error_class = type(exc).__name__
        logger.error(
            "message_processing_error",
            extra={"error_class": error_class, "msg_type": msg.kind.value},
        )
        try:
            await trace.mark_error(
                self._pool,
                msg.wa_message_id,
                msg_type=msg.kind.value,
                reason=error_class,
                clock=self._clock,
            )
        except Exception as mark_exc:
            if is_transient(mark_exc):
                raise TransientDbError(type(mark_exc).__name__) from mark_exc
            logger.error("trace_mark_error_failed", extra={"error_class": type(mark_exc).__name__})

    async def _pending_hook(self, ctx: MessageContext) -> MessageOutcome:
        """Decisao sem tratador registrado: falha alto (rastro `error`)."""
        raise NotImplementedError(f"tratador pendente: {ctx.decision.value}")

    # ---- tratadores de silencio ---------------------------------------------------

    async def _record_attempt(
        self, ctx: MessageContext, reason: AttemptReason
    ) -> attempts.AttemptResult | None:
        ident = ctx.ident
        if ident.phone_key is None and ident.bsuid is None:
            return None  # sem identificador nao ha como registrar tentativa
        return await attempts.record(
            ctx.conn,
            phone_key=ident.phone_key,
            bsuid=ident.bsuid,
            reason=reason.value,
            msg_type=ctx.msg.kind.value,
            now=ctx.now,
            text=ctx.msg.text,
            mime=ctx.msg.mime_type,
            user_id=ident.user_id,
            store_first_text=self._settings.unknown_first_text_store,
        )

    async def _silent(
        self, ctx: MessageContext, reason: AttemptReason, decision: AccessDecision
    ) -> attempts.AttemptResult | None:
        """Tentativa + rastro final, sem resposta, alerta nem entrega."""
        attempt = await self._record_attempt(ctx, reason)
        if attempt is None:
            await trace.finalize(
                ctx.conn,
                ctx.trace_id,
                access_decision=AccessDecision.ERROR,
                decision=Decision.ERROR,
                decision_reason="sender_missing",
            )
            logger.info("message_decision", extra={"decision": "error", "reason": "sender_missing"})
            return None
        await trace.finalize(
            ctx.conn,
            ctx.trace_id,
            access_decision=decision,
            decision=Decision(decision.value),
            user_id=ctx.ident.user_id,
            attempt_id=attempt.attempt_id,
        )
        logger.info(
            "message_decision",
            extra={
                "decision": decision.value,
                "user_id": ctx.ident.user_id,
                "attempt_id": attempt.attempt_id,
                "msg_type": ctx.msg.kind.value,
            },
        )
        return attempt

    async def _on_unknown(self, ctx: MessageContext) -> MessageOutcome:
        await self._silent(ctx, AttemptReason.UNKNOWN, AccessDecision.UNKNOWN)
        return MessageOutcome()

    async def _on_declined(self, ctx: MessageContext) -> MessageOutcome:
        await self._silent(ctx, AttemptReason.DECLINED, AccessDecision.DECLINED)
        return MessageOutcome()

    async def _on_revoked(self, ctx: MessageContext) -> MessageOutcome:
        await self._silent(ctx, AttemptReason.REVOKED, AccessDecision.REVOKED)
        return MessageOutcome()

    async def _on_conflict(self, ctx: MessageContext) -> MessageOutcome:
        attempt = await self._silent(
            ctx, AttemptReason.IDENTITY_CONFLICT, AccessDecision.IDENTITY_CONFLICT
        )
        payload: dict[str, Any] = {
            "conflict_user_ids": list(ctx.ident.conflict_user_ids),
            "input_type": ctx.msg.kind.value,
        }
        if attempt is not None:
            payload["attempt_id"] = attempt.attempt_id
        alert_id = await alerts.raise_alert(
            ctx.conn,
            "identity_conflict",
            "high",
            "Conflito de identidade: remetente com telefone e BSUID de cadastros diferentes.",
            payload,
            dedup_key=_conflict_dedup_key(ctx.ident),
            dedup_window=IDENTITY_CONFLICT_DEDUP,
            flags=self._flags,
            settings=self._settings,
            clock=self._clock,
        )
        delivery_ids = await self._alert_delivery_ids(ctx.conn, alert_id)
        return MessageOutcome(delivery_ids=delivery_ids)

    @staticmethod
    async def _delivery_ids(conn: Any, source_type: str, source_id: int) -> tuple[int, ...]:
        rows = await conn.fetch(_DELIVERIES_OF, source_type, source_id)
        return tuple(int(r["id"]) for r in rows)

    async def _alert_delivery_ids(self, conn: Any, alert_id: int | None) -> tuple[int, ...]:
        if alert_id is None:  # suprimido pelo dedup: nada novo a despachar
            return ()
        return await self._delivery_ids(conn, "alert", alert_id)

    # ---- opt-in (pending_optin) -----------------------------------------------------

    async def _load_user(self, ctx: MessageContext) -> _User:
        row = await ctx.conn.fetchrow(_LOAD_USER, ctx.ident.user_id)
        if row is None:  # a linha foi travada na identidade: nao deveria sumir
            raise LookupError("usuario ausente")
        return _User(
            id=int(row["id"]),
            name=row["name"],
            phone_e164=row["phone_e164"],
            profile=row["profile"],
            ufs=list(row["ufs"]),
            channels=list(row["channels"]),
            brands=list(row["brands"]),
            rate_limit_notice_at=row["rate_limit_notice_at"],
        )

    async def _finalize(
        self,
        ctx: MessageContext,
        decision: AccessDecision,
        *,
        reply_kind: ReplyKind = ReplyKind.NONE,
        reason: str | None = None,
        counted: bool = False,
        body: str | None = None,
        ticket_number: str | None = None,
    ) -> None:
        await trace.finalize(
            ctx.conn,
            ctx.trace_id,
            access_decision=decision,
            decision=Decision(decision.value),
            user_id=ctx.ident.user_id,
            decision_reason=reason,
            counted_in_limit=counted,
            body=body,
            reply_kind=reply_kind,
            ticket_number=ticket_number,
        )
        logger.info(
            "message_decision",
            extra={
                "decision": decision.value,
                "user_id": ctx.ident.user_id,
                "msg_type": ctx.msg.kind.value,
                "reply_kind": reply_kind.value,
            },
        )

    async def _on_pending_optin(self, ctx: MessageContext) -> MessageOutcome:
        """Aceito/Nao aceito do aviso aberto; qualquer outro conteudo = lembrete 1/24h."""
        user = await self._load_user(ctx)
        parsed = None
        if ctx.msg.button is not None:
            parsed = parse_optin_payload(ctx.msg.button.payload)
        if ctx.decision is AccessDecision.OPTIN_ACCEPTED and parsed is not None:
            return await self._answer_notice(ctx, user, accepted=True, notice_id=parsed[1])
        if ctx.decision is AccessDecision.OPTIN_DECLINED and parsed is not None:
            return await self._answer_notice(ctx, user, accepted=False, notice_id=parsed[1])
        return await self._remind(ctx, user)

    async def _answer_notice(
        self, ctx: MessageContext, user: _User, *, accepted: bool, notice_id: int
    ) -> MessageOutcome:
        common: dict[str, Any] = {
            "settings": self._settings,
            "wa_message_id": ctx.msg.wa_message_id,
            "inbound_trace_id": ctx.trace_id,
            "clock": self._clock,
        }
        if accepted:
            out_id = await optin.accept(
                ctx.conn, user, notice_id, ctx.ident.bsuid, ctx.now, **common
            )
            kind = ReplyKind.OPTIN_CONFIRMATION
        else:
            out_id = await optin.decline(ctx.conn, user, notice_id, ctx.now, **common)
            kind = ReplyKind.OPTIN_DECLINED
        if out_id is None:  # toque que nao vale (aviso fechado entre a decisao e agora)
            return await self._remind(ctx, user)
        await self._finalize(ctx, ctx.decision, reply_kind=kind)
        return MessageOutcome(outbound_ids=(out_id,))

    async def _remind(self, ctx: MessageContext, user: _User) -> MessageOutcome:
        out_id = await optin.maybe_remind(
            ctx.conn,
            user,
            ctx.now,
            settings=self._settings,
            inbound_trace_id=ctx.trace_id,
            clock=self._clock,
        )
        kind = ReplyKind.OPTIN_REMINDER if out_id is not None else ReplyKind.NONE
        await self._finalize(ctx, AccessDecision.PENDING_OPTIN, reply_kind=kind)
        return MessageOutcome(outbound_ids=() if out_id is None else (out_id,))

    # ---- usuario ativo --------------------------------------------------------------

    async def _on_active(self, ctx: MessageContext) -> MessageOutcome:
        """Limite de 60/h (4.4.5) e depois o tratador por conteudo (4.4.2)."""
        if ctx.decision is AccessDecision.IGNORED_TYPE:
            # Reacao/sistema/nao suportado: so rastro e fora do limite de 60/h
            # (decisao do Caio, 2026-10-26).
            return await self._ignored_or_rating(ctx)
        user = await self._load_user(ctx)
        result = await limits.check_and_count(
            ctx.conn,
            user.id,
            ctx.now,
            limit=self._settings.rate_limit_per_hour,
            window_minutes=self._settings.rate_limit_window_minutes,
        )
        if not result.allowed:
            return await self._rate_limited(ctx, user, result.count)
        if ctx.decision is AccessDecision.ESCALATED_MEDIA:
            return await self._media(ctx, user)
        if access.is_opt_out_text(ctx.msg):
            return await self._opt_out(ctx, user)
        return await self._text(ctx, user)

    async def _ignored_or_rating(self, ctx: MessageContext) -> MessageOutcome:
        """Toque de avaliacao vale mesmo no limite de 60/h e nao conta nele; o resto, so rastro."""
        button = ctx.msg.button
        if button is not None and ctx.ident.user_id is not None:
            if parse_rating_payload(button.payload) is not None:
                result = await ratings.handle_rating(
                    ctx.conn,
                    user_id=ctx.ident.user_id,
                    payload=button.payload,
                    trace_id=ctx.trace_id,
                    clock=self._clock,
                    settings=self._settings,
                    flags=self._flags,
                )
                if result.recorded:
                    await self._finalize(
                        ctx,
                        AccessDecision.IGNORED_TYPE,
                        reason="rating_up" if result.useful else "rating_down",
                    )
                    return MessageOutcome(outbound_ids=result.outbound_ids)
        await self._finalize(ctx, AccessDecision.IGNORED_TYPE, counted=False)
        return MessageOutcome()

    async def _rate_limited(self, ctx: MessageContext, user: _User, count: int) -> MessageOutcome:
        """Bloqueada (nao conta na janela): um aviso por janela e um alerta por hora."""
        out_ids: tuple[int, ...] = ()
        kind = ReplyKind.NONE
        if limits.rate_limit_notice_due(
            user.rate_limit_notice_at,
            ctx.now,
            window_minutes=self._settings.rate_limit_window_minutes,
        ):
            out_id = await outbox.enqueue(
                ctx.conn,
                user,
                OutboundKind.RATE_LIMIT_NOTICE,
                body=self._settings.text_rate_limit,
                inbound_trace_id=ctx.trace_id,
                clock=self._clock,
            )
            await ctx.conn.execute(_SET_LIMIT_NOTICE, user.id, ctx.now)
            out_ids, kind = (out_id,), ReplyKind.RATE_LIMIT_NOTICE
        await self._finalize(ctx, AccessDecision.RATE_LIMITED, reply_kind=kind)
        alert_id = await alerts.raise_alert(
            ctx.conn,
            "rate_limit",
            "warn",
            "Usuário atingiu o limite de mensagens por hora.",
            {"limit": self._settings.rate_limit_per_hour, "count_in_window": count},
            user_id=user.id,
            dedup_key=f"rate_limit:{user.id}",
            dedup_window=RATE_LIMIT_ALERT_DEDUP,
            flags=self._flags,
            settings=self._settings,
            clock=self._clock,
        )
        deliveries = await self._alert_delivery_ids(ctx.conn, alert_id)
        return MessageOutcome(outbound_ids=out_ids, delivery_ids=deliveries)

    async def _opt_out(self, ctx: MessageContext, user: _User) -> MessageOutcome:
        await ctx.conn.execute(_SET_OPT_OUT, user.id, ctx.now)
        await audit.write(
            ctx.conn, optin.ACTOR_TYPE, optin.ACTOR, "user.proactive_opt_out", "user",
            str(user.id), new={"proactive_opt_out": True}, clock=self._clock,
        )  # fmt: skip
        out_id = await outbox.enqueue(
            ctx.conn,
            user,
            OutboundKind.OPT_OUT_CONFIRMATION,
            body=self._settings.text_opt_out_confirmed,
            inbound_trace_id=ctx.trace_id,
            clock=self._clock,
        )
        await self._finalize(
            ctx,
            AccessDecision.ALLOWED,
            reply_kind=ReplyKind.OPT_OUT_CONFIRMATION,
            reason=OPT_OUT_REASON,
            counted=True,
            body=ctx.msg.text,
        )
        return MessageOutcome(outbound_ids=(out_id,))

    async def _text(self, ctx: MessageContext, user: _User) -> MessageOutcome:
        """Texto autorizado: corpo guardado, resposta `pending` e pedido ao cerebro."""
        text = ctx.msg.text or ""
        await self._finalize(
            ctx, AccessDecision.ALLOWED, reply_kind=ReplyKind.PENDING, counted=True, body=text
        )
        context = answering.AnswerContext(
            user_id=user.id,
            profile=user.profile,
            ufs=user.ufs,
            channels=user.channels,
            brands=user.brands,
            text=text,
            inbound_wa_message_id=ctx.msg.wa_message_id,
            received_at=ctx.now,
            trace_id=ctx.trace_id,
        )
        request = answering.AnswerRequest(
            trace_id=ctx.trace_id, phone_e164=user.phone_e164, context=context
        )
        return MessageOutcome(answer_request=request)

    async def _media(self, ctx: MessageContext, user: _User) -> MessageOutcome:
        """Midia de usuario ativo: ticket + "equipe avisada"; sem alerta, sem e-mail."""
        ticket = await tickets.create_media_ticket(
            ctx.conn,
            user.id,
            ctx.trace_id,
            ctx.msg.kind.value,
            flags=self._flags,
            clock=self._clock,
        )
        out_ids: tuple[int, ...] = ()
        kind = ReplyKind.NONE
        if self._settings.media_ack_enabled:
            out_id = await outbox.enqueue(
                ctx.conn,
                user,
                OutboundKind.MEDIA_ACK,
                body=self._settings.text_media_ack,
                inbound_trace_id=ctx.trace_id,
                clock=self._clock,
            )
            out_ids, kind = (out_id,), ReplyKind.MEDIA_ACK
        await self._finalize(
            ctx,
            AccessDecision.ESCALATED_MEDIA,
            reply_kind=kind,
            counted=True,
            ticket_number=ticket.ticket_number,
        )
        deliveries = await self._delivery_ids(ctx.conn, "ticket", ticket.id)
        return MessageOutcome(outbound_ids=out_ids, delivery_ids=deliveries)

    # ---- statuses -----------------------------------------------------------------

    async def handle_status(self, st: InboundStatus) -> MessageOutcome | None:
        """Ignora sent/delivered/read; `failed` marca a saida e o rastro de origem."""
        if st.status in _IGNORED_STATUSES or not st.is_failed:
            return None
        try:
            with translate_db_errors():
                async with self._pool.acquire() as conn, transaction(conn):
                    await self._apply_failed(conn, st)
        except TransientDbError:
            raise
        except Exception as exc:
            if is_transient(exc):
                raise TransientDbError(type(exc).__name__) from exc
            logger.error("status_processing_error", extra={"error_class": type(exc).__name__})
        return None

    async def _apply_failed(self, conn: Any, st: InboundStatus) -> None:
        error = None if st.error_code is None else str(st.error_code)
        rows = await conn.fetch(_FAIL_OUTBOUND, st.wa_message_id, error)
        trace_ids = {int(r["inbound_trace_id"]) for r in rows if r["inbound_trace_id"] is not None}
        if not rows:  # resposta enviada fora da fila de saida: casa pelo rastro
            found = await conn.fetchval(_TRACE_BY_REPLY, st.wa_message_id)
            if found is not None:
                trace_ids.add(int(found))
        for trace_id in sorted(trace_ids):
            await trace.record_send_result(
                conn,
                trace_id,
                send_result=SendResult.FAILED,
                decision=Decision.SEND_FAILED,
                send_error=error,
            )
