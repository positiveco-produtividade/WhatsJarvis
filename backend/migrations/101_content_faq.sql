-- 101: FAQ do painel, schema `content` (spec painel.1 4.3.2). Somente recursos do PostgreSQL 16.
-- Toda escrita em content.faq_items incrementa ops.system_state['faq_revision'] (contrato de
-- propagacao para o leitor da FAQ, PN-25); o gatilho cobre importacao, script e SQL manual.

CREATE SCHEMA IF NOT EXISTS content;

CREATE TABLE IF NOT EXISTS content.faq_items (
  id            bigserial PRIMARY KEY,
  brand         text NOT NULL CHECK (brand IN ('todas','jungle','plant_power','possible','a_tal_da_castanha','zaya')),
  category      text NOT NULL CHECK (category IN ('PRODUTO','PRECO','ARGUMENTO','MATERIAL','CAMPANHA')),
  question      text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 500),
  question_key  text NOT NULL,
  answer        text,
  variations    text[] NOT NULL DEFAULT '{}',
  bot_responde  boolean NOT NULL,
  active        boolean NOT NULL DEFAULT true,
  origin        text NOT NULL CHECK (origin IN ('manual','import','seed','nielsen','gmr')),
  origin_ref    text,
  version       integer NOT NULL DEFAULT 1,
  created_at    timestamptz NOT NULL,
  created_by    text NOT NULL,
  updated_at    timestamptz NOT NULL,
  updated_by    text NOT NULL,
  deactivated_at timestamptz,
  deactivated_by text,
  CONSTRAINT answer_required CHECK (answer IS NOT NULL OR bot_responde = false)
);

CREATE UNIQUE INDEX IF NOT EXISTS faq_active_question_uq ON content.faq_items (brand, question_key) WHERE active;
CREATE INDEX IF NOT EXISTS faq_bot_read_idx ON content.faq_items (brand, category) WHERE active;
CREATE INDEX IF NOT EXISTS faq_list_idx ON content.faq_items (active, brand, id);

CREATE TABLE IF NOT EXISTS content.faq_item_versions (
  item_id     bigint NOT NULL REFERENCES content.faq_items(id),
  version     integer NOT NULL,
  snapshot    jsonb NOT NULL,
  change_kind text NOT NULL CHECK (change_kind IN ('create','update','deactivate','reactivate','import','seed')),
  changed_at  timestamptz NOT NULL,
  changed_by  text NOT NULL,
  PRIMARY KEY (item_id, version)
);

CREATE OR REPLACE FUNCTION content.bump_faq_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO ops.system_state (key, value, updated_at, updated_by)
  VALUES ('faq_revision', to_jsonb(1), now(), 'faq_trigger')
  ON CONFLICT (key) DO UPDATE
    SET value = to_jsonb((ops.system_state.value #>> '{}')::bigint + 1),
        updated_at = now(),
        updated_by = 'faq_trigger';
  RETURN NULL;
END;
$$;

-- Por instrucao (nao por linha): uma importacao em lote conta como uma revisao.
CREATE OR REPLACE TRIGGER faq_items_bump_revision_ins
  AFTER INSERT ON content.faq_items
  FOR EACH STATEMENT EXECUTE FUNCTION content.bump_faq_revision();

CREATE OR REPLACE TRIGGER faq_items_bump_revision_upd
  AFTER UPDATE ON content.faq_items
  FOR EACH STATEMENT EXECUTE FUNCTION content.bump_faq_revision();
