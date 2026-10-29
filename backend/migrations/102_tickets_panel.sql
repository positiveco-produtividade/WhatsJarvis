-- 102 (painel, spec 4.3.3): busca sem acento e índices por marca/status em chat.tickets.
-- Só ACRESCENTA (coluna gerada e índices); nenhuma coluna de PF muda. Sem extensões; PostgreSQL 16.
ALTER TABLE chat.tickets ADD COLUMN IF NOT EXISTS search_key text GENERATED ALWAYS AS (
  lower(translate(ticket_number || ' ' || coalesce(message, ''),
    'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ',
    'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'))
) STORED;

CREATE INDEX IF NOT EXISTS tickets_brand_idx  ON chat.tickets (brand, created_at DESC);
CREATE INDEX IF NOT EXISTS tickets_status_idx ON chat.tickets (status, created_at DESC);

-- Recria a visão para expor search_key; as colunas de PF seguem na mesma ordem, search_key vai por último.
CREATE OR REPLACE VIEW ops.v_tickets AS SELECT * FROM chat.tickets;
