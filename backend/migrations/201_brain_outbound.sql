-- 201 (cerebro, spec 4.11): novos kinds de saida e botoes de resposta em chat.outbound_messages.
-- Acha o CHECK antigo de `kind` em pg_constraint (sem supor o nome gerado), recria como
-- outbound_messages_kind_check com os kinds existentes + brain_answer, brain_answer_rate,
-- brain_notice e acrescenta reply_buttons (jsonb, nulo). Idempotente; PostgreSQL 16.
DO $$
DECLARE
  con record;
BEGIN
  FOR con IN
    SELECT c.conname
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
     WHERE c.conrelid = 'chat.outbound_messages'::regclass
       AND c.contype = 'c'
       AND a.attname = 'kind'
  LOOP
    EXECUTE format('ALTER TABLE chat.outbound_messages DROP CONSTRAINT %I', con.conname);
  END LOOP;

  ALTER TABLE chat.outbound_messages ADD CONSTRAINT outbound_messages_kind_check
    CHECK (kind IN ('welcome_template','optin_reminder','optin_confirmation','optin_declined',
      'under_construction','rate_limit_notice','media_ack','opt_out_confirmation',
      'brain_answer','brain_answer_rate','brain_notice'));
END
$$;

ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS reply_buttons jsonb;
