-- 003: schema chat, sem tickets (ficam na 005) (spec 4.2.3). Somente recursos do PostgreSQL 16.

CREATE TABLE IF NOT EXISTS chat.message_trace (
  id                 bigserial PRIMARY KEY,
  wa_message_id      text NOT NULL UNIQUE,
  received_at        timestamptz NOT NULL,
  wa_timestamp       timestamptz,
  msg_type           text NOT NULL,
  user_id            bigint REFERENCES access.users(id),
  attempt_id         bigint REFERENCES access.access_attempts(id),
  access_decision    text NOT NULL,
  decision           text NOT NULL,
  decision_reason    text,
  counted_in_limit   boolean NOT NULL DEFAULT false,
  body               text,
  reply_kind         text NOT NULL DEFAULT 'none',
  reply_wa_message_id text,
  reply_sent_at      timestamptz,
  latency_ms         integer,
  send_result        text,
  send_error         text,
  ticket_number      text,
  CONSTRAINT has_sender CHECK (user_id IS NOT NULL OR attempt_id IS NOT NULL OR access_decision = 'error')
);
CREATE INDEX IF NOT EXISTS message_trace_user_time_idx ON chat.message_trace (user_id, received_at DESC);
CREATE INDEX IF NOT EXISTS message_trace_limit_idx     ON chat.message_trace (user_id, received_at) WHERE counted_in_limit;
CREATE INDEX IF NOT EXISTS message_trace_received_idx  ON chat.message_trace (received_at);

CREATE TABLE IF NOT EXISTS chat.outbound_messages (
  id                bigserial PRIMARY KEY,
  user_id           bigint NOT NULL REFERENCES access.users(id),
  to_phone          text NOT NULL,
  kind              text NOT NULL CHECK (kind IN ('welcome_template','optin_reminder','optin_confirmation',
        'optin_declined','under_construction','rate_limit_notice','media_ack','opt_out_confirmation')),
  required_status   text NOT NULL,
  body              text,
  template          jsonb,
  inbound_trace_id  bigint REFERENCES chat.message_trace(id),
  optin_request_id  bigint REFERENCES access.optin_requests(id),
  status            text NOT NULL CHECK (status IN ('pending','sending','sent','failed','discarded','unknown')),
  attempts          smallint NOT NULL DEFAULT 0,
  next_attempt_at   timestamptz NOT NULL,
  claimed_at        timestamptz,
  wa_message_id     text UNIQUE,
  error_code        text,
  error_subcode     text,
  discard_reason    text,
  created_at        timestamptz NOT NULL,
  sent_at           timestamptz
);
CREATE INDEX IF NOT EXISTS outbound_due_idx ON chat.outbound_messages (next_attempt_at) WHERE status = 'pending';
