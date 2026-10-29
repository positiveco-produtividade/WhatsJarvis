# Conjunto de avaliacao do cerebro (CB-21)

Casos do gate offline (`python -m app.services.brain.eval`, tarefa CB-T29). Cada arquivo
em `cases/*.jsonl` tem um caso JSON por linha. O esquema e validado por
`tests/brain/unit/test_eval_cases_schema.py`.

## Esquema do caso

| Campo | Obrigatorio | Descricao |
|---|---|---|
| `id` | sim | unico em todos os arquivos |
| `category` | sim | `guia`, `piloto`, `preco`, `followup`, `ataque`, `sintetico` (igual ao arquivo) |
| `question` | sim | texto do vendedor |
| `registration` | sim | cadastro simulado: `brands` (slugs) e `ufs` |
| `history` | nao | turnos anteriores `{role: user\|assistant, text}` (follow-ups) |
| `expected` | sim | `responde`, `saudacao`, `nao_sei`, `pede_marca`, `acesso`, `recusa` |
| `refs` | sim | itens que a resposta deve citar (vazio quando nao responde); `"a|b"` aceita qualquer um dos itens listados |
| `items` | nao | itens SINTETICOS extras a injetar na fonte so neste caso |
| `must_contain` | nao | trechos que a resposta (modo `palavras`) deve repetir da fonte |
| `price_in_source` | preco | `true` = valor na fonte (deve repetir); `false` = deve escalar |
| `classification` | nao | classificacao esperada do ticket (ex.: `PRECO`) |
| `note` | nao | comentario livre |

`nao_sei` conta como acerto quando o caso escala (CB-21). Preco: 100% exigido.

## Referencias estaveis (`refs`)

- `guia:NNN`: posicao (1..112, tres digitos) do item em `piloto/guia.json`, na ordem da carga
  `python -m app.cli faq seed`. O id do banco (`faq:123`) muda por ambiente; o executor
  mapeia `guia:NNN` para o item semeado pela pergunta (`q`) e marca.
- `nielsen_sint:NNN`, `gmr_sint:NNN`, `preco_sint:NNN`: itens inline (`items`) do proprio caso.

## Regra de ouro: nenhum dado licenciado

Itens Nielsen/GMR aqui sao **inventados** no formato deles: ref com sufixo `_sint`, texto com
`SINTETICO` e `(Fonte: Nielsen sintético)`. Nunca copie dado real (licenciado), nunca
leia `*.xlsx` nem `docs/designs/dados/`. A varredura do teste rejeita planilhas, a pasta de
dados e rodapes de relatorio reais.

## Arquivos

`guia` (112 perguntas do Guia + variacoes, marca ambigua, acesso, fora da base), `piloto`
(os 5 casos validados), `preco` (com e sem valor na fonte), `followup`, `ataques` (PT-BR e
injecao), `nielsen_gmr_sinteticos`.
