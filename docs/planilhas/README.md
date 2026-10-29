# Planilhas para preencher

Modelos das planilhas que alimentam o Jarvis com **dados exatos** (códigos,
medidas, preço). Esses dados nunca saem da "cabeça" da IA: o Jarvis só
responde número que estiver aqui.

Podem ser preenchidas no Google Sheets (Arquivo → Importar o `.csv`).
**Uma linha por SKU.** Não mude o nome das colunas.

## produtos.csv — cadastro dos produtos

| Coluna | O que é | Exemplo de formato |
|---|---|---|
| `sku` | Código interno do produto | texto |
| `marca` | A Tal da Castanha, Jungle, Possible, Plant Power ou Zaya | texto |
| `familia` | Linha dentro da marca (ex.: Ultracoffee, Low Carb) | texto |
| `nome` | Nome comercial | texto |
| `sabor` | Sabor, se houver | texto |
| `tamanho` | Tamanho da embalagem (ex.: 220 g, 1 L, display 14 un) | texto |
| `ean` | Código de barras da unidade (13 dígitos) | número |
| `dun` | Código de barras da caixa (14 dígitos) | número |
| `ncm` | Classificação fiscal (8 dígitos) | número |
| `altura_cm`, `largura_cm`, `profundidade_cm` | Medidas da unidade | número com vírgula |
| `peso_liquido_g`, `peso_bruto_g` | Pesos da unidade em gramas | número |
| `unidades_caixa_master` | Quantas unidades vêm na caixa master | número |
| `caixas_por_palete` | Quantas caixas cabem num palete | número |
| `shelf_life_dias` | Validade a partir da fabricação, em dias | número |
| `armazenagem` | Como guardar (ex.: local seco e arejado) | texto |
| `status` | `ativo` ou `inativo` | texto |

## precos.csv — tabela de preço

Por enquanto um preço único por SKU (não varia por canal, estado ou perfil).

| Coluna | O que é |
|---|---|
| `sku` | Mesmo código da planilha de produtos |
| `preco` | Preço de venda ao cliente (loja/distribuidor), em R$ |
| `preco_sugerido_consumidor` | Preço sugerido na prateleira, em R$ |
| `inicio_vigencia` | Data em que o preço começa a valer (DD/MM/AAAA) |
| `fim_vigencia` | Data em que deixa de valer (DD/MM/AAAA) |

**Sem vigência, o Jarvis não informa o preço.** Vencida a data, o preço sai do ar sozinho.

## skus_prioritarios.csv — foco do mês

O ciclo comercial é mensal.

| Coluna | O que é |
|---|---|
| `marca` | Marca do SKU |
| `sku` | Mesmo código da planilha de produtos |
| `papel` | `nucleo` (sempre presente), `foco` (foco do mês) ou `lancamento` |
| `mes_referencia` | Mês do ciclo (MM/AAAA) |

## Fica para depois

- **Mix por tipo de loja** — depende da classificação de lojas (adiada).
- **Informação nutricional e alergênicos** — o Guia de produtos cobre parte; a planilha vem quando os rótulos forem levantados.
