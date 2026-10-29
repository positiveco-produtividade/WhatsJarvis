-- 001: schemas por dominio, estado do sistema e auditoria imutavel (spec 4.2.4).
-- ops.schema_migrations e criada pelo proprio runner (app.db.migrate).
-- Somente recursos do PostgreSQL 16.

CREATE SCHEMA IF NOT EXISTS access;
CREATE SCHEMA IF NOT EXISTS chat;
CREATE SCHEMA IF NOT EXISTS ops;

CREATE TABLE IF NOT EXISTS ops.system_state (
  key text PRIMARY KEY,
  value jsonb NOT NULL,
  updated_at timestamptz NOT NULL,
  updated_by text NOT NULL
);
-- chaves: team_group_enabled (bool), paused (bool), brand_enabled:<marca> (bool).

CREATE TABLE IF NOT EXISTS ops.audit_log (
  id bigserial PRIMARY KEY,
  at timestamptz NOT NULL,
  actor_type text NOT NULL CHECK (actor_type IN ('cli','panel','system')),
  actor text NOT NULL,
  action text NOT NULL,
  target_type text NOT NULL,
  target_id text NOT NULL,
  old_value jsonb,
  new_value jsonb
);

CREATE OR REPLACE FUNCTION ops.audit_log_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'ops.audit_log e imutavel: % recusado', TG_OP;
END;
$$;

CREATE OR REPLACE TRIGGER audit_log_no_update_delete
  BEFORE UPDATE OR DELETE ON ops.audit_log
  FOR EACH ROW EXECUTE FUNCTION ops.audit_log_immutable();
