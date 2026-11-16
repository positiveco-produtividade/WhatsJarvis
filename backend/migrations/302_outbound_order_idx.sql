-- 302 (canal-waha.1, WA-T15): indice para a varredura que mantem as partes de uma resposta
-- em ordem (workers._DUE_OUTBOUND procura parte anterior `pending`/`sending` do mesmo
-- inbound_trace_id). Parcial: so as linhas ainda em aberto, que sao poucas. PostgreSQL 16.
CREATE INDEX IF NOT EXISTS outbound_open_by_trace_idx
  ON chat.outbound_messages (inbound_trace_id, id)
  WHERE status IN ('pending', 'sending');
