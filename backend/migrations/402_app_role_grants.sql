-- porta-fechada.2 (PA-T01, spec 7.1): permissoes do papel da aplicacao `jarvis_app`.
-- Em producao o app conecta como jarvis_app (DATABASE_URL) e o runner como o dono
-- (MIGRATION_DATABASE_URL). A funcao abaixo e idempotente e o runner a reaplica ao fim de
-- cada `migrate` (D1), entao tabelas de migrations futuras ganham as permissoes sozinhas.
-- SECURITY INVOKER: so funciona chamada pelo dono das tabelas. Sem o papel: so um NOTICE.

CREATE OR REPLACE FUNCTION ops.apply_app_grants() RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  s text;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_app') THEN
    RAISE NOTICE 'papel jarvis_app nao existe: permissoes do app nao aplicadas';
    RETURN;
  END IF;

  EXECUTE format('GRANT CONNECT ON DATABASE %I TO jarvis_app', current_database());
  -- Nada em public (PA-7). No PG 16 PUBLIC ja nao cria em public; reforca em bancos antigos.
  REVOKE ALL ON SCHEMA public FROM jarvis_app;
  REVOKE CREATE ON SCHEMA public FROM PUBLIC;

  FOREACH s IN ARRAY ARRAY['access','chat','ops','content'] LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA %I TO jarvis_app', s);
    EXECUTE format('REVOKE CREATE ON SCHEMA %I FROM jarvis_app', s);
    EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO jarvis_app', s);
    EXECUTE format('REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA %I FROM jarvis_app', s);
    EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO jarvis_app', s);
  END LOOP;

  -- Auditoria: so inserir e ler (PA-5). O gatilho audit_log_no_update_delete continua.
  REVOKE UPDATE, DELETE ON ops.audit_log FROM jarvis_app;
  -- Controle de migrations: so ler, para o /health (PA-6).
  REVOKE INSERT, UPDATE, DELETE ON ops.schema_migrations FROM jarvis_app;

  -- Nada no schema do assistente (PA-8).
  REVOKE ALL ON ALL TABLES IN SCHEMA assistant FROM jarvis_app;
  REVOKE ALL ON SCHEMA assistant FROM jarvis_app;
END;
$$;

REVOKE ALL ON FUNCTION ops.apply_app_grants() FROM PUBLIC;

SELECT ops.apply_app_grants();
