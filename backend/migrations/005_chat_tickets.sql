-- 005: chat.tickets (entidade única de ticket, R1) e ops.v_tickets (spec 4.2.3). Somente recursos do PostgreSQL 16.
-- O painel (102) acrescenta search_key e índices por marca/status; nada daquilo é criado aqui.

CREATE TABLE IF NOT EXISTS chat.tickets (
  id                bigserial PRIMARY KEY,
  ticket_number     text NOT NULL UNIQUE,
  user_id           bigint NOT NULL REFERENCES access.users(id),
  trace_id          bigint REFERENCES chat.message_trace(id),
  kind              text NOT NULL CHECK (kind IN ('question','media')),
  brand             text CHECK (brand IN ('jungle','plant_power','possible','a_tal_da_castanha','zaya')),
  classification    text NOT NULL DEFAULT 'NAO_IDENTIFICADO'
      CHECK (classification IN ('PRODUTO','PRECO','ARGUMENTO','MATERIAL','CAMPANHA','MIDIA','NAO_IDENTIFICADO')),
  confidence        numeric(4,3),
  message           text,
  summary           text,
  escalation_reason text,
  escalated         boolean NOT NULL DEFAULT false,
  status            text NOT NULL CHECK (status IN ('answered_bot','answered_human','awaiting','send_error')),
  urgency           text CHECK (urgency IN ('muito_urgente','urgente','pouco_urgente')),
  answered_by       text,
  answered_at       timestamptz,
  responsible       text,
  meta              jsonb NOT NULL DEFAULT '{}',
  created_at        timestamptz NOT NULL,
  updated_at        timestamptz NOT NULL,
  CONSTRAINT escalated_needs_urgency CHECK (NOT escalated OR urgency IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS tickets_created_idx  ON chat.tickets (created_at DESC);
CREATE INDEX IF NOT EXISTS tickets_user_idx     ON chat.tickets (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS tickets_awaiting_idx ON chat.tickets (classification, urgency, created_at) WHERE status = 'awaiting';

CREATE OR REPLACE VIEW ops.v_tickets AS SELECT * FROM chat.tickets;
