# Perguntas Nielsen e GMR — como são feitas e como são respondidas

Resumo de 16/11/2026. Vale para o estado atual do projeto.

## Em uma frase

Não existe uma planilha com as perguntas prontas. As perguntas de Nielsen e GMR
são **montadas por código, na hora**, a partir de dois resumos em JSON. Esses
resumos foram extraídos das planilhas originais, que ficam fora do projeto.

```
Planilha original (.xlsx, fora do git)
        │  extração (feita na tela "Dados" do protótipo)
        ▼
docs/designs/dados/nielsen-resumo.json   e   gmr-resumo.json   (também fora do git)
        │  piloto/faq.js monta pergunta + resposta com regras fixas
        ▼
FAQ do piloto: 112 do Guia + 56 Nielsen + 55 GMR = 223 itens
        │  busca por palavras (sem IA)
        ▼
Resposta no WhatsApp, sempre com "(Fonte: ... · período)"
```

## 1. De onde vêm os dados

| Fonte | Planilha original | Resumo usado pelo Jarvis | Período atual |
|---|---|---|---|
| **Nielsen** (mercado) | `farol-de-negocios/bases/base_dados_farol.xlsx` (exportada do Farol em 13/10/2026) | `docs/designs/dados/nielsen-resumo.json` (73 linhas) | set/26 comparado com set/25 |
| **GMR** (vendas internas da Positive) | `farol-de-negocios/bases/base_gmr_rob_liquida.xlsx` | `docs/designs/dados/gmr-resumo.json` (4 linhas Brasil, 9 por canal, 99 por estado) | jul/26 a set/26 |

As planilhas e os resumos **não vão para o GitHub** (`*.xlsx` no `.gitignore`,
decisão registrada no `docs/prd.md`), porque são dados licenciados (Nielsen) ou
internos (GMR).

Cada linha do resumo **Nielsen** traz: categoria (isotônicos ou bebidas
vegetais), marca, lugar (estado ou Brasil), share atual e anterior, posição no
ranking, número de lojas da amostra, distribuição e preço médio.

Cada linha do resumo **GMR** traz: marca, recorte (Brasil, canal ou estado) e os
3 produtos mais pedidos pelos clientes, no nome do SAP.

## 2. Como as perguntas são feitas

Quem monta tudo é o arquivo `piloto/faq.js`, nas funções `itensNielsen()` e
`itensGmr()`. A regra geral é **gerar só o que serve de argumento de venda**.

### Nielsen: até 3 tipos de pergunta por linha

Antes de tudo, a linha é descartada se a amostra tiver **menos de 10 lojas**
(número instável).

| Quando gera | Pergunta | Resposta |
|---|---|---|
| A marca é **a 1ª** do ranking | "Quem lidera {categoria} em {lugar}?" | "{Marca} é o líder de {categoria} em {lugar}, com X% de share" |
| A marca está em **2º ou 3º** e **não caiu** mais de 0,5 ponto | "Qual o share do {Marca} em {lugar}?" | "{Marca} é o 2º/3º em {categoria} em {lugar}, com X% de participação em volume" |
| A marca **ganhou 0,5 ponto ou mais** de share | "{Marca} está crescendo em {lugar}?" | "Sim. {Marca} ganhou X pontos de share em {lugar} em relação a {período anterior} (de A% para B%)" |

**Ficam de fora de propósito** (uso interno): quedas de share, distribuição e
preço médio. A Zaya não aparece porque não está na base Nielsen.

Exemplo real gerado:

> **P:** A Tal da Castanha está crescendo em AL?
> **R:** Sim. A Tal da Castanha ganhou 3,8 pontos de share em AL em relação a
> set 25 (de 72,6% para 76,4%) (Fonte: Nielsen Retail · bebidas vegetais · AL · set 26.)

Com a base de set/26 saem **56 perguntas Nielsen**.

### GMR: uma pergunta só, em três recortes

A pergunta é sempre "Quais produtos do/da {Marca} os clientes mais pedem
{onde}?" e a resposta lista o top 3 ("1º …; 2º …; 3º …").

| Recorte | Quando gera |
|---|---|
| **Brasil** | Sempre, uma por marca |
| **Canal** (varejo tradicional, varejo especializado, food service) | Sempre |
| **Estado** | Só para **Jungle e Plant Power**, e só quando o top 3 do estado **é diferente** do top 3 do Brasil (para não repetir a mesma resposta) |

Regras que valem para todas as perguntas GMR:

- **Sem R$, sem volume e sem nome de cliente.** Só a ordem dos produtos (regra
  de não expor resultado interno).
- Estados com menos de 10 clientes ficam de fora (isso é feito na extração).
- Produtos Jungle vêm cadastrados como "PLANT POWER" na GMR e são reclassificados
  pelo nome do produto.
- O nome do produto é a descrição do SAP com letras ajeitadas (ex.: "Alimento
  Cast ATDC Original Org 12x1l"). **Pendente:** trocar por nome comercial.

Exemplo real gerado:

> **P:** Quais produtos do Jungle os clientes mais pedem no Brasil?
> **R:** Os mais pedidos do Jungle no Brasil são: 1º Suplemento Alim Jung Low
> Aba/Ho 12x500ml; 2º Suplemento Alim Jung Low Mor/Li 12x500ml; 3º Suplemento Alim
> Jung Low Uva 12x500ml (Fonte: GMR · pedidos de clientes Positive · Brasil ·
> jul/26 a set/26.)

Com a base de jul–set/26 saem **55 perguntas GMR**.

### O que toda pergunta Nielsen/GMR carrega

- A **marca** (usada para filtrar).
- A **origem** (`Nielsen` ou `GMR`), que vira selo no painel (📈 Nielsen, 📦 GMR).
- A **fonte com período**, já colada no fim da resposta. Isso é obrigatório: a
  Nielsen aprovou o repasse dos números aos vendedores (29/10/2026) **desde que
  sempre com fonte e período**.

## 3. Como as perguntas são respondidas

### No piloto (hoje, sem IA)

A função `buscar()` do `piloto/faq.js` compara a mensagem do vendedor com todas
as 223 perguntas e escolhe a mais parecida:

1. **Limpa a mensagem:** tira acentos, palavras vazias ("de", "qual", "o"…) e
   troca sinônimos ("líder" → "lidera", "vende/vendido" → "pedem", "cresceu" →
   "crescendo", "participação" → "share", "atal" → "castanha").
2. **Descobre a marca** citada. Se o vendedor citou uma marca, só valem
   perguntas dessa marca (ou gerais).
3. **Descobre o estado.** "CE" em maiúsculas ou "em ce", "no sp" contam como
   estado. Regras:
   - pergunta cadastrada de um estado só responde se o vendedor citou **esse**
     estado;
   - se o vendedor pediu um estado, a resposta "no Brasil" é descartada (não
     responde com o número errado).
4. **Dá uma nota** de 0 a 1 pela quantidade de palavras em comum (nos dois
   sentidos) e fica com a maior.
5. A resposta sai **exatamente como foi montada**, com a linha de fonte,
   assinada pelo agente da marca.

### No backend novo (cérebro, com IA)

No backend em Python, os itens Nielsen e GMR são **itens comuns da FAQ**,
gravados no banco com `origin = 'nielsen'` ou `'gmr'`
(`backend/app/services/brain/source.py`). O cérebro:

- filtra a FAQ pelas marcas, estados e canais do cadastro do vendedor (o filtro
  é feito na consulta ao banco, não no texto enviado à IA);
- usa a busca por palavras como primeiro filtro e o Gemini para entender a
  pergunta e escrever a resposta **só com o que está nos itens**;
- mantém o "(Fonte: …)" que já vem no texto do item;
- se nada na FAQ responde, diz que não sabe e abre ticket para a equipe.

## 4. Carga no backend (desde 16/11/2026)

O comando `faq dados` grava as perguntas na FAQ do painel, com as mesmas regras
do piloto (`backend/app/panel/services/faq_dados.py`):

```
cd backend
.venv\Scripts\python -m app.cli faq dados --by caio
# faq dados: total=111 inseridas=111 ja existiam=0 nielsen=56 gmr=55
```

- Lê `docs/designs/dados/` (ou `--dir`); basta um dos dois resumos.
- Entram com categoria **Argumento**, `origin` `nielsen` ou `gmr` e o período em
  `origin_ref`: o painel mostra o selo "Nielsen · set 26" ou "GMR · jul/26 a set/26".
- **Só recorte nacional (decisão do Caio, 16/11/2026):** Nielsen só Total
  Brasil; GMR só Brasil por marca e por canal. Nada por estado. Com as bases
  atuais: 3 perguntas Nielsen (liderança da A Tal da Castanha; 3º lugar e
  crescimento do Jungle) e 13 GMR. Possible e Plant Power não têm argumento
  Nielsen nacional (6º e 14º lugar, sem crescimento de 0,5 ponto).
- **Tom de venda de comercial para comercial (decisão do Caio, 16/11/2026):**
  o número vem primeiro e a frase seguinte convida a comprar ("é a marca que o
  consumidor mais leva…", "bom momento para colocar ou ampliar…", "bom ponto de
  partida para montar o mix…"), sem preço e sem citar outra marca. Cada pergunta
  traz variações de como o vendedor pergunta ("Por que vender X?", "Qual o mix
  do X?"), que entram na busca.
- Idempotente: rodar de novo pula o que já existe (mesma marca e pergunta) e
  **atualiza** a pergunta da mesma origem cujo texto, variações ou período
  mudou (nova versão no histórico).
- **Substitui a carga anterior:** pergunta ativa de uma origem carregada que
  não foi gerada de novo vai para Desativadas (sem apagar). Só a origem cujo
  resumo está na pasta é substituída; Guia e manuais nunca são tocados.
- Pergunta de outra origem (Guia, manual) com o mesmo texto nunca é alterada.

## 5. O que ainda falta

- **Tela "Dados" no painel real** (tarefa `painel.2`): hoje ela só existe no
  protótipo (`docs/designs/painel-prototipo.html`). A ideia é o admin subir a
  planilha Nielsen ou GMR, ver a prévia das perguntas e publicar na FAQ. Cada
  nova carga substitui a anterior, e as antigas vão para Desativadas. Até lá, a
  carga é feita pelo comando `faq dados`.
- **Nomes comerciais dos produtos** nas respostas da GMR, no lugar da descrição
  SAP.

## Arquivos para consultar

- `piloto/faq.js`: montagem das perguntas (`itensNielsen`, `itensGmr`) e busca
  (`buscar`).
- `docs/designs/dados/nielsen-resumo.json` e `gmr-resumo.json`: os dados de
  entrada.
- `docs/prd.md` (seção de 22/10/2026 e 29/10/2026): as regras de negócio e a
  aprovação da Nielsen.
- `docs/tasks/cerebro.1/spec/spec.md`: como o cérebro usa esses itens (decisões
  D5, D7 e D15).
- `docs/tasks/painel.1/spec/spec.md` (seção 8.4): a futura tela Dados.
