-- Setup unico do banco de desenvolvimento do Jarvis (PostgreSQL local).
-- O superusuario `postgres` serve so para rodar este script; a aplicacao nunca o usa.
-- Execucao (pelo Caio):
--   & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -d postgres -f backend\scripts\dev_db_setup.sql
-- Depois defina as senhas dentro do psql: \password jarvis  e  \password jarvis_test
-- Idempotente. Somente recursos do PostgreSQL 16 (producao).

-- Papeis: senha placeholder, a real e definida com \password (nunca em arquivo).
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis') THEN
    CREATE ROLE jarvis LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '__DEFINIR_SENHA__';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_test') THEN
    CREATE ROLE jarvis_test LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '__DEFINIR_SENHA__';
  END IF;
  -- Leitura do assistente (painel). Os grants vem da migration 105 (schema `assistant`), nao daqui:
  -- depois de criar o papel, o dono de cada banco roda `SELECT assistant.apply_grants();`
  -- (idempotente). Senha real: \password jarvis_assistant.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_assistant') THEN
    CREATE ROLE jarvis_assistant LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '__DEFINIR_SENHA__';
  END IF;
  -- Papel da aplicacao em producao (porta-fechada.2). Aqui e NOLOGIN: ninguem entra com ele no
  -- PC; os testes o assumem (SET ROLE) para provar que o app funciona sem DDL. As permissoes
  -- vem da migration 402 (ops.apply_app_grants), reaplicadas a cada `python -m app.db.migrate`.
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jarvis_app') THEN
    CREATE ROLE jarvis_app NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
  END IF;
END
$$;

-- CREATE DATABASE nao roda em bloco: gera o comando so se o banco nao existe.
SELECT 'CREATE DATABASE jarvis OWNER jarvis'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'jarvis') \gexec

SELECT 'CREATE DATABASE jarvis_test OWNER jarvis_test'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'jarvis_test') \gexec

REVOKE ALL ON DATABASE jarvis FROM PUBLIC;
REVOKE ALL ON DATABASE jarvis_test FROM PUBLIC;

-- Testes do painel: o papel de teste pode assumir jarvis_assistant (SET ROLE) para provar
-- que a leitura do assistente nao ve PII nem escreve. So no banco local de teste.
GRANT jarvis_assistant TO jarvis_test;
-- Testes: o app roda como jarvis_app (porta-fechada.2, D3). Depois, rodar as migrations de novo
-- (ou a suite) para aplicar as permissoes: o runner chama ops.apply_app_grants().
GRANT jarvis_app TO jarvis_test;
