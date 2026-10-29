-- 002: schema access (spec 4.2.2). Somente recursos do PostgreSQL 16.

CREATE TABLE IF NOT EXISTS access.users (
  id                      bigserial PRIMARY KEY,
  name                    text NOT NULL,
  phone_e164              text NOT NULL,
  phone_key               text NOT NULL UNIQUE,
  bsuid                   text UNIQUE,
  profile                 text NOT NULL CHECK (profile IN
        ('vendedor','gestor_comercial','executivo','parceiro_externo','administrador')),
  company                 text NOT NULL,
  ufs                     text[] NOT NULL CHECK (cardinality(ufs) > 0),
  channels                text[] NOT NULL CHECK (cardinality(channels) > 0),
  brands                  text[] NOT NULL CHECK (cardinality(brands) > 0),
  manager_name            text NOT NULL,
  valid_until             timestamptz,
  status                  text NOT NULL CHECK (status IN ('pending_optin','active','declined','revoked')),
  status_changed_at       timestamptz NOT NULL,
  accepted_at             timestamptz,
  accepted_notice_version text,
  declined_at             timestamptz,
  revoked_at              timestamptz,
  revoked_by              text,
  revoked_reason          text CHECK (revoked_reason IN ('left_company','number_change',
        'partner_contract_end','monthly_reconciliation','declined_notice','other')),
  proactive_opt_out       boolean NOT NULL DEFAULT false,
  proactive_opt_out_at    timestamptz,
  optin_reminder_at       timestamptz,
  rate_limit_notice_at    timestamptz,
  created_at              timestamptz NOT NULL,
  created_by              text NOT NULL,
  updated_at              timestamptz NOT NULL,
  CONSTRAINT active_needs_acceptance CHECK (status <> 'active'
        OR (accepted_at IS NOT NULL AND accepted_notice_version IS NOT NULL)),
  CONSTRAINT revoked_needs_author CHECK (status <> 'revoked'
        OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)),
  CONSTRAINT opt_out_consistent CHECK (proactive_opt_out = (proactive_opt_out_at IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS users_status_idx ON access.users (status);

CREATE TABLE IF NOT EXISTS access.optin_requests (
  id              bigserial PRIMARY KEY,
  user_id         bigint NOT NULL REFERENCES access.users(id),
  status          text NOT NULL CHECK (status IN ('open','accepted','declined','superseded')),
  notice_version  text NOT NULL,
  issued_at       timestamptz NOT NULL,
  issued_by       text NOT NULL,
  responded_at    timestamptz,
  responded_wa_message_id text
);
CREATE UNIQUE INDEX IF NOT EXISTS optin_one_open_per_user
  ON access.optin_requests (user_id) WHERE status = 'open';

CREATE TABLE IF NOT EXISTS access.access_attempts (
  id               bigserial PRIMARY KEY,
  phone_key        text UNIQUE,
  bsuid            text UNIQUE,
  user_id          bigint REFERENCES access.users(id),
  reason           text NOT NULL CHECK (reason IN ('unknown','revoked','declined','identity_conflict')),
  attempt_count    integer NOT NULL CHECK (attempt_count >= 1),
  first_attempt_at timestamptz NOT NULL,
  last_attempt_at  timestamptz NOT NULL,
  first_msg_type   text NOT NULL,
  first_msg_chars  integer,
  first_msg_mime   text,
  dismissed_at     timestamptz,
  CONSTRAINT has_identifier CHECK (phone_key IS NOT NULL OR bsuid IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS access.unknown_first_messages (
  attempt_id bigint PRIMARY KEY REFERENCES access.access_attempts(id) ON DELETE CASCADE,
  body       text NOT NULL,
  stored_at  timestamptz NOT NULL
);
