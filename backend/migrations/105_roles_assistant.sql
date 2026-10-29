-- 105 (painel, spec 4.10): acesso somente leitura do assistente e visoes sem PII. PostgreSQL 16.
-- Regra do projeto (ADR de PF): migrations nunca criam papel. O papel `jarvis_assistant` e criado
-- fora (backend/scripts/dev_db_setup.sql); aqui ficam o schema `assistant`, as visoes e a funcao
-- idempotente assistant.apply_grants(), chamada ao fim desta migration SE o papel existir.
-- Depois de criar o papel, o dono do banco reaplica: SELECT assistant.apply_grants();
-- Fora das visoes: sem chat.message_trace.body, access.unknown_first_messages, telefones/bsuid,
-- ops.audit_log, texto livre de tickets e payload/mensagem de alertas.

CREATE SCHEMA IF NOT EXISTS assistant;

CREATE OR REPLACE VIEW assistant.people AS
SELECT id, name, profile, company, ufs, channels, brands, manager_name, valid_until,
       status, status_changed_at, accepted_at, revoked_at, revoked_reason,
       proactive_opt_out, created_at, updated_at
FROM access.users;

CREATE OR REPLACE VIEW assistant.tickets AS
SELECT id, ticket_number, user_id, kind, brand, classification, confidence, summary,
       escalated, status, urgency, answered_by, answered_at, responsible, created_at, updated_at
FROM chat.tickets;

CREATE OR REPLACE VIEW assistant.messages AS
SELECT id, received_at, msg_type, user_id, access_decision, decision, decision_reason,
       counted_in_limit, reply_kind, reply_sent_at, latency_ms, send_result, ticket_number
FROM chat.message_trace;

CREATE OR REPLACE VIEW assistant.faq AS
SELECT id, brand, category, question, answer, variations, bot_responde, active, origin,
       version, created_at, updated_at
FROM content.faq_items;

CREATE OR REPLACE VIEW assistant.alerts AS
SELECT id, severity, kind, user_id, ticket_number, auto_action, acknowledged_at, created_at
FROM ops.alerts;

-- Idempotente; chamavel pelo dono do banco. Concede so as visoes e revoga todo o resto.
CREATE OR REPLACE FUNCTION assistant.apply_grants() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  s text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_assistant') THEN
    RAISE NOTICE 'papel jarvis_assistant nao existe: grants do assistente nao aplicados';
    RETURN;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO jarvis_assistant', current_database());
  FOREACH s IN ARRAY ARRAY['public','access','chat','ops','content'] LOOP
    EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM jarvis_assistant', s);
    EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM jarvis_assistant', s);
    EXECUTE format('REVOKE ALL ON SCHEMA %I FROM jarvis_assistant', s);
  END LOOP;
  REVOKE ALL ON ALL TABLES IN SCHEMA assistant FROM jarvis_assistant;
  GRANT USAGE ON SCHEMA assistant TO jarvis_assistant;
  GRANT SELECT ON ALL TABLES IN SCHEMA assistant TO jarvis_assistant;
END;
$$;

REVOKE ALL ON SCHEMA assistant FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA assistant FROM PUBLIC;
REVOKE ALL ON FUNCTION assistant.apply_grants() FROM PUBLIC;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_assistant') THEN
    PERFORM assistant.apply_grants();
  ELSE
    RAISE NOTICE 'papel jarvis_assistant ausente: rode backend/scripts/dev_db_setup.sql e depois SELECT assistant.apply_grants()';
  END IF;
END
$$;
