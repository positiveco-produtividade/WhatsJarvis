-- 202 (cerebro, spec 4.11): rastro/custo por mensagem do cerebro e avaliacao unica por ticket.
-- chat.brain_runs NAO guarda texto de pergunta nem de resposta (so desfecho, refs, tokens, custo).
-- Chaves ON DELETE CASCADE/SET NULL para a rotina de retencao (tickets -> saidas -> rastros)
-- continuar funcionando sem alteracao. Idempotente; PostgreSQL 16.
-- GRANTs ficam no runbook (verificacao-externa.md, item 5); aqui nao se criam papeis nem GRANTs.
CREATE TABLE IF NOT EXISTS chat.brain_runs (
  id             bigserial PRIMARY KEY,
  trace_id       bigint NOT NULL UNIQUE REFERENCES chat.message_trace(id) ON DELETE CASCADE,
  ticket_id      bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  outcome        text NOT NULL CHECK (outcome IN ('answered','no_answer','brand_disabled',
                    'access_request','injection','refusal','greeting','ask_brand')),
  path           text NOT NULL CHECK (path IN ('palavras','ia','nenhum')),
  brand          text,
  item_refs      text[] NOT NULL DEFAULT '{}',
  best_score     numeric(4,3),
  llm_calls      smallint NOT NULL DEFAULT 0,
  tokens_in      integer NOT NULL DEFAULT 0,
  tokens_out     integer NOT NULL DEFAULT 0,
  cost_usd       numeric(10,6) NOT NULL DEFAULT 0,
  context_turns  smallint NOT NULL DEFAULT 0,
  fidelity       text CHECK (fidelity IN ('ok','repaired','failed')),
  flags          text[] NOT NULL DEFAULT '{}',
  prompt_version text,
  model          text,
  timings_ms     jsonb NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS brain_runs_month_idx ON chat.brain_runs (created_at) INCLUDE (cost_usd);

CREATE TABLE IF NOT EXISTS chat.answer_ratings (
  id             bigserial PRIMARY KEY,
  ticket_id      bigint NOT NULL UNIQUE REFERENCES chat.tickets(id) ON DELETE CASCADE,
  user_id        bigint NOT NULL REFERENCES access.users(id),
  trace_id       bigint NOT NULL REFERENCES chat.message_trace(id) ON DELETE CASCADE,
  rating         text NOT NULL CHECK (rating IN ('up','down')),
  down_ticket_id bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  rated_at       timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS answer_ratings_time_idx ON chat.answer_ratings (rated_at DESC);

-- um ticket de pergunta por rastro (defesa em profundidade da idempotencia); `media` nao entra
CREATE UNIQUE INDEX IF NOT EXISTS tickets_question_trace_uq ON chat.tickets (trace_id)
  WHERE kind = 'question' AND trace_id IS NOT NULL;
