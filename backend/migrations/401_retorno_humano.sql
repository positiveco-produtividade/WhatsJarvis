-- 401 (retorno-humano.1, spec 9.2): resposta humana pelo painel, acks de entrega e leitura,
-- sugestoes para a FAQ e a origem `sugestao`. Aditiva e idempotente; somente PostgreSQL 16.
-- GRANTs para o papel da aplicacao ficam no checklist de go-live (spec 9.10), como na 202.

-- chat.outbound_messages: dados da resposta humana (RH-7, RH-11) e acks (RH-15).
-- ticket_id/author/author_email/client_token ficam NULL nas saidas do bot.
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS ticket_id bigint;
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS author text;
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS author_email text;
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS client_token uuid;
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
ALTER TABLE chat.outbound_messages ADD COLUMN IF NOT EXISTS read_at timestamptz;

DO $$
BEGIN
  -- A retencao apaga chat.tickets antes de chat.outbound_messages: a FK vira NULL.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'chat.outbound_messages'::regclass AND conname = 'outbound_messages_ticket_fk'
  ) THEN
    ALTER TABLE chat.outbound_messages ADD CONSTRAINT outbound_messages_ticket_fk
      FOREIGN KEY (ticket_id) REFERENCES chat.tickets(id) ON DELETE SET NULL;
  END IF;
  -- Clique duplo ou reenvio do formulario (RH-11): o token vai so na primeira parte.
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'chat.outbound_messages'::regclass
       AND conname = 'outbound_messages_client_token_key'
  ) THEN
    ALTER TABLE chat.outbound_messages ADD CONSTRAINT outbound_messages_client_token_key
      UNIQUE (client_token);
  END IF;
END
$$;

CREATE INDEX IF NOT EXISTS outbound_ticket_idx
  ON chat.outbound_messages (ticket_id, id) WHERE ticket_id IS NOT NULL;

-- Mesmo padrao da 201/301: acha o CHECK de `kind` pelo catalogo e o recria com human_reply.
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
      'brain_answer','brain_answer_rate','brain_notice','brand_poll','human_reply'));
END
$$;

-- content.faq_items.origin ganha `sugestao` (RH-18). Mesmo padrao: CHECK achado pelo catalogo.
DO $$
DECLARE
  con record;
BEGIN
  FOR con IN
    SELECT c.conname
      FROM pg_constraint c
      JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
     WHERE c.conrelid = 'content.faq_items'::regclass
       AND c.contype = 'c'
       AND a.attname = 'origin'
  LOOP
    EXECUTE format('ALTER TABLE content.faq_items DROP CONSTRAINT %I', con.conname);
  END LOOP;

  ALTER TABLE content.faq_items ADD CONSTRAINT faq_items_origin_check
    CHECK (origin IN ('manual','import','seed','nielsen','gmr','sugestao'));
END
$$;

-- Sugestoes de itens da FAQ a partir das respostas humanas (RH-16 a RH-19).
-- Uma por ticket; complementos concatenam a resposta. question/answer ficam NULL apos LGPD.
CREATE TABLE IF NOT EXISTS content.faq_suggestions (
  id            bigserial PRIMARY KEY,
  ticket_id     bigint REFERENCES chat.tickets(id) ON DELETE SET NULL,
  ticket_number text NOT NULL UNIQUE,
  user_id       bigint REFERENCES access.users(id),
  brand         text CHECK (brand IN ('jungle','plant_power','possible','a_tal_da_castanha','zaya')),
  category      text CHECK (category IN ('PRODUTO','PRECO','ARGUMENTO','MATERIAL','CAMPANHA')),
  question      text,
  answer        text,
  status        text NOT NULL DEFAULT 'pendente'
      CHECK (status IN ('pendente','aprovada','descartada')),
  author        text NOT NULL,
  decided_by    text,
  decided_at    timestamptz,
  faq_item_id   bigint REFERENCES content.faq_items(id),
  created_at    timestamptz NOT NULL,
  updated_at    timestamptz NOT NULL,
  CONSTRAINT faq_suggestions_decision_check
    CHECK ((status = 'pendente') = (decided_at IS NULL)),
  CONSTRAINT faq_suggestions_approved_item_check
    CHECK (status <> 'aprovada' OR faq_item_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS faq_suggestions_pending_idx
  ON content.faq_suggestions (created_at DESC) WHERE status = 'pendente';
CREATE INDEX IF NOT EXISTS faq_suggestions_user_idx ON content.faq_suggestions (user_id);
