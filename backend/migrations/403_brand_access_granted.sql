-- 403 (cadastro.1, spec 8.3): kind `brand_access_granted` em chat.outbound_messages, o aviso
-- "Seu acesso a {marca} foi liberado" quando o admin da ao vendedor uma marca pedida (CA-11).
-- Mesmo padrao da 201/301/401: acha o CHECK de `kind` pelo catalogo e o recria.
-- Aditiva e idempotente; somente PostgreSQL 16. Sem tabela nova: as permissoes do jarvis_app
-- nao mudam (porta-fechada.2).

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
      'brain_answer','brain_answer_rate','brain_notice','brand_poll','human_reply',
      'brand_access_granted'));
END
$$;
