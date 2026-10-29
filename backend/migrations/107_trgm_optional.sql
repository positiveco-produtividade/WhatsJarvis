-- 107 (painel, spec 4.3.3 / Q-P8): índices trigram OPCIONAIS para a busca por substring.
-- Nunca falha: sem pg_trgm disponível, sem privilégio para criá-la ou com qualquer outro erro,
-- emite NOTICE e segue (a busca continua correta, só sem índice). Idempotente.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm') THEN
    IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm') THEN
      RAISE NOTICE '107: pg_trgm indisponivel neste servidor; indices trigram ignorados';
      RETURN;
    END IF;
    BEGIN
      CREATE EXTENSION pg_trgm;
    EXCEPTION WHEN insufficient_privilege THEN
      RAISE NOTICE '107: sem privilegio para criar pg_trgm; indices trigram ignorados';
      RETURN;
    END;
  END IF;

  BEGIN
    EXECUTE 'CREATE INDEX IF NOT EXISTS tickets_search_trgm_idx ON chat.tickets USING gin (search_key gin_trgm_ops)';
    EXECUTE 'CREATE INDEX IF NOT EXISTS users_search_trgm_idx ON access.users USING gin (search_key gin_trgm_ops)';
  EXCEPTION WHEN OTHERS THEN
    -- ex.: classe de operadores fora do search_path; o subbloco desfaz o que criou
    RAISE NOTICE '107: indices trigram ignorados (%)', SQLERRM;
  END;
END
$$;
