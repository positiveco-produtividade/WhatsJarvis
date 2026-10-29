-- 104 (painel, spec 4.3.4): busca de pessoas sem acento por nome/empresa. Coluna gerada, sem extensões.
ALTER TABLE access.users ADD COLUMN IF NOT EXISTS search_key text GENERATED ALWAYS AS (
  lower(translate(name || ' ' || company,
    'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ',
    'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'))
) STORED;
