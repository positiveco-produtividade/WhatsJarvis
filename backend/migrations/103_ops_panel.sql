-- 103: tabelas operacionais do painel e indices de auditoria/alertas (spec painel.1 4.3.4).
-- Somente recursos do PostgreSQL 16.

CREATE TABLE IF NOT EXISTS ops.panel_sessions (
  id_hash            text PRIMARY KEY,
  email              text NOT NULL,
  name               text NOT NULL,
  csrf_secret        text NOT NULL,
  created_at         timestamptz NOT NULL,
  last_seen_at       timestamptz NOT NULL,
  last_iam_check_at  timestamptz NOT NULL,
  expires_at         timestamptz NOT NULL,
  revoked_at         timestamptz
);
CREATE INDEX IF NOT EXISTS panel_sessions_exp_idx ON ops.panel_sessions (expires_at);

CREATE TABLE IF NOT EXISTS ops.import_batches (
  id          uuid PRIMARY KEY,
  kind        text NOT NULL CHECK (kind IN ('faq','people')),
  actor       text NOT NULL,
  brand       text,
  filename    text NOT NULL,
  sha256      text NOT NULL,
  size_bytes  integer NOT NULL,
  rows        jsonb NOT NULL,
  summary     jsonb NOT NULL,
  status      text NOT NULL CHECK (status IN ('preview','committed','cancelled','expired')),
  created_at  timestamptz NOT NULL,
  expires_at  timestamptz NOT NULL,
  finished_at timestamptz
);

CREATE TABLE IF NOT EXISTS ops.assistant_questions (
  id          bigserial PRIMARY KEY,
  asked_at    timestamptz NOT NULL,
  asked_by    text NOT NULL,
  agent       text NOT NULL,
  question    text NOT NULL,
  answer      text,
  status      text NOT NULL CHECK (status IN ('ok','error')),
  tools_used  text[] NOT NULL DEFAULT '{}',
  tokens_in   integer,
  tokens_out  integer,
  latency_ms  integer,
  error_code  text
);
CREATE INDEX IF NOT EXISTS assistant_q_idx ON ops.assistant_questions (asked_at DESC);

CREATE INDEX IF NOT EXISTS audit_at_idx     ON ops.audit_log (at DESC);
CREATE INDEX IF NOT EXISTS audit_action_idx ON ops.audit_log (action, at DESC);
CREATE INDEX IF NOT EXISTS audit_target_idx ON ops.audit_log (target_type, target_id);
CREATE INDEX IF NOT EXISTS alerts_unack_idx ON ops.alerts (created_at DESC) WHERE acknowledged_at IS NULL;
