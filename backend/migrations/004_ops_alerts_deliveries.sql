-- 004: alertas, sequência de tickets, entregas e contadores de segurança (spec 4.2.4). Somente recursos do PostgreSQL 16.

CREATE TABLE IF NOT EXISTS ops.alerts (
  id               bigserial PRIMARY KEY,
  severity         text NOT NULL CHECK (severity IN ('info','warn','high','critical')),
  kind             text NOT NULL,
  message          text NOT NULL,
  payload          jsonb NOT NULL DEFAULT '{}',
  user_id          bigint REFERENCES access.users(id),
  ticket_number    text,
  auto_action      text,
  dedup_key        text,
  acknowledged_at  timestamptz,
  acknowledged_by  text,
  created_at       timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS alerts_created_idx ON ops.alerts (created_at DESC);
CREATE INDEX IF NOT EXISTS alerts_dedup_idx   ON ops.alerts (dedup_key, created_at DESC) WHERE dedup_key IS NOT NULL;

CREATE SEQUENCE IF NOT EXISTS ops.ticket_seq;

CREATE TABLE IF NOT EXISTS ops.deliveries (
  id               bigserial PRIMARY KEY,
  source_type      text NOT NULL CHECK (source_type IN ('alert','ticket')),
  source_id        bigint NOT NULL,
  channel          text NOT NULL CHECK (channel IN ('email','group')),
  status           text NOT NULL CHECK (status IN ('pending','sending','sent','failed','panel_only','skipped')),
  attempts         smallint NOT NULL DEFAULT 0,
  next_attempt_at  timestamptz NOT NULL,
  claimed_at       timestamptz,
  last_error       text,
  created_at       timestamptz NOT NULL,
  sent_at          timestamptz,
  UNIQUE (source_type, source_id, channel),
  CONSTRAINT ticket_never_emails CHECK (NOT (source_type = 'ticket' AND channel = 'email'))
);

CREATE TABLE IF NOT EXISTS ops.security_counters (
  day    date NOT NULL,
  kind   text NOT NULL,
  count  bigint NOT NULL,
  PRIMARY KEY (day, kind)
);
