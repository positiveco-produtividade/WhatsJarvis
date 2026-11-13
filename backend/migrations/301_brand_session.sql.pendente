-- 301 (canal-waha.1, spec 9.2): marca da conversa em access.users e o kind brand_poll
-- em chat.outbound_messages. Aditiva e idempotente; somente recursos do PostgreSQL 16.
-- active_brand: marca escolhida pelo vendedor (enquete ou nome escrito); active_brand_at:
-- ultima mensagem dele com essa marca. A validade (BRAND_SESSION_MINUTES) e calculada na
-- leitura. Nao e dado pessoal: a exclusao LGPD ja trata a linha do usuario inteira.

ALTER TABLE access.users ADD COLUMN IF NOT EXISTS active_brand text;
ALTER TABLE access.users ADD COLUMN IF NOT EXISTS active_brand_at timestamptz;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'access.users'::regclass AND conname = 'users_active_brand_check'
  ) THEN
    ALTER TABLE access.users ADD CONSTRAINT users_active_brand_check
      CHECK (active_brand IS NULL
             OR active_brand IN ('jungle','plant_power','possible','a_tal_da_castanha','zaya'));
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'access.users'::regclass AND conname = 'users_active_brand_pair_check'
  ) THEN
    ALTER TABLE access.users ADD CONSTRAINT users_active_brand_pair_check
      CHECK ((active_brand IS NULL) = (active_brand_at IS NULL));
  END IF;
END
$$;

-- Mesmo padrao da 201: acha o CHECK de `kind` pelo catalogo (sem supor o nome) e o recria
-- com os kinds existentes + brand_poll.
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
      'brain_answer','brain_answer_rate','brain_notice','brand_poll'));
END
$$;
