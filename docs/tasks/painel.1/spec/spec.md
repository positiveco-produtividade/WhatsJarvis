---
task_id: PAINEL-1
status: approved
phase: 3-tasks
design_approved_by: Caio
design_approved_at: 2026-10-23
approved_by: Caio
approved_at: 2026-10-23
---

# Spec — Painel de gestão do Jarvis

> **Task ID:** PAINEL-1
> **Story:** [painel.1](../../../stories/painel.1.md)
> **Epic:** [painel](../../../epics/painel.md)
> **Status:** Fase 1 (Requisitos) **aprovada pelo Caio em 2026-10-23**. Fase 2 (Design: seções 4, 5 e 8–10, `research.json`, `complexity.json`) **redigida em 2026-10-23, aguardando aprovação** antes da Fase 3 (tarefas). Alinhada em 2026-10-23 com a aceitação de R1–R4 por porta-fechada.1 (PF spec 4.11).
> **Size:** XL (34 pontos, ~246 h; ver `complexity.json`). Deve ser quebrada em ~20 tarefas na Fase 3 (ver 8.1).

Fonte de verdade dos critérios: `requirements.json` (mesma pasta). Fonte de verdade visual e de comportamento: protótipo aprovado `docs/designs/painel-prototipo.html` (imagens dos agentes em `docs/designs/bots/`, fichas em `docs/designs/bots/fichas/`, arte do coordenador em `docs/designs/bots/fichas/jarvis-coordenador.png`). Em caso de divergência entre o protótipo e este spec, vale este spec nos pontos listados em "Divergências do protótipo".

## Decisões (2026-10-22 e 2026-10-23)

Respostas do Caio às questões da primeira versão e instruções posteriores:

- **Primeira mensagem de desconhecido.** O painel MOSTRA o texto da primeira mensagem de um número desconhecido, SOMENTE em Pessoas autorizadas → Pedindo acesso (só a primeira, nunca histórico). Condicionado a validação jurídica da LGPD (checklist legal) e à emenda de porta-fechada.1, em paralelo. Sem a validação, a exibição fica desligada (PN-37, D3).
- **Número desconhecido não é alerta e não envia e-mail.** A tentativa aparece só em Pedindo acesso (PN-67).
- **Reativar.** Permitido: a pessoa volta a pending_optin e precisa aceitar o aviso de novo. Motivos de revogação: lista fixa do protótipo (PN-34, PN-39, D1).
- **Cadastro completo.** Obrigatórios todos os campos do PRD: nome, telefone, perfil, empresa, UFs, canais e marcas (múltiplos) e gestor responsável. A importação de pessoas traz os mesmos campos, grava só as linhas válidas e reporta as inválidas (PN-32, PN-40).
- **Modo conservador: removido** por inteiro (PN-46 removido).
- **Acesso:** só o Caio, admin único, por enquanto (PN-2).
- **Escalado não é alerta e não envia e-mail.** Tickets escalados (inclusive escalação de mídia) aparecem só em Escalados, Tickets e Inbox, nunca em Alertas (PN-62 retirado; PN-64, o e-mail de escalado, também retirado). Alertas = só problemas de sistema e segurança: falha de API, taxa de erro, pico de volume, tentativa de injection, limite de 60 msg/h, pausa/retomada e marca ligada/desligada (PN-44). A urgência nasce como no protótipo e o Caio pode mudá-la no cartão (PN-63).
- **E-mail ao Caio só para novos alertas.** Cada novo alerta gera um e-mail imediato, no padrão do AUTO-1 (Resend). O destinatário vem de configuração do servidor (o endereço não é escrito neste spec). Conteúdo mínimo, sem dados pessoais; falha de envio nunca bloqueia o processamento e é retentada e registrada (PN-65, PN-66).
- **Grupo da equipe (WAHA).** Caminho (avisos e resumo diário às 08:05) construído, mas DESLIGADO por chave de configuração (padrão desligado). Desligado, nada é enviado a grupo algum; ligado, segue o AUTO-1 (PN-60, PN-61). O e-mail de alertas é independente dessa chave.
- **Nome do agente coordenador:** Jarvis (não "Beto Positive"). Responde quando nenhuma marca única é citada; os agentes de marca continuam Duda, Léo, Nico, Rita e Tati.
- **Áudio e foto de usuário autorizado (2026-10-23, Caio):** recebem a resposta curta "equipe avisada" (PF AC-36 vale; a nota do PRD §10 de 2026-10-22 que dizia "sem resposta" foi revertida). O Inbox do protótipo já mostra essa resposta como mensagem do Bot. Impacto neste spec: o chat do Inbox exibe a resposta `media_ack` como "Bot"; a pausa do Jarvis bloqueia também esse aviso (4.9).
- **R1–R4 aceitos por porta-fechada.1 (2026-10-23):** tickets em `chat.tickets` e visão `ops.v_tickets` criados por PF (migration `005`); `users.validate_row`/`register` reutilizáveis; `raise_alert(..., email=True)` com parâmetro `email`; `flags` com `paused`/`brand_enabled:*` e `reply_policy` (`FlagsReplyPolicy`) são de PF.

## 1. Goal

Dar ao Caio (hoje o único administrador) um painel web igual ao do AUTO-1 para operar o Jarvis sem comandos no servidor: acompanhar tickets e conversas, tratar os escalados, manter a FAQ (semente: Guia de produtos, 112 perguntas), cadastrar e revogar pessoas e ver quem pede acesso, pausar o Jarvis (geral e por marca), ver métricas e relatórios, acompanhar alertas de sistema e segurança, ser avisado por e-mail de cada novo alerta e perguntar ao assistente interno. Substitui o CLI/CSV temporário de porta-fechada.1 (exceção à regra 1 da lista positiva, PRD §10 de 2026-10-21).

Menu (como no protótipo): **Operação** (Tickets, Inbox, Escalados, FAQ, Pessoas autorizadas) · **Análise** (Métricas, Alertas, Relatórios) · **Admin** (Logs, Perguntas). No topo, o seletor de marca (Todas as marcas, Jungle, Plant Power, Possible, A Tal da Castanha, Zaya, cada uma com o rosto do seu agente) escopa as telas. Flutuante em todas as telas: o Assistente (Jarvis, coordenador, e os agentes Duda, Léo, Nico, Rita e Tati).

Classificações compartilhadas por Tickets, Escalados e FAQ: Produto, Preço, Argumento, Material, Campanha, Mídia, Sem classificação (a FAQ usa só as cinco primeiras). Status de ticket: Respondido pelo bot, Respondido por humano, Aguardando atendimento, Erro no envio.

Contexto: PRD RF-20, RF-21 (parcial), RF-22 (parcial), RF-23; Decision Log 2026-10-19..21; ADR 2026-10-20 (Python 3.12 + FastAPI, Postgres, login IAM, mesmo padrão do AUTO-1). Depende de porta-fechada.1 (tabelas de usuários, mensagens, rastro, tentativas, alertas, tickets); termos e comportamentos de lá (PF AC-n) valem aqui.

## 2. User Story

**Como** responsável pelo Jarvis,
**quero** um painel igual ao do AUTO-1, com as telas aprovadas no protótipo, e ser avisado por e-mail de cada novo alerta,
**para** ver as conversas, tratar escalados, atualizar a FAQ, controlar quem pode usar o Jarvis, pausar o Jarvis e acompanhar o uso sem depender de comandos no servidor.

## 3. Acceptance Criteria

Formato EARS (GIVEN/WHEN/THEN) com notas de teste em `requirements.json`. Total: **64 critérios ativos** (PN-1 a PN-67; PN-46, PN-62 e PN-64 foram removidos e os IDs não são reutilizados), agrupados por tela.

| Grupo | IDs | Resumo |
|---|---|---|
| Acesso | PN-1..4 | Login IAM só admin (Caio); 403 no servidor para os demais; IAM fora do ar (sem login novo, sessões vivas seguem, Jarvis no WhatsApp intocado); CSRF |
| Marca em foco | PN-5..6 | Seletor persiste e escopa; conversa é por vendedor, marca vem do ticket; mídia sem marca aparece em todas |
| Tickets | PN-7..9 | 5 KPIs, filtros (busca, classificação, status, período), abrir no Inbox, estado vazio |
| Inbox | PN-10..14 | Conversas/Mídias, só aguardando, Concluir em lote, horários, separadores de dia e "respondeu em X", "Responder em: Grupo da equipe" sem caixa de resposta (desativado com a chave desligada), Tickets recentes |
| Drawer do ticket | PN-15..18 | Conteúdo sem "Responder ao vendedor"; Concluir (envia nada, registra autor); conclusão concorrente; Reclassificar |
| Escalados | PN-19..21, PN-63 (PN-62 removido) | Quadro por classificação (inclui mídia, sem alerta e sem e-mail), botão Mídias, Mover para Concluída, urgência editável no cartão |
| FAQ | PN-22..30 | Lista/filtros/paginação, adicionar/editar com validação, desativar/reativar, propagação ≤5 min, edição concorrente, importação (conferência, rejeições, gravação atômica), semente de 112 |
| Pessoas | PN-31..40, PN-67 | Três partes e busca única, cadastrar com todos os campos, telefone inválido/duplicado (9º dígito), revogar ≤1 min com motivo fixo, revogar no meio da conversa, autorizar pedido, primeira mensagem de desconhecido só em Pedindo acesso (condicionada a LGPD), desconhecido sem alerta nem e-mail, ignorar, reativar, importar CSV (grava só as válidas) |
| Métricas / Relatórios | PN-41..43 | KPIs, mensagens por dia, por marca, tempo de resposta, bloqueios; perguntas frequentes, objeções, sem resposta com "Criar na FAQ"; PDF mensal; estados vazios |
| Alertas | PN-44..48 | Só sistema e segurança (escalação, mídia e número desconhecido não geram cartão); Pausar/Retomar; Jarvis por marca; confirmações em modal e idempotência; todo controle gera alerta e auditoria (sem modo conservador) |
| Grupo da equipe | PN-49, PN-60, PN-61 | Chave desligada por padrão: nada é enviado a grupo; ligada: comportamento AUTO-1 (avisos, resumo 08:05) |
| E-mail | PN-65..66 (PN-64 removido) | E-mail imediato por alerta novo (severidade, tipo, texto, link), sem PII; escalado e desconhecido não geram e-mail; falha não bloqueia, é retentada e registrada |
| Assistente | PN-50..53 | Jarvis (coordenador) e os agentes de marca; agente pela marca citada; Gemini só leitura sobre o banco; tudo registrado em Perguntas |
| Logs | PN-54 | Tickets com problema e final do log, sem PII. "Final do log" (decisão da Fase 4, 2026-10-26) = as últimas 500 linhas JSON já redigidas que o próprio processo guarda em memória (handler em anel registrado ao montar o painel; nada lido de arquivo do servidor; zera ao reiniciar) |
| Transversal e LGPD | PN-55..59 | Auditoria; exportação de dados da pessoa; exclusão a pedido; visão de retenção de 12 meses; desempenho (≤2 s p95) e PT-BR |

Casos de borda cobertos: não-admin (PN-2), IAM fora do ar (PN-3), edição concorrente (PN-17, PN-26, PN-48), importação de FAQ e de pessoas com linhas inválidas e telefones duplicados com 9º dígito (PN-28, PN-33, PN-40), revogar com conversa em andamento (PN-35), propagação da FAQ ≤5 min (PN-25), estados vazios (PN-9, PN-43), LGPD (PN-56..58), auditoria (PN-55), grupo desligado (PN-60), e-mail indisponível (PN-66), desconhecido sem alerta (PN-67).

### Divergências do protótipo (registradas aqui para não virarem bug)

1. **Alertas de escalação, mídia e número desconhecido.** O protótipo tem os tipos `media_received`, `escalation_sla` e `unknown_number` em Alertas; foram retirados por decisão de 2026-10-22 (escalado aparece só em Escalados; desconhecido só em Pedindo acesso). O alerta do protótipo com "primeira_mensagem" no detalhe deixa de existir.
2. **Modo conservador.** Existe no protótipo (Alertas), mas foi removido; não implementar.
3. **Nome do coordenador.** O protótipo usa "Beto Positive"; o nome correto é Jarvis.
4. **Chave do grupo.** O protótipo mostra "Grupo da equipe (WhatsApp)" e o resumo das 08:05 como se estivessem ativos; na versão real dependem da chave (desligada por padrão).
5. **E-mail.** O protótipo não tem avisos por e-mail; vêm de PN-65..66 (só alertas).
6. **Campos de cadastro.** O protótipo tem nome, telefone, perfil, UF, empresa; a versão real exige também UFs múltiplas, canais, marcas e gestor.
7. **Sem tags.** Em nenhuma tela (FAQ, importação, assistente).
8. **Sem tela de auditoria nem de LGPD** no protótipo; os PN-55..58 pedem os pontos de entrada. Design (Fase 2): abas **Auditoria** e **Retenção** dentro de Logs, e botões Exportar/Excluir na ficha da pessoa (Q-P3).
9. **CDN.** O protótipo carrega Tailwind/XLSX de CDN; a versão real usa assets próprios. Observação da Fase 2: o AUTO-1 serve marked, DOMPurify e Alpine components do próprio servidor, mas carrega htmx, Alpine CSP e Chart.js de CDN com SRI; aqui os três também passam a ser servidos de `/static/vendor/`.
10. **Importar CSV de pessoas** não grava no protótipo; a versão real segue PN-40.
11. **Respostas humanas no chat.** O protótipo mostra mensagens "Humano" no chat; sem caixa de resposta no painel e com o grupo desligado, a equipe responde fora do sistema e o chat real só mostra respostas do Bot e a linha "Concluído por X em hh:mm" (a captura de respostas do grupo é a história futura de escalonamento).

## 4. Arquitetura e Design

Base: AUTO-1 (`C:\Users\User\Documents\GitHub\agente-instagram-direct-n8n`: `backend/app/{main,config}.py`, `core/security.py`, `services/iam.py`, `api/{dashboard,admin,assistant}.py`, `frontend/templates`, `frontend/static/js/alpine-components.js`), estudado em `research.json`. O modelo de dados compartilhado (banco `jarvis`, schemas `access`/`chat`/`ops`, **`chat.tickets` e `ops.v_tickets`**, auditoria, camada de serviço do CLI) é de **porta-fechada.1 (PF)**; este spec só acrescenta migrations **aditivas** e reutiliza os serviços de PF (`users`, `attempts`, `flags`, `reply_policy`, `audit`, `alerts`, `deliveries`, `outbox`). Este spec **não edita** nada de PF. Os quatro pedidos do painel (R1–R4) foram aceitos por PF (4.15).

### 4.1 Mesmo processo ou processo separado

**Decisão: um único app FastAPI e um único processo PM2 `jarvis` (1 worker), o mesmo do webhook de PF, com o painel como pacote isolado `app/panel/`.**

| Opção | Prós | Contras |
|---|---|---|
| **A. Mesmo processo (escolhida)** | Igual ao AUTO-1; um deploy, um nginx, um subdomínio; pausa/marca/chave do grupo valem na hora (cache do mesmo processo invalidado na escrita); "Reenviar" dispara o despacho imediato; menos memória (servidor com 2,4 GB livres); PF já reservou `location /` para o painel | Bug ou rota lenta do painel divide o único worker com o webhook; deploy de template reinicia o webhook (a Meta reentrega, a idempotência absorve) |
| B. Dois processos PM2 (`jarvis-webhook`, `jarvis-panel`) | Isolamento de falha e de superfície (webhook sem Jinja/IAM) | Dobra deploy e memória; pausa leva até 30 s (cache) em vez de imediata; ações do painel dependem do varredor para enviar; mais coisas no servidor compartilhado |

Mitigações da A: (1) todo trabalho de CPU (ler XLSX/CSV, PDF) roda em `asyncio.to_thread`; (2) timeouts em tudo (banco 30 s, Gemini 60 s, IAM 3 s); (3) o painel não é importado pelo caminho do webhook (teste de arquitetura: `app.services` e `app.api.webhook` não importam `app.panel`; pausa e marca vivem em PF justamente por isso); (4) `PANEL_ENABLED=false` desliga o pacote inteiro (rotas e laços); (5) **separar depois é só configuração** (`app.panel_main:app` + segundo bloco PM2), porque tudo do painel já mora em `app/panel/`. Gatilho para separar: p95 do webhook > 1 s por causa do painel, ou segundo administrador com uso intenso.

### 4.2 Componentes e layout

Raiz do repositório como no AUTO-1 (`frontend/` ao lado de `backend/`; `app/paths.py` aponta para `frontend/{templates,static}`).

```
backend/app/
  main.py                      (MODIFY, arquivo de PF, aditivo: monta app.panel se PANEL_ENABLED; headers do painel)
  config.py                    (MODIFY, aditivo: variáveis de 4.11)
  paths.py                     (NEW: TEMPLATES_DIR, STATIC_DIR)
  services/
    faq_reader.py              (NEW, compartilhado: contrato de leitura da FAQ pelo bot, 4.7)
  panel/
    __init__.py, routes.py     (monta os routers abaixo)
    deps.py                    (current_admin, require_csrf, brand_scope)
    values.py                  (classificações, status, motivos, rótulos PT-BR)
    auth/ {sessions.py, iam.py, csrf.py}
    api/ {system.py, tickets.py, inbox.py, escalados.py, faq.py, pessoas.py, metrics.py,
          alerts.py, relatorios.py, logs.py, assistant.py}
    services/ {scope.py, formatting.py, mask.py, tickets_read.py, tickets_admin.py, inbox_read.py,
          faq_admin.py, faq_validation.py, faq_import.py, people_read.py, people_import.py,
          import_batches.py, metrics_read.py, reports.py, report_pdf.py, lgpd.py, retention.py,
          audit_read.py, monitor.py, workers.py}
    assistant/ {provider.py, gemini.py, disabled.py, agents.py, tools.py, service.py, log.py}
backend/migrations/101..107_*.sql   (bloco 100+ reservado ao painel, 4.3.1)
frontend/
  templates/ (4.5)
  static/{css/input.css, css/tailwind.css, js/<uma por tela>.js, vendor/*, img/bots/*}
  tailwind.config.js, package.json, e2e/ (Playwright)
backend/tests/panel/ {unit,integration,security}
```

**Não são do painel (donos: PF):** `services/flags.py` (chaves `paused`, `brand_enabled:*`, `team_group_enabled`, cache com invalidação na escrita), `services/reply_policy.py` (`FlagsReplyPolicy`, instalada pelo `main.py` de PF e consultada por `outbox.dispatch`), `services/tickets.py` (cria `chat.tickets`), `alerts.raise_alert`. O painel os **chama**; não os cria nem os altera.

Regra de camadas: `panel/api/*` só HTTP e render; `panel/services/*` fala com o banco; escritas sempre por serviço transacional que grava auditoria na mesma transação; ações sobre pessoas passam **sempre** pelos serviços de PF (nunca SQL solto sobre `access.users`). O JavaScript é **um arquivo por tela** (não um `alpine-components.js` único como no AUTO-1), para tarefas paralelas terem escopos de escrita disjuntos.

### 4.3 Modelo de dados (acréscimos aditivos)

#### 4.3.1 Migrations

Runner de PF (`python -m app.db.migrate`, ADR 2026-10-23), que ordena pelo prefixo numérico e recusa prefixo duplicado. **Numeração:** PF usa `001–099` (hoje `001–005`); o painel usa o bloco `101..107`. Todas imutáveis, só para frente, aplicadas só com confirmação do Caio.

| Migration | Conteúdo |
|---|---|
| `101_content_faq.sql` | schema `content`; `content.faq_items`, `content.faq_item_versions`; gatilho que incrementa `ops.system_state['faq_revision']` |
| `102_tickets_panel.sql` | **só o que é do painel sobre `chat.tickets` (criada por PF `005`):** coluna gerada `search_key`, índices `tickets_brand_idx` e `tickets_status_idx`, e `CREATE OR REPLACE VIEW ops.v_tickets` acrescentando `search_key` (só colunas a mais, como PF 4.2.3 permite). Não cria tabela de ticket |
| `103_ops_panel.sql` | `ops.panel_sessions`, `ops.import_batches`, `ops.assistant_questions`; índices de `ops.audit_log` e `ops.alerts` |
| `104_access_search.sql` | `access.users.search_key` (coluna gerada) |
| `105_roles_assistant.sql` | papel `jarvis_assistant` (somente leitura) + schema `assistant` com views sem PII (4.10) |
| `106_faq_origin_check.sql` | reservada: ajustes de CHECK da FAQ se a tarefa Dados (8.4) pedir novos valores de `origin` |
| `107_trgm_optional.sql` | **opcional**, só se `pg_trgm` estiver disponível: índices GIN de busca (risco 9) |

A carga das 112 perguntas não é migration: é o script `python -m app.cli faq seed` (4.7).

#### 4.3.2 FAQ (schema `content`)

```sql
CREATE SCHEMA IF NOT EXISTS content;
CREATE TABLE content.faq_items (
  id            bigserial PRIMARY KEY,
  brand         text NOT NULL CHECK (brand IN ('todas','jungle','plant_power','possible','a_tal_da_castanha','zaya')),
  category      text NOT NULL CHECK (category IN ('PRODUTO','PRECO','ARGUMENTO','MATERIAL','CAMPANHA')),
  question      text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 500),
  question_key  text NOT NULL,               -- normalizada pela aplicação (sem acento, minúscula, espaços colapsados)
  answer        text,                        -- NULL permitido só com bot_responde = false
  variations    text[] NOT NULL DEFAULT '{}',
  bot_responde  boolean NOT NULL,
  active        boolean NOT NULL DEFAULT true,
  origin        text NOT NULL CHECK (origin IN ('manual','import','seed','nielsen','gmr')),
  origin_ref    text,                        -- id do lote de importação ou da carga (tela Dados, futura)
  version       integer NOT NULL DEFAULT 1,  -- controle otimista (PN-26)
  created_at timestamptz NOT NULL, created_by text NOT NULL,
  updated_at timestamptz NOT NULL, updated_by text NOT NULL,
  deactivated_at timestamptz, deactivated_by text,
  CONSTRAINT answer_required CHECK (answer IS NOT NULL OR bot_responde = false)
);
CREATE UNIQUE INDEX faq_active_question_uq ON content.faq_items (brand, question_key) WHERE active;  -- PN-23
CREATE INDEX faq_bot_read_idx ON content.faq_items (brand, category) WHERE active;                  -- leitura do bot
CREATE INDEX faq_list_idx ON content.faq_items (active, brand, id);
CREATE TABLE content.faq_item_versions (    -- uma linha por mudança (PRD: toda alteração gera versão com autor e data)
  item_id bigint NOT NULL REFERENCES content.faq_items(id), version integer NOT NULL,
  snapshot jsonb NOT NULL,                  -- brand, category, question, answer, variations, bot_responde, active
  change_kind text NOT NULL CHECK (change_kind IN ('create','update','deactivate','reactivate','import','seed')),
  changed_at timestamptz NOT NULL, changed_by text NOT NULL, PRIMARY KEY (item_id, version));
```
Variações ficam em `text[]` (como o AUTO-1) em vez de tabela filha: não têm atributos próprios; o histórico está em `faq_item_versions`. A FAQ não tem PII (a validação proíbe telefone, e-mail e link), então o snapshot não entra na exclusão LGPD.

#### 4.3.3 Tickets (de PF: `chat.tickets` e `ops.v_tickets`)

**PF é a dona** da entidade única de ticket (R1 aceito): `chat.tickets` e a visão `ops.v_tickets` (PF migration `005`, PF spec 4.2.3), com `kind` (`question`|`media`), `brand`, `classification` (7 valores), `status` (`answered_bot`, `answered_human`, `awaiting`, `send_error`), `escalated`, `urgency`, `message`, `summary`, `escalation_reason`, `confidence`, `answered_by/at`, `responsible`, `meta`, `created_at/updated_at` e os índices `tickets_created_idx`, `tickets_user_idx`, `tickets_awaiting_idx`. O AC-36 de PF grava `kind='media'`, `classification='MIDIA'`, `escalated=true`, `status='awaiting'`, `urgency='urgente'`. Quem grava `kind='question'` é o módulo de resposta (fora deste spec).

O painel **lê só por `ops.v_tickets`** e **escreve só por `panel/services/tickets_admin.py`** (`UPDATE chat.tickets ... WHERE status='awaiting'`, concorrência do PN-17; reclassificar; urgência), conforme a tabela de contrato de PF 4.2.8. A migration `102` do painel só acrescenta:

```sql
ALTER TABLE chat.tickets ADD COLUMN search_key text GENERATED ALWAYS AS (lower(translate(ticket_number||' '||coalesce(message,''),
  'áàâãäéèêëíìîïóòôõöúùûüçÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ','aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC'))) STORED;
CREATE INDEX tickets_brand_idx  ON chat.tickets (brand, created_at DESC);
CREATE INDEX tickets_status_idx ON chat.tickets (status, created_at DESC);
CREATE OR REPLACE VIEW ops.v_tickets AS SELECT * FROM chat.tickets;   -- recria para expor search_key (colunas só a mais)
```
Vocabulário: Escalado = `escalated AND status='awaiting'`; "Respondido por humano" é gravado só pelo painel ao concluir (nada é enviado ao vendedor). Hoje (antes do módulo de resposta) só existem tickets de mídia; as telas funcionam com fixtures e ficam vazias com elegância (PN-43).

#### 4.3.4 Demais tabelas

```sql
CREATE TABLE ops.panel_sessions (          -- sessão própria sobre o cookie do IAM (4.4.2)
  id_hash text PRIMARY KEY,                 -- sha256 do cookie opaco; nunca o valor
  email text NOT NULL, name text NOT NULL, csrf_secret text NOT NULL,
  created_at timestamptz NOT NULL, last_seen_at timestamptz NOT NULL,
  last_iam_check_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, revoked_at timestamptz);
CREATE INDEX panel_sessions_exp_idx ON ops.panel_sessions (expires_at);

CREATE TABLE ops.import_batches (          -- conferência de importação (FAQ e pessoas)
  id uuid PRIMARY KEY, kind text NOT NULL CHECK (kind IN ('faq','people')),
  actor text NOT NULL, brand text, filename text NOT NULL, sha256 text NOT NULL, size_bytes integer NOT NULL,
  rows jsonb NOT NULL,                      -- linhas com situação; 'people' contém PII: apagado ao gravar, cancelar ou expirar
  summary jsonb NOT NULL,                   -- contadores e motivos, sem PII
  status text NOT NULL CHECK (status IN ('preview','committed','cancelled','expired')),
  created_at timestamptz NOT NULL, expires_at timestamptz NOT NULL, finished_at timestamptz);

CREATE TABLE ops.assistant_questions (
  id bigserial PRIMARY KEY, asked_at timestamptz NOT NULL, asked_by text NOT NULL, agent text NOT NULL,
  question text NOT NULL, answer text, status text NOT NULL CHECK (status IN ('ok','error')),
  tools_used text[] NOT NULL DEFAULT '{}', tokens_in integer, tokens_out integer, latency_ms integer, error_code text);
CREATE INDEX assistant_q_idx ON ops.assistant_questions (asked_at DESC);

CREATE INDEX audit_at_idx     ON ops.audit_log (at DESC);
CREATE INDEX audit_action_idx ON ops.audit_log (action, at DESC);
CREATE INDEX audit_target_idx ON ops.audit_log (target_type, target_id);
CREATE INDEX alerts_unack_idx ON ops.alerts (created_at DESC) WHERE acknowledged_at IS NULL;

ALTER TABLE access.users ADD COLUMN search_key text
  GENERATED ALWAYS AS (lower(translate(name||' '||company, <mesmo mapa de acentos>))) STORED;
```
Chaves em `ops.system_state` (JSONB): `paused`, `brand_enabled:<marca>` (ausente = ligada) e `team_group_enabled` são gravadas por `flags` de PF; `faq_revision` pelo gatilho da migration 101; `unknown_first_text_display` (chave de exibição do PN-37, padrão false) pelo painel via `flags.set_bool`.

`ops.alerts.kind` é texto livre (sem migration). Ganha: `volume_spike`, `error_rate`, `api_failure`, `injection_attempt`, `pause`, `resume`, `brand_on`, `brand_off`; PF já usa `rate_limit`, `send_failure` (rótulo "Falha de API"), `welcome_failed` e `identity_conflict` (os dois últimos são sistema/segurança e aparecem na tela; PN-44 não os cita, Q-P5).

### 4.4 Autenticação, autorização e CSRF

#### 4.4.1 Gate de admin (PN-1, PN-2)
`current_admin` (dependência FastAPI) roda em **toda** rota de `app/panel` (inclusive fragmentos HTMX, JSON do assistente e `.pdf`): sem sessão → 302 ao IAM (HTML) ou 401 (JSON); sessão válida cujo e-mail **não está em `PANEL_ADMIN_EMAILS`** → 403 sem consultar dados, e grava `panel.forbidden` na auditoria (no máximo 1 por minuto por e-mail). A autoridade é a lista de e-mails em configuração (hoje só o Caio), **não** `is_superadmin` do IAM, porque o modelo de papéis por aplicação do IAM não está documentado (Q-P1). Teste de varredura percorre `app.routes` e falha se existir rota de `/dashboard`, `/api/assistant` ou estático privado sem a dependência (exceções: entrada/saída do login, `/health`, estáticos públicos).

#### 4.4.2 Sessão do painel sobre o cookie do IAM (PN-1, PN-3)
Sem sessão, lê `iam_session` (cookie do domínio pai, como no AUTO-1), chama `whoami` (cliente copiado de `services/iam.py`: LRU 30 s, single-flight, disjuntor 3 falhas → 10 s, timeout 3 s, allow-list SSRF) e cria uma linha em `ops.panel_sessions`, entregando o cookie opaco `jarvis_panel` (`HttpOnly; Secure; SameSite=Lax; Path=/`, 256 bits aleatórios; só o hash vai ao banco). Revalida no IAM a cada 5 min; 401 do IAM revoga a sessão; **IAM fora do ar não derruba sessão viva**, que segue até `expires_at` (ociosa 2 h, absoluta 12 h; Q-P2). Sem sessão e IAM fora do ar: página "Login corporativo indisponível, tente em alguns minutos" (503). Sair revoga a linha. Rejeitadas: (a) só cache do `whoami` como no AUTO-1 (30 s: com o IAM fora, o painel cairia em 30 s, contra o PN-3); (b) cache em memória com tolerância longa (some no reinício do PM2). O "state inválido" da nota de teste do PN-1 vira "`next` fora da lista de hosts permitidos é descartado": o fluxo do IAM é redirecionamento com `next`, sem OAuth state (Q-P1).

#### 4.4.3 CSRF (PN-4)
Dois controles, ambos obrigatórios em todo POST/PUT/DELETE: (1) **token** `HMAC(PANEL_CSRF_SECRET, csrf_secret_da_sessão)` enviado no cabeçalho `X-CSRF-Token` (HTMX via `hx-headers` no `<body>`) ou no campo oculto `csrf` (formulários e uploads), comparado em tempo constante; (2) checagem de `Origin`/`Referer` contra o host público (padrão `_check_csrf` do AUTO-1, que sozinho não cumpre "token" do PN-4). Falhou qualquer um: 403, nada alterado. Exportação LGPD e exclusão são POST (não GET) para não serem disparadas por link ou prefetch.

#### 4.4.4 Cabeçalhos e cache
Padrão do AUTO-1 (`X-Frame-Options: DENY`, `nosniff`, HSTS, `Referrer-Policy`, `Permissions-Policy`) com CSP `script-src 'self'` **sem hashes de CDN** (htmx, Alpine CSP e Chart.js em `/static/vendor/`). `Cache-Control: no-store, private` em **todas** as respostas HTML e JSON do painel (o AUTO-1 só faz isso em algumas rotas). Imagens e vendor: cache longo com `?v=<mtime>` (padrão `static_v` do AUTO-1).

### 4.5 Rotas, templates e telas

Prefixo `/dashboard` (igual ao AUTO-1 e ao link do e-mail de PF: `PANEL_BASE_URL/dashboard/alerts`). Convenção HTMX: toda lista é uma página completa; se o pedido traz `HX-Request`, devolve só o fragmento (`hx-target`); filtros e paginação usam `hx-get` com `hx-push-url`. Alpine (build CSP) cuida só de estado de UI (modal, drawer, seleção, abas) em componentes registrados; nenhuma expressão inline complexa nem `onclick` inline. Modal e drawer ficam em `base.html`; **nunca** `confirm()`/`alert()`. Todas as rotas exigem `current_admin`; POST exige CSRF.

| Tela | Rotas (método e caminho) | Templates |
|---|---|---|
| Entrada e sistema | `GET /` e `GET /dashboard` → 302 `/dashboard/tickets`; `POST /dashboard/sair`; `GET /dashboard/marca?b=<slug>` (grava cookie, volta por `Referer` validado); `GET /dashboard/estado` (fragmento: faixa de pausa e contagem de alertas não vistos, `hx-trigger="every 30s"`) | `base.html`, `_nav.html`, `_banner_estado.html`, `403/404/500/503_iam.html`, `_macros.html` (chips de status/classificação/marca, avatar do agente, telefone mascarado) |
| Tickets | `GET /dashboard/tickets` (q, classification, status, days, page); `GET /dashboard/tickets/{n}` (drawer); `POST .../{n}/concluir`; `POST .../{n}/reclassificar`; `POST .../{n}/urgencia` | `tickets.html`, `_tickets_kpis.html`, `_tickets_tabela.html`, `_ticket_drawer.html` |
| Inbox | `GET /dashboard/inbox` (aba conversas/midias, q, so_aguardando, page); `GET /dashboard/inbox/{user_id}` (chat); `POST /dashboard/inbox/{user_id}/concluir`; `POST /dashboard/inbox/concluir-lote` | `inbox.html`, `_inbox_lista.html`, `_inbox_chat.html` (usa `_ticket_drawer.html`) |
| Escalados | `GET /dashboard/escalados` (midias=1); `POST /dashboard/escalados/{n}/mover` (destino=concluida) | `escalados.html`, `_kanban_coluna.html`, `_kanban_cartao.html` |
| FAQ | `GET /dashboard/faq` (brand, category, view=ativas/desativadas, q, page); `GET /dashboard/faq/nova?ticket=J-nnnn` (modal; preenche pelo ticket, **nunca** pelo texto na URL); `POST /dashboard/faq/nova`; `GET|POST /dashboard/faq/{id}/editar`; `POST .../{id}/desativar`; `.../reativar`; `GET /dashboard/faq/importar` (modal); `GET .../importar/modelo` (.xlsx); `POST .../importar` (envia e cria lote); `GET .../importar/{lote}` (conferência); `POST .../{lote}/gravar`; `POST .../{lote}/cancelar` | `faq.html`, `_faq_linha.html`, `_faq_form.html`, `_faq_importar_form.html`, `faq_importar_previa.html` |
| Pessoas | `GET /dashboard/pessoas` (aba, q, filtro, page); `GET|POST /dashboard/pessoas/nova` (`?pedido=<attempt_id>`); `GET /dashboard/pessoas/{id}` (ficha); `POST .../{id}/revogar`; `.../reativar`; `.../reenviar`; `POST /dashboard/pessoas/pedidos/{attempt_id}/ignorar`; importação: `GET /dashboard/pessoas/importar`, `.../importar/modelo` (.csv), `POST .../importar`, `GET .../importar/{lote}`, `POST .../{lote}/gravar|cancelar`; LGPD: `POST .../{id}/exportar` (anexo JSON), `POST .../{id}/excluir-dados` | `pessoas.html`, `_pessoas_lista.html` (3 abas), `_pessoa_form.html`, `_pessoa_revogar.html`, `_pessoa_ficha.html`, `_pessoa_lgpd.html`, `pessoas_importar_previa.html` |
| Métricas | `GET /dashboard/metrics?periodo=hoje\|7\|30` | `metrics.html`, `_metrics_*.html` (dados em `<script type="application/json">`, lidos por `metrics-charts.js`; compatível com CSP) |
| Alertas | `GET /dashboard/alerts?filtro=todos\|nao_vistos\|criticos&abrir=<id>`; `POST /dashboard/alerts/{id}/ack`; `POST /dashboard/control/pausa` (`desired=pause\|resume`); `POST /dashboard/control/marca/{marca}` (`desired=on\|off`) | `alerts.html`, `_alert_card.html`, `_control_pausa.html`, `_control_marcas.html` (modal de confirmação na página) |
| Relatórios | `GET /dashboard/relatorios?periodo=`; `GET /dashboard/relatorios/mensal.pdf?mes=YYYY-MM` | `relatorios.html` |
| Logs e Erros (Admin) | `GET /dashboard/logs?aba=problemas\|servidor\|auditoria\|retencao` (Q-P3: auditoria e retenção são abas de Logs, sem novo item de menu) | `logs.html`, `_logs_problemas.html`, `_logs_servidor.html`, `_logs_auditoria.html`, `_logs_retencao.html` |
| Perguntas (Admin) | `GET /dashboard/assistente-perguntas` | `assistente_perguntas.html` |
| Assistente | `GET /api/assistant/status`; `POST /api/assistant/chat` (JSON; CSRF) | `_assistant_widget.html` (estado desligado incluso), `static/js/assistente.js` |
| Estáticos | `/static/*` (nginx serve direto) | `img/bots/*` (cópia otimizada de `docs/designs/bots/`), `vendor/{htmx,alpine-csp,chart}.min.js`, `marked`, `purify` |

O visual vem do protótipo: `tailwind.config.js` extrai cores (`ink`, `mint`, `lavender` etc.) e fontes da configuração do `painel-prototipo.html`; `npm run build:css` roda na máquina de desenvolvimento e `tailwind.css` minificado é versionado, então **o servidor não precisa de Node**. O protótipo é vanilla JS com `innerHTML`; a versão real é renderizada no servidor (Jinja) com `autoescape` ligado (proibido `|safe` fora de marcadores revisados).

### 4.6 Escopo por marca (PN-5, PN-6)
Cookie `jarvis_brand` (`todas|jungle|plant_power|possible|a_tal_da_castanha|zaya`; `HttpOnly; Secure; SameSite=Lax`; 1 ano). `deps.brand_scope` valida contra a lista (inválido → `todas`) e devolve um `Scope` com `sql(coluna, params)`. Regras: **tickets** `brand = $b OR (brand IS NULL AND kind='media')` (mídia sem marca aparece em qualquer marca); **Inbox** a conversa entra se existir ao menos um ticket da marca (ou mídia sem marca); **FAQ** `brand IN ($b,'todas')`; **Alertas** `payload->>'brand' = $b OR payload->>'brand' IS NULL`; **Métricas/Relatórios** contam tickets por marca (mensagens sem ticket só entram em "Todas as marcas"; limitação explícita); **Pessoas, Logs e Perguntas** ignoram o cookie. O seletor mostra o rosto do agente de cada marca (`img/bots/rosto-*.png`).

### 4.7 FAQ: edição, propagação em até 5 minutos, importação e carga inicial

**Escrita** (`faq_admin`): transação única = validar (`faq_validation`, função pura: pergunta 1-500, resposta obrigatória se `bot_responde`, proíbe telefone/e-mail/link na resposta e nas variações com a mesma expressão do protótipo, duplicata por `question_key` ativa na marca, variações vazias descartadas, lista todos os problemas de uma vez) → `UPDATE ... WHERE id=$1 AND version=$2` (0 linhas = "Este item mudou desde que você abriu", mostra o texto atual e só grava com `force=1` explícito; PN-26) → linha em `faq_item_versions` → auditoria. Desativar e reativar não apagam (PN-24).

**Como o bot lê a FAQ (PN-25).** O contrato é `services/faq_reader.py`: `FaqReader.snapshot(brand) -> list[FaqItem]` lê `content.faq_items WHERE active AND brand IN ($1,'todas')` e guarda o resultado em memória **por revisão**. Todo INSERT/UPDATE em `content.faq_items` (gatilho no banco, cobre importação, script e SQL manual) incrementa `ops.system_state['faq_revision']`; o leitor consulta essa revisão (uma leitura por pergunta do vendedor) e recarrega quando mudou; teto de idade do cache `FAQ_CACHE_MAX_AGE_SECONDS=60` como cinto e suspensório. Pior caso 60 s, muito abaixo dos 300 s do PRD. Rejeitadas: sync por planilha/n8n (atraso e dois donos da verdade), TTL fixo de 5 min (estoura o limite sem folga), LISTEN/NOTIFY (conexão dedicada e reconexão; ganho nulo nesta escala). O recuperador real é de incremento futuro; este spec entrega o contrato, o leitor e o teste com relógio injetável. `bot_responde=false` faz o tema escalar (o leitor devolve o item com a flag; a decisão é do módulo de resposta).

**Importação com conferência (PN-27..29).** `POST .../importar` lê o arquivo em stream com teto (2 MB), usa `asyncio.to_thread` para `openpyxl` (`read_only=True`, `data_only=True`) ou CSV (delimitador `,` ou `;`, UTF-8 com BOM), valida o cabeçalho (`pergunta` e `resposta` obrigatórias; `categoria`/`assunto` e `bot responde` opcionais), o limite de 200 linhas, e classifica cada linha (Entra, Fica de fora, Erro com motivo) com as mesmas regras de `faq_validation`. Nada vai a `content.faq_items`: grava `ops.import_batches` (`status='preview'`, TTL 2 h) e redireciona à conferência, que pode ser reaberta. `gravar`: numa transação, `UPDATE import_batches SET status='committed' WHERE id=$1 AND status='preview' RETURNING` (reabrir ou clicar duas vezes não duplica) → **revalida** cada linha contra a FAQ atual → insere as que entram (`origin='import'`, `origin_ref=<lote>`, categoria escolhida na tela) + versões + auditoria; qualquer falha reverte tudo. `cancelar` marca `cancelled`. Rejeitada a conferência sem estado do AUTO-1 (JSON em campo oculto): serve para FAQ, mas na importação de pessoas levaria PII ao HTML e permite adulteração; o lote também serve de registro do upload (`sha256`, tamanho, autor).

**Carga inicial (PN-30).** `python -m app.cli faq seed --file piloto/guia.json --by importacao-inicial` (aproveita a extração já feita no piloto; conferir 112): idempotente por `(brand, question_key)`, `origin='seed'`, as gerais entram em `todas`, temas de saúde sem resposta aprovada entram com `bot_responde=false`. As perguntas Nielsen e GMR do piloto **não** entram aqui (tela Dados, 8.4).

### 4.8 Pessoas, revogação, importação de pessoas e LGPD

- **Leitura** (`people_read`): três abas por `status` (Autorizadas = `active|pending_optin`; Retiradas = `revoked|declined`; Pedindo acesso = `access_attempts WHERE user_id IS NULL AND dismissed_at IS NULL`, por `last_attempt_at DESC`, destaque com `attempt_count >= 3`). Busca única normalizada em SQL: `search_key LIKE '%'||$q||'%'` (nome+empresa), UF `= ANY(ufs)` se a consulta tem 2 letras, telefone por dígitos contra `phone_key`. 50 por página, "X de Y". Telefone completo só na ficha; nas demais telas, máscara `+55 85 9••••-1201`.
- **Escritas só pelos serviços de PF**: cadastrar = `users.register(conn, data, actor=..., actor_type="panel", enqueue_welcome=True, settings=settings)` (R2; 8 campos obrigatórios; erro nomeia o campo; boas-vindas pela fila `outbound_messages`); revogar = `users.revoke(conn, user_id, reason, actor, actor_type="panel")` com o motivo mapeado da lista fixa (Saiu da empresa → `left_company`; Troca de número → `number_change`; Fim do contrato do parceiro → `partner_contract_end`; Reconciliação mensal (3C) → `monthly_reconciliation`; Recusou o aviso → `declined_notice`; `other` não é oferecido); reativar/reenviar = `users.resend` (PF AC-35); ignorar = `attempts.dismiss`. **O próprio administrador não tem botão Revogar**, e a regra é conferida também no servidor (teste), não só na tela.
- **Revogação em até 1 minuto (PN-34/35):** o painel não cacheia status de usuário. A decisão de acesso de PF lê o banco a cada mensagem, então o bloqueio vale na mensagem seguinte ao COMMIT (≤ 1 s; os 60 s do AC são folga), e o despachante de PF relê o status antes de enviar (`revoked_before_send`). O painel só garante: chamada transacional ao serviço, resposta ao admin só depois do COMMIT, auditoria na mesma transação.
- **Primeira mensagem de desconhecido (PN-37):** `unknown_first_messages` só é lida na aba Pedindo acesso e só se `system_state['unknown_first_text_display']=true` (padrão false, ligada após a validação jurídica), por uma função exclusiva (`people_read.pending_requests`); teste varre templates e consultas para garantir que nenhum outro lugar a referencia. Nunca vai a log, e-mail, Alertas, Inbox, Tickets, Logs nem às views do assistente.
- **Importar pessoas (PN-40):** mesmo pipeline de lote da FAQ (`kind='people'`); cabeçalho `nome,telefone,perfil,empresa,ufs,canais,marcas,gestor`, múltiplos separados por `|` ou `;` (Q15); validação por linha com `users.validate_row(row, now)` de PF (R2: pura, devolve valor validado ou erros por campo); duplicata no arquivo e na base (mesmo `phone_key`, cobre o 9º dígito) fica de fora; grava só as válidas, cada uma via `users.register` (política "só as válidas" do painel; o CLI de PF usa as mesmas funções com "tudo ou nada"); o lote perde as linhas (PII) ao gravar, cancelar ou expirar (1 h).
- **LGPD (`lgpd.py`).** *Exportar* (PN-56): JSON com cadastro, histórico de aceite, mensagens (corpo, dentro da retenção), respostas, tickets e tentativas da pessoa; anexo com `Cache-Control: no-store`; auditoria sem PII. *Excluir* (PN-57; Q7: anonimizar, padrão): o modal exige digitar o nome; numa transação: revoga (serviço), `users.name/phone_e164/phone_key/bsuid = 'anon:<id>'`, `message_trace.body = NULL`, `outbound_messages.to_phone/body/template = NULL`, `chat.tickets.message/summary = NULL`, apaga `unknown_first_messages` e anonimiza `access_attempts` do mesmo `phone_key`, e *melhor esforço* em `ops.assistant_questions` (resposta que cita o nome vira "[removido a pedido do titular]"); auditoria `lgpd.delete` com contagens. Irreversível; some das buscas porque `search_key` é gerada do nome anonimizado.
- **Retenção (PN-58, `retention.py`).** Laço diário (02:30 America/Sao_Paulo, trava consultiva) apaga em lotes de 5.000, na ordem das chaves estrangeiras (`chat.tickets` → `outbound_messages` → `message_trace`), o que passou de 12 meses (`RETENTION_MONTHS`), mais `unknown_first_messages` por `stored_at`, `import_batches` e `panel_sessions` vencidos e `assistant_questions` com mais de 12 meses; grava `retention.purge` com contagens. Toda consulta das telas tem piso `>= now - 12 meses`, então nada vencido aparece mesmo antes da rotina. A aba Retenção mostra o volume por mês e o que expira em 30 dias. Na primeira semana roda com `RETENTION_DRY_RUN=true` (só conta).

### 4.9 Controles do Jarvis, alertas e e-mail

- **Estado e política são de PF** (R4 aceito): `flags` (PF) guarda `paused` e `brand_enabled:<marca>` em `ops.system_state`, com cache de 30 s **invalidado na escrita no mesmo processo** (escrita por outro processo vale em ≤ 30 s); `reply_policy.FlagsReplyPolicy` é instalada pelo `main.py` de PF e consultada por `outbox.dispatch` antes de qualquer envio. O painel só **chama** `flags.set_bool(...)`. Escrita idempotente: a requisição envia o estado desejado (`pause|resume`, `on|off`), nunca "alternar"; a escrita informa se mudou, e só se mudou o painel grava auditoria **e** `alerts.raise_alert(kind='pause'|'resume'|'brand_on'|'brand_off', ..., payload={'brand': ...})`, que por PF já cria a entrega de e-mail e a de grupo conforme a chave. O modal abre com o estado atual (PN-48).
- **Efeito da pausa (decidido em PF 4.11/4.9):** bloqueia respostas conversacionais (`under_construction`, `media_ack`, `rate_limit_notice` e as futuras do módulo de resposta); **não** bloqueia mensagens de consentimento (boas-vindas, lembrete, confirmação, recusa), por uma lista de isenções em `FlagsReplyPolicy`. Mensagens continuam recebidas e guardadas, desconhecido segue em silêncio, o webhook responde 200. Marca desligada: o gate de marca (consulta de `brand_enabled:<marca>` em `flags`) é usado pelo módulo de resposta depois da classificação; marca desligada cria o ticket aguardando (`escalation_reason='brand_off'`) sem chamar o modelo. O painel testa isso com um provedor de teste.
- **Monitor** (`monitor.py`, laço de 10 min com `pg_try_advisory_lock`): `volume_spike` (mensagens/hora acima de 3x a mediana das mesmas horas dos 7 dias anteriores e mínimo absoluto), `error_rate` (falhas de envio + `decision='error'` sobre o total na janela), `api_failure` (falhas definitivas de envio na Meta, complementa o `send_failure` de PF). `injection_attempt` só é exibido: o detector pertence ao módulo de resposta. Dedup por `dedup_key` via `raise_alert`. Os limiares ficam em configuração e são calibrados no piloto.
- **E-mail de alertas: reuso total de PF** (`deliveries` + Resend, sem PII, retentativa). O painel só grava alertas por `raise_alert` e entrega o destino do link `PANEL_BASE_URL/dashboard/alerts?abrir=<id>`, que destaca o cartão. Escalado, desconhecido e mídia não chamam `raise_alert` (teste de contagem). `raise_alert(..., email=True)` já existe em PF (R3, padrão `True`): se a resposta à Q17 for "sem e-mail para ações do próprio Caio", basta o painel passar `email=False` nos alertas de pausa, retomada e marca; hoje vale o PN-65 aprovado (envia).
- **Tela de Alertas:** cartões com severidade, tipo (rótulo PT-BR), data, texto, selo `AUTO: ação`, detalhes recolhidos, "Abrir J-xxxx" (drawer) e "Marcar como visto" (`acknowledged_*`).

### 4.10 Assistente (widget) e Perguntas

**Interface de provedor** (`panel/assistant/provider.py`):
```python
class AssistantProvider(Protocol):
    enabled: bool
    async def chat(self, *, system: str, messages: Sequence[ChatTurn],
                   tools: Sequence[ToolSpec], call_tool: Callable[[str, dict], Awaitable[dict]],
                   max_tool_rounds: int = 5) -> ProviderReply: ...   # texto, tokens_in, tokens_out, tools_used
class DisabledProvider: enabled = False        # sem GEMINI_API_KEY
class GeminiProvider:   enabled = True         # httpx.AsyncClient contra a API REST de generateContent (modelo Flash)
```
`GeminiProvider` usa **httpx** (já fixado) em vez do SDK `google-generativeai` do AUTO-1: sem dependência nova; base validada na allow-list SSRF. API key x Vertex AI (o PRD cita Vertex) é a Q-P6; só `gemini.py` muda.
- **Sem chave:** `GET /api/assistant/status` → `{"enabled": false, "reason": "..."}`; o widget abre, mostra os agentes e os chips **desabilitados** com a mensagem "O assistente será ativado quando a chave do Gemini for configurada"; `POST /api/assistant/chat` responde 503 sem registrar pergunta (nada foi processado).
- **Somente leitura (PN-52):** as ferramentas (`tools.py`) rodam num pool próprio (`ASSISTANT_DATABASE_URL`, papel `jarvis_assistant`) com `SELECT` apenas no schema `assistant` (views: tickets escalados, pedidos de acesso **sem** texto, pessoas sem aceite com nome e sem telefone, métricas agregadas, lacunas da FAQ, falhas de envio, saúde), sessão `default_transaction_read_only=on`, `statement_timeout=5s`. O papel **não tem** acesso a `chat.message_trace.body`, `unknown_first_messages`, telefones nem `audit_log` (resposta padrão à Q14; ampliar é uma view nova). O resultado de ferramenta chega ao modelo como dado delimitado, com instrução fixa de ignorar ordens dentro dele. Ferramentas de escrita **não existem** (o AUTO-1 tem `send_reply`/`mark_lost` e uma flag `somente_leitura`; aqui a garantia está no banco).
- **Agentes** (`agents.py`): Jarvis (coordenador), Duda, Léo, Nico, Rita, Tati; `pick_agent(texto)` sem acento (1 marca citada = agente da marca; nenhuma ou várias = Jarvis), com a detecção do protótipo (`MARCAS_NO_TEXTO`); persona e restrições por agente; teste de varredura garante nenhuma ocorrência de "Beto".
- **Limites:** 12 perguntas/min (limitador do AUTO-1), 4.000 caracteres, histórico máx. 40 turnos mantido no navegador (como no AUTO-1), `ASSISTANT_DAILY_LIMIT` (padrão 200; Q14), timeout 60 s. Falha do modelo: mensagem amigável, painel intacto, linha `status='error'` em Perguntas.
- **Perguntas:** toda resposta entregue (ou erro) vira linha em `ops.assistant_questions`; a página lista com contadores (perguntas em 30 dias, pessoas distintas).

### 4.11 Configuração (acréscimos às de PF)

| Variável | Padrão | Segredo | Observação |
|---|---|---|---|
| `PANEL_ENABLED` | true | não | desliga rotas e laços do painel |
| `PANEL_PUBLIC_HOST` | `jarvis.positiveco.com.br` | não | TrustedHost, `next` do IAM, Origin do CSRF |
| `PANEL_ADMIN_EMAILS` | **sem padrão** | sim | lista separada por vírgula; fora do código e do spec |
| `PANEL_CSRF_SECRET` | **sem padrão** | sim | fail-fast se ausente em produção |
| `PANEL_SESSION_IDLE_MINUTES` / `_ABSOLUTE_HOURS` | 120 / 12 | não | Q-P2 |
| `IAM_BASE_URL` / `IAM_WHOAMI_CACHE_TTL_SECONDS` / `IAM_HTTP_TIMEOUT_SECONDS` | AUTO-1 | não | allow-list SSRF |
| `DEV_AUTH_EMAIL` | vazio | não | só com `PRODUCTION=false` (validador do AUTO-1 recusa em produção) |
| `FAQ_CACHE_MAX_AGE_SECONDS` | 60 | não | PN-25 |
| `RETENTION_MONTHS` / `RETENTION_DRY_RUN` | 12 / true | não | PN-58 |
| `UNKNOWN_FIRST_TEXT_DISPLAY` | false | não | valor inicial; `system_state` sobrepõe |
| `GEMINI_API_KEY` / `GEMINI_MODEL` / `ASSISTANT_DAILY_LIMIT` / `ASSISTANT_DATABASE_URL` | vazio / flash / 200 / vazio | chave e URL sim | vazio = assistente desligado |
| `MEDIA_ACK_ENABLED` | true | não | de PF; confirmado ligado em 2026-10-23 |
| `MONITOR_VOLUME_FACTOR` / `MONITOR_VOLUME_MIN_PER_HOUR` | 3.0 / 30 | não | `volume_spike`: mensagens na última hora > fator × mediana das mesmas horas dos 7 dias anteriores **e** ≥ mínimo (acrescentado na Fase 4, 2026-10-26; calibrar no piloto) |
| `MONITOR_ERROR_RATE` / `MONITOR_ERROR_MIN_MESSAGES` / `MONITOR_WINDOW_MINUTES` | 0.20 / 20 / 60 | não | `error_rate`: (falhas de envio + `decision='error'`) ÷ total na janela ≥ taxa, com pelo menos N mensagens na janela |
| `MONITOR_API_FAILURES` | 5 | não | `api_failure`: falhas definitivas de envio na Meta na janela; dedup de 1 h por tipo em todos |

### 4.12 Estratégia de testes

- **Camadas** (mesmo desenho de PF): `unit/` (puros), `integration/` (Postgres real, marcador `db`), `security/`; pytest-asyncio `auto`. `TEST_DATABASE_URL`; fixture aplica as migrations pelo runner (PF 001–005 + painel 101–107) e faz `TRUNCATE ... CASCADE` por teste; **nunca** o banco do droplet.
- **Dublês:** IAM (`httpx.MockTransport` com 200/401/timeout/5xx), `FakeChannel` e spies de PF (Resend, WAHA), `FakeClock`, `FakeProvider` do assistente, Gemini por `MockTransport`. Fixture de duas marcas, três vendedores (uma marca, duas marcas, só mídia), tickets de todos os status inseridos em `chat.tickets`, FAQ com 25 itens.
- **Renderização de templates** (padrão `test_a11y_templates`/`test_admin_gating` do AUTO-1): renderiza **cada** template com dados de fixture e com listas vazias (PN-43: nada de divisão por zero) e falha se houver `|safe` fora da lista, `confirm(`/`alert(`, "Beto", CDN externo, `onclick` inline (CSP), campo de resposta ao vendedor no Inbox ou drawer, ou modal sem `role="dialog"`.
- **Autorização:** teste que enumera `app.routes` e chama cada uma sem sessão, com sessão não-admin e com sessão admin sem CSRF (PN-2, PN-4); varredura de que nenhum corpo 403 contém dados.
- **Concorrência (Postgres real):** dois `concluir` simultâneos (PN-17), edição concorrente de FAQ (PN-26), dois `POST` de pausa (PN-48), gravar o mesmo lote duas vezes (PN-29).
- **Propagação (PN-25):** FAQ editada, `FakeClock` avança 59 s e 301 s; o leitor reflete a revisão sem esperar o teto.
- **Revogação (PN-34/35):** revoga pelo serviço a partir da rota e envia mensagem pelo webhook de teste; spy de envio sem chamadas.
- **Mapa AC → arquivo:** `tests/panel/` espelha os grupos (`test_access.py`, `test_brand_scope.py`, `test_tickets.py`, `test_inbox.py`, `test_escalados.py`, `test_faq.py`, `test_faq_import.py`, `test_pessoas.py`, `test_people_import.py`, `test_metrics.py`, `test_alerts_controls.py`, `test_email_alerts.py`, `test_assistant.py`, `test_lgpd.py`, `test_retention.py`, `test_perf.py`); cada PN tem ao menos um teste.
- **E2E (Playwright):** login com IAM mockado, marca persistida entre telas, drawer, modais de confirmação, widget (desligado e ligado com provedor falso). Revisão visual manual contra o protótipo, tela a tela.
- **Desempenho (PN-59):** semente sintética de 2.000 usuários, 20 mil mensagens/dia por 30 dias e 200 mil tickets; mede p95 de Tickets, Inbox, Pessoas, FAQ e Métricas (alvo 2 s); marcador `perf` no CI (não bloqueia PR, bloqueia release).
- **Comandos de verificação** (somar aos de PF e registrar em `.claude/CLAUDE.md` ao fim): `pytest backend/tests/panel -q`, `ruff check backend`, `mypy backend/app`, `npm --prefix frontend run build:css` (sem diff em `tailwind.css`), `npx playwright test`.

### 4.13 Trade-offs (resumo; alternativas em `research.json`)

- **Mesmo processo** em vez de dois (4.1): menos operação e efeito imediato dos controles; custo de dividir o worker, mitigado e reversível.
- **Sessão própria sobre o IAM** em vez de só `whoami` com cache: cumpre o PN-3; custo de uma tabela e de uma regra de revalidação.
- **`chat.tickets` única (de PF) lida por `ops.v_tickets`** em vez de duas tabelas de ticket: telas e escritas simples; o plano B (visão `UNION`) foi descartado porque PF aceitou R1.
- **FAQ no banco compartilhado, leitor por revisão** em vez de sync por planilha: propagação em segundos, um só dono do dado.
- **Lote de importação no servidor** em vez de campo oculto: sem PII no HTML e idempotente; custo de uma tabela e de expurgo.
- **Colunas geradas com `translate`** em vez de `unaccent`/`pg_trgm`: nenhuma extensão a instalar num servidor compartilhado; busca por substring sem índice trigram (aceitável na janela padrão; mitigação opcional na migration 107).
- **Gemini por REST/httpx e papel de banco somente leitura** em vez de SDK e flag de código: sem dependência nova e garantia no banco (PN-52).
- **ReportLab para o PDF** em vez de página de impressão: PDF de verdade e testável, ao custo de uma dependência (4.14).

### 4.14 Dependências novas (explícitas)

Além das de PF (fastapi, uvicorn, asyncpg, pydantic-settings, httpx): `jinja2==3.1.4`, `python-multipart==0.0.12`, `openpyxl>=3.1` (versões do AUTO-1; fixar na versão instalada), `reportlab` (PDF mensal; versão a fixar na implementação, Q-P7). Dev: `playwright`, `tailwindcss ^3.4.17` (Node só na máquina de desenvolvimento). Nenhuma biblioteca de IA (Gemini via httpx).

### 4.15 Pedidos a porta-fechada.1: todos aceitos (PF spec 4.11, 2026-10-23)

- **R1 (aceito)** — PF cria `chat.tickets` (migration `005`) e `ops.v_tickets`; o AC-36 grava lá. `ops.escalation_tickets` deixa de existir. Efeito aqui: migration `102` do painel só acrescenta `search_key` e índices (4.3.3); o plano B de visão `UNION` está descartado.
- **R2 (aceito)** — `users.validate_row(row, now)` pura e `users.register(conn, data, actor=..., actor_type="panel", enqueue_welcome=True, settings=settings)` reutilizáveis (lote de CSV = uma transação de quem chama).
- **R3 (aceito)** — `raise_alert(..., email: bool = True)`; `email=False` não cria a entrega de e-mail; a de grupo segue a chave.
- **R4 (aceito)** — PF é dona de `services/flags.py` (`paused`, `brand_enabled:*`, invalidação na escrita) e de `services/reply_policy.py` (`FlagsReplyPolicy`, instalada pelo `main.py` de PF e consultada por `outbox.dispatch`). O painel retirou ambos da sua lista de arquivos; mantém apenas a modificação aditiva de `main.py` (monta o pacote do painel) e `config.py`.

## 5. Requisitos Não Funcionais (detalhe)

- **Desempenho (PN-59).** Cada tela p95 ≤ 2 s com 2.000 usuários e 20 mil mensagens/dia. Paginação no servidor (Tickets 25, Inbox 50, Pessoas 50, FAQ 10, Alertas 100); contagens com `COUNT(*) FILTER` numa consulta; lista do Inbox por `LATERAL` sobre `message_trace (user_id, received_at DESC)`; Métricas com cache em memória de 60 s por (marca, período) (único usuário; Pessoas e Tickets nunca são cacheadas). Fragmentos HTMX evitam recarregar a página. Nenhuma rota do painel bloqueia o laço de eventos (CPU em thread). Pool: 2-10 (aplicação) + 1-3 (assistente, somente leitura); confirmar `max_connections` do Postgres compartilhado (Q-P8).
- **Consultas por tela e índices.**

| Tela | Consulta (resumo) | Índice |
|---|---|---|
| Tickets | KPIs: `COUNT(*) FILTER` sobre `ops.v_tickets WHERE created_at >= now-$days` e escopo de marca; lista com filtros e `LIMIT 25 OFFSET`; busca `search_key LIKE '%q%'` | `tickets_created_idx` (PF), `tickets_brand_idx`, `tickets_status_idx` (painel 102) |
| Inbox | usuários autorizados ou com histórico, `LATERAL` da última mensagem; "só aguardando" = `EXISTS` ticket `awaiting`; `⏳ tempo` = `MIN(created_at)` dos aguardando | `message_trace_user_time_idx` (PF), `tickets_user_idx` (PF), `tickets_awaiting_idx` (PF) |
| Chat | trace do usuário + `outbound_messages` por `inbound_trace_id`, 200 últimas | `message_trace_user_time_idx` |
| Escalados | `awaiting AND escalated`, agrupado por classificação, por urgência e data | `tickets_awaiting_idx` (PF) |
| FAQ | `active`, marca (+ `todas`), categoria, busca em `question_key` e `answer`, `todas` primeiro | `faq_list_idx` |
| Pessoas | por `status`; busca `search_key`/`phone_key`/`ufs`; Pedindo acesso por `last_attempt_at` | `users_status_idx` (PF), índices de `access_attempts` (PF) |
| Métricas | `date_trunc('day', received_at AT TIME ZONE 'America/Sao_Paulo')` agrupado por `access_decision`; `percentile_cont` de `latency_ms` | `message_trace_received_idx` (PF) |
| Alertas | `ORDER BY created_at DESC LIMIT 100`, não vistos | `alerts_created_idx` (PF), `alerts_unack_idx` |
| Auditoria | `at DESC` + filtro de ação/alvo | `audit_at_idx`, `audit_action_idx`, `audit_target_idx` |
| Perguntas | `asked_at DESC` | `assistant_q_idx` |

- **Segurança.** Autorização em toda rota (4.4.1), CSRF duplo, CSP sem CDN, `no-store`, escape automático, uploads com teto de 2 MB, planilha lida sem fórmulas (`data_only`), nome do arquivo nunca usado em caminho, allow-list SSRF (IAM, Gemini), segredos só em ambiente (`SecretStr` mascarado), `PANEL_ADMIN_EMAILS` fora de código, log e spec, assistente com papel de banco sem escrita, logs sem PII (telefone mascarado, sem corpo de mensagem, sem e-mail do destinatário), exportação e exclusão LGPD como POST + auditoria. Limite do painel: 240/60 s por IP (padrão AUTO-1) e 12 perguntas/min no assistente.
- **Privacidade (LGPD).** Matriz em 4.8; primeira mensagem de desconhecido só em Pedindo acesso e atrás de chave; lote de importação de pessoas apagado ao terminar; retenção de 12 meses por rotina e por piso nas consultas; dado de pessoa nunca em e-mail, alerta ou auditoria.
- **Confiabilidade.** Escritas do painel são transacionais com auditoria (falha de auditoria aborta, PN-55); controles idempotentes; IAM fora do ar não derruba sessão viva (PN-3); falha do Gemini, do Resend ou do WAHA nunca afeta tickets, FAQ ou o webhook; `PANEL_ENABLED=false` desliga o painel inteiro sem tocar no Jarvis do WhatsApp.
- **Observabilidade.** Logs JSON de PF com `request_id`; eventos `panel_login`, `panel_forbidden`, `faq_changed`, `import_committed`, `control_changed`, `lgpd_export|delete`, `retention_purge`, `assistant_call`; tudo sem PII.
- **Acessibilidade e idioma.** PT-BR; datas `dd/mm/aaaa` e horário em `America/Sao_Paulo` (filtro Jinja `sp_dt`; o AUTO-1 usa Fortaleza); foco e `role="dialog"` em modal e drawer, verificados no teste de renderização.
- **Manutenibilidade.** Um roteador por tela (o `dashboard.py` do AUTO-1 passa de 2.700 linhas e não é copiado); funções puras (escopo, formatação, validações, normalização) testadas isoladamente; listas de valores em `panel/values.py`; JS por tela.

## 6. Out of Scope

- Responder ao vendedor pelo painel (Inbox e drawer não têm caixa de resposta; a equipe responde pelo grupo de WhatsApp quando a chave estiver ligada, e o painel só registra a conclusão). Encaminhar resposta do grupo ao usuário fica para a história futura de escalonamento.
- Modo conservador (removido) e papéis além de administrador; hoje só o Caio acessa.
- Alertas ou e-mails para escalações, mídia e números desconhecidos. E-mail só para novos alertas; um único destinatário; nenhum e-mail ao vendedor.
- Ativar o grupo da equipe em produção (o caminho é construído, a chave fica desligada; definir grupo e sessão WAHA é configuração posterior).
- Publicação de arquivos a partir do Drive (RF-21 completo), importação de preço, mix e SKUs (RF-22 completo), aprovação de conteúdo por vários donos.
- **Tela "Dados" (Nielsen e GMR → FAQ automática, PRD §10 de 2026-10-22): não faz parte do escopo aprovado de painel.1.** Fica como tarefa de acompanhamento (8.4). O modelo da FAQ já reserva `origin` (`nielsen`, `gmr`) e `origin_ref` para ela.
- Avaliação útil/não útil do vendedor (RF-16): a métrica aparece como "sem avaliações" até existir.
- Processamento de áudio e imagem (PF AC-36: só escala e responde "equipe avisada"); o painel mostra mídia como ticket do tipo Mídia, sem baixar arquivo.
- Criação de tickets de pergunta (bot, classificação, resumo, escalonamento por tema): é do módulo de resposta; o painel entrega as telas e PF a tabela.
- Detector de injection (o painel só exibe o alerta quando o módulo de resposta o gerar).
- Expiração automática de acesso e revalidação em lote (suspensas, PF Q6).
- Social listening, reclamações e demais telas do AUTO-1 que não estão no protótipo.
- Respostas em tempo real (WebSocket); atualização por recarga ou polling simples (a faixa de estado atualiza a cada 30 s).
- Edição do texto dos modelos e avisos do WhatsApp.
- Segundo administrador, papéis e 2FA próprio do painel (2FA, se houver, é do IAM).
- Alterar qualquer arquivo ou tabela de porta-fechada.1 além das adições aditivas listadas em 4.3 e das duas modificações aditivas de `main.py` e `config.py`.

## 7. Test Plan

| Camada | O que testar | Como |
|---|---|---|
| Unit | Escopo por marca (PN-6), formatação de horário, separador de dia e "respondeu em X" (PN-12), validações da FAQ (PN-23), regras de rejeição da importação (PN-28), normalização de telefone com 9º dígito (PN-33), escolha do agente (PN-51), busca sem acento, montagem do corpo do e-mail de alerta sem PII (PN-65), token CSRF, mapeamento motivo→código de revogação | pytest, relógio injetável |
| Integration | Autorização por rota (PN-2, PN-4), fluxos de FAQ e propagação ≤5 min (PN-25), importações (PN-27..29, PN-40, só válidas gravadas), ciclo de Pessoas, primeira mensagem de desconhecido (só a primeira guardada e exibida, só em Pedindo acesso), desconhecido sem alerta nem e-mail (PN-67), concluir/reclassificar com concorrência, escalado sem alerta e sem e-mail (PN-44, PN-62), e-mail de alerta (PN-65), falha do Resend sem bloquear o webhook e com retentativa (PN-66), controles de Alertas e seus efeitos no pipeline, chave do grupo ligada/desligada (spy do WAHA), assistente somente leitura, auditoria, exportação/exclusão LGPD, retenção, resposta `media_ack` no chat | pytest + FastAPI TestClient + Postgres de teste; IAM, WhatsApp, WAHA, Resend e Gemini mockados com spies; fixture de duas marcas e vendedores com tickets mistos |
| Renderização | Todos os templates com dados e vazios (4.12) | pytest + Jinja |
| E2E | Login mockado do IAM, navegação entre telas com a marca persistida, drawer, modal de confirmação (sem confirm() nativo), widget do assistente | Playwright |
| Segurança | Varredura de rotas sem sessão e com não-admin (nenhum dado vaza), CSRF, texto de desconhecido só a primeira mensagem, só em Pedindo acesso e fora de logs e e-mails, e-mail sem telefone, logs sem PII e sem endereço do Caio, conta do assistente sem permissão de escrita | Suíte automatizada |
| Desempenho | p95 ≤ 2 s com a semente sintética (PN-59) | marcador `perf` |
| Manual | Revisão visual contra o protótipo aprovado em cada tela, importação real do Guia de produtos (112), fluxo de revogação com o telefone do Caio no WhatsApp, recebimento real do e-mail de um alerta na caixa do Caio | Roteiro de smoke com o Caio |

Meta: todo PN com ao menos um teste automatizado; telas validadas contra o protótipo antes de aceitar a história.

## Dependências externas e checklist legal

- **Validação jurídica da LGPD** para exibir (e guardar) a primeira mensagem de número desconhecido em Pedindo acesso: item novo no checklist legal, junto da aprovação do aviso de privacidade (`docs/legal/aviso-privacidade.md`). Sem ela, PN-37 fica com a exibição desligada.
- **Emenda de porta-fechada.1** (em andamento, R1 a R4 já aceitos): guardar só a primeira mensagem de desconhecido; permitir Reativar de revogado (PF AC-19/AC-35); campos múltiplos de UFs/canais/marcas e gestor no usuário; escalação, mídia e desconhecido sem registro de alerta no painel (D5).
- **Resend:** conta e domínio de envio reaproveitados do AUTO-1; chave de API e e-mail do destinatário só em configuração do servidor.
- **IAM:** registro do host `jarvis.positiveco.com.br` na lista de destinos permitidos do IAM (Q-P1).

## 8. Plano de Implementação

Insumo para a Fase 3 (`task.md`): marcos em ordem de dependência, não tarefas (a quebra em tarefas de 1-4 h, com lista de arquivos, é a Fase 3). Cada marco deixa a suíte verde e é um commit lógico `type(painel): PAINEL-1.x - ...`.

### 8.1 Marcos

| # | Marco | Entrega | PNs | Horas |
|---|---|---|---|---|
| P0 | Casca do painel | `config`, `paths`, templating Jinja, `base.html`, nav, macros, formatação/máscara, Tailwind + vendor + imagens, `PANEL_ENABLED`, teste de renderização base | PN-43, PN-59 (base) | 14-18 |
| P1 | Acesso | cliente IAM, `panel_sessions`, `current_admin`, CSRF, erros 403/503, escopo de marca, varredura de rotas | PN-1..6 | 14-18 |
| P2 | Banco do painel | migrations 101-105 (sem tabela de ticket, sem `flags`/`reply_policy`: são de PF), fixtures, `faq_reader`, papel `jarvis_assistant` | base de PN-24, 25, 44 | 10-14 |
| P3 | Tickets, Inbox, Escalados | listas, KPIs, drawer, concluir/reclassificar/urgência, chat, lote | PN-7..21, 63 | 32-40 |
| P4 | FAQ | lista, formulário, validação, desativar, importação com lote, semente | PN-22..30 | 28-34 |
| P5 | Pessoas | abas, cadastro, revogar, reativar, pedidos, importação, LGPD exportar/excluir | PN-31..40, 56, 57, 67 | 30-38 |
| P6 | Controles e alertas | Alertas, pausa e marcas (chamam `flags` de PF), monitor, link do e-mail, estado do grupo | PN-44..49, 60, 61, 65, 66 | 16-22 |
| P7 | Métricas e Relatórios | consultas, gráficos, PDF mensal | PN-41, 42 | 18-24 |
| P8 | Assistente e Perguntas | provedor, ferramentas, papel/views, widget, estado desligado, Perguntas | PN-50..53 | 20-26 |
| P9 | Logs, auditoria, retenção | Logs e abas, leitura de auditoria, rotina de retenção | PN-54, 55, 58 | 12-16 |
| P10 | Fechamento | suítes de segurança, E2E, desempenho, revisão visual, mapa PN → teste | todos | 20-28 |
| P11 | Deploy e go-live | nginx, env, runbook, smoke com o Caio | manuais | 6-10 |

Total 220-284 h (esperado ~246): a retirada de `chat.tickets`, `flags` e `reply_policy` (agora de PF) tirou cerca de 4 h de P2. Ordem: P0 → P1 → P2 → (P3 ∥ P4 ∥ P5 ∥ P6 ∥ P7) → P8 (após P6) → P9 → P10 → P11. Escopos de escrita disjuntos para ondas paralelas: um roteador, um conjunto de serviços e um JS por tela; os pontos compartilhados (`base.html`, `_nav.html`, `_macros.html`, `routes.py`, `values.py`, `config.py`, `main.py`) pertencem a P0/P1, e acréscimos nesses arquivos são serializados por uma tarefa dona.

### 8.2 Checklist de go-live (cada passo no servidor exige confirmação do Caio)

1. Pré-requisitos: porta-fechada.1 em produção (migrations 001-005, incluindo `chat.tickets` e `ops.v_tickets`); `pg_dump` do banco `jarvis`; host registrado no IAM (Q-P1).
2. Migrations 101-105 (`--status` antes); criar o papel `jarvis_assistant`.
3. Preencher no `.env` (modo 600, pelo Caio): `PANEL_ADMIN_EMAILS`, `PANEL_CSRF_SECRET`, `PANEL_PUBLIC_HOST`, `IAM_*`; deixar `GEMINI_API_KEY` vazio (assistente desligado) e `RETENTION_DRY_RUN=true`.
4. Deploy por rsync de `backend/app`, `backend/migrations`, `frontend/templates`, `frontend/static` (CSS já compilado), `deploy`; `pm2 restart jarvis --update-env`; `GET /health` 200.
5. nginx: acrescentar os blocos descritos em 8.3 (`nginx -t` e `nginx -s reload`, nunca restart); conferir `X-Real-IP`.
6. Smoke: login do Caio; outro usuário do IAM recebe 403; carga das 112 (`faq seed`); editar uma FAQ e conferir o leitor; cadastrar o telefone do Caio, revogar e reativar; pausar e retomar (e-mail do alerta chega); importar uma planilha pequena de FAQ; áudio de teste cria ticket e mostra o "Bot" no Inbox; conferir que nada de grupo foi enviado.
7. Chaves que continuam desligadas: `UNKNOWN_FIRST_TEXT_DISPLAY`, grupo da equipe, assistente (até haver chave).

### 8.3 Deploy (mesmo processo PM2 e mesmo nginx de PF)

Sem processo novo: o painel roda dentro do `jarvis` de PF (`deploy/ecosystem.config.js` inalterado, `--workers 1`). O site nginx de PF (`deploy/nginx/jarvis.conf.template`) ganha, **além** dos `location = /webhook/whatsapp` e `= /health` que já existem, os blocos: `location /dashboard/` e `location = /` (proxy para o app, `X-Real-IP`, `proxy_read_timeout 30s`, `client_max_body_size 64k`), `location ~ ^/dashboard/(faq|pessoas)/importar$` com `client_max_body_size 3m`, `location /api/assistant/` com `proxy_read_timeout 90s`, e `location /static/ { alias /opt/jarvis/frontend/static/; expires 7d; access_log off; }`. Zona de limite do nginx para `/dashboard/` (além do limitador do app). A restrição de PF "`location /` devolve 404" é mantida para qualquer caminho fora dessa lista. `deploy/nginx/jarvis.conf.template` é arquivo de PF: o painel entrega o trecho como anexo na Fase 3 e a mesclagem é feita pela tarefa de deploy, para não editar o arquivo irmão em paralelo.

### 8.4 Tarefas de acompanhamento (fora de painel.1)

- **painel.2 — Tela Dados (admin):** Nielsen e GMR → FAQ automática com prévia, selos "Nielsen" e "GMR", substituição da carga anterior (antigas vão para Desativadas), conforme PRD §10 de 2026-10-22 (56 + 55 perguntas na base de set/26). Usa `content.faq_items.origin/origin_ref` e a importação com lote. Pendências do PRD: licença Nielsen e nome comercial dos produtos.
- **Módulo de resposta (incremento 2/4):** cria tickets de pergunta em `chat.tickets`, classifica, usa `FaqReader`, o gate de marca de `flags` e o detector de injection.
- **Encaminhar a resposta do grupo ao vendedor** (história de escalonamento).
- **Segundo administrador / papéis / 2FA**, se a operação crescer.

## 9. Plano de Reversão

- **Painel inteiro:** `PANEL_ENABLED=false` + `pm2 restart jarvis --update-env` (ou remover os blocos de 8.3 do nginx e `reload`). O webhook e o Jarvis no WhatsApp não são afetados.
- **Código:** `rsync` da versão anterior (cópia em `/opt/jarvis/backup/app-<data>.tgz` antes de cada deploy, como em PF).
- **Banco:** migrations 101-107 só para frente e **aditivas**; na primeira implantação as tabelas do painel estão vazias, então a reversão é migration compensatória (`DROP` das tabelas novas e das colunas/índices que o painel acrescentou a `chat.tickets` e `access.users`) ou restauração do `pg_dump` tirado antes. A migration `102` recria `ops.v_tickets` com `search_key`; reverter exige recriar a visão sem a coluna. PF e o webhook continuam funcionando sobre o banco com as tabelas a mais. Reverter PF `005` exige reverter antes o que do painel usa `chat.tickets` e `ops.v_tickets`.
- **FAQ:** cada alteração tem versão (`faq_item_versions`); desfazer um item = reeditar com o snapshot anterior; lote de importação errado = desativar os itens com `origin_ref=<lote>`.
- **Irreversíveis, por desenho:** exclusão LGPD e rotina de retenção. Mitigações: modal com digitação do nome; retenção nasce em `RETENTION_DRY_RUN=true` por uma semana e só passa a apagar com confirmação do Caio.
- **Assistente:** remover `GEMINI_API_KEY` o desliga; o papel `jarvis_assistant` pode ser revogado sem efeito no resto.
- **Sessões:** `UPDATE ops.panel_sessions SET revoked_at = now()` derruba todas as sessões; trocar `PANEL_CSRF_SECRET` invalida todos os tokens.
- **Parada de emergência do Jarvis:** a pausa do painel (PN-45); se o painel estiver fora, `flag set` do CLI de PF.

### 9.1 Propostas de ADR (para `docs/architecture/overview.md`, que este spec não edita)

O CLAUDE.md exige ADR para mudanças em `docs/architecture/`. Registradas aqui como **propostas**, a serem copiadas ao topo da seção 8 do overview quando a Fase 2 for aprovada:

1. **Painel no mesmo processo do webhook, em pacote isolado `app/panel`.** Contexto: 1 worker, servidor compartilhado. Decisão: 4.1. Alternativa: dois processos. Consequência: separar depois é só configuração; pausa e política de resposta ficam em PF.
2. **Sessão própria do painel sobre o cookie do IAM, com CSRF por token + Origin.** Contexto: PN-3 e PN-4. Alternativas: só `whoami` com cache; cache em memória. Consequência: tabela `ops.panel_sessions`.
3. **`chat.tickets` (criada por PF) como entidade única de ticket, lida por `ops.v_tickets`; o painel só acrescenta busca e índices.** Contexto: R1 aceito. Alternativa descartada: duas tabelas com `UNION`.
4. **Schema `content` para a base de conhecimento, com revisão (`faq_revision`) como contrato de propagação.** Contexto: FAQ ≤ 5 min. Alternativas: sync por planilha, TTL fixo, LISTEN/NOTIFY. Consequência: o futuro módulo de dados estruturados (incremento 2) entra no mesmo schema.
5. **Migrations do painel no bloco 100+ (PF usa 001-099).** Contexto: duas frentes criando migrations em paralelo; o runner recusa prefixo duplicado.
6. **Assistente com papel de banco somente leitura e schema de views sem PII; provedor LLM atrás de interface.** Contexto: PN-52 e Q14. Alternativa: flag de código (AUTO-1). Consequência: toda ampliação de leitura é uma view nova, revisada.
7. **Busca sem acento por coluna gerada com `translate` (sem extensões).** Contexto: servidor compartilhado. Alternativa: `unaccent` + `pg_trgm`. Consequência: índice trigram opcional se a extensão existir.

## 10. Referências

- `docs/prd.md` (§3, §6, §7, §10 incl. itens de 2026-10-22), `docs/architecture/overview.md` (ADRs de 2026-10-19, 2026-10-20 e os dois de 2026-10-23), `docs/STATE.md`
- `docs/epics/painel.md`, `docs/stories/painel.1.md`
- `docs/tasks/painel.1/spec/{requirements.json,research.json,complexity.json}`
- `docs/tasks/porta-fechada.1/spec/{spec.md,requirements.json,research.json,complexity.json}` (dono do modelo de dados; 4.2, 4.2.8 e 4.11 em especial)
- `docs/designs/painel-prototipo.html`, `docs/designs/bots/`, `docs/designs/dados/` (tela Dados, tarefa futura)
- `docs/tasks/piloto-tecnico.1/spec/spec.md`, `piloto/guia.json` (extração das 112 perguntas)
- `docs/legal/aviso-privacidade.md`
- AUTO-1 (`C:\Users\User\Documents\GitHub\agente-instagram-direct-n8n`): `backend/app/{main,config}.py`, `core/security.py`, `services/{iam,faq,faq_planilha,assistant,monitor,rate_limit}.py`, `api/{dashboard,admin,assistant}.py`, `migrations/007_faq_uploads.sql`, `frontend/templates/*`, `frontend/static/js/alpine-components.js`, `frontend/tailwind.config.js`, `backend/tests/{test_admin_gating,test_a11y_templates}.py`

## Questões em aberto

Resolvidas em 2026-10-22 (ver bloco Decisões): Q1, Q2, Q5, Q6, Q8, Q9, Q11. Resolvidas em 2026-10-23: resposta a áudio e foto (PF Q20); **Q-P9 (R1 a R4 aceitos por PF)**; **Q-P4** (a pausa não bloqueia consentimento: decidido em PF 4.11). Detalhe das restantes da Fase 1 em `requirements.json` (`open_questions`).

**Da Fase 1 (ainda abertas; a Fase 2 assumiu um padrão, nenhuma bloqueia a Fase 3):**
1. **Q3** Métricas de Relatórios/Métricas; período padrão; conteúdo do PDF; denominador de "usuários ativos". *Padrão:* período 30 dias, denominador = usuários `active`, PDF com KPIs, tickets por marca/classificação, top 10 perguntas e tempo de resposta. *Se diferente:* só consultas e o gerador do PDF mudam.
2. **Q4** Formato de importação da FAQ e carga das 112. *Padrão:* colunas do protótipo; carga a partir de `piloto/guia.json`. *Se diferente:* muda `faq_import`/script de semente.
3. **Q7** Exclusão LGPD. *Padrão:* anonimizar (4.8). *Se "apagar tudo":* `lgpd.delete_person` troca `UPDATE` por `DELETE` na ordem das FKs; PN-57 já cobre as contagens.
4. **Q10** Onde ver a auditoria. *Padrão:* aba em Logs (4.5). *Se tela própria:* um item de menu e um template.
5. **Q12** 2FA e duração de sessão. *Padrão:* 2FA do IAM; sessão ociosa 2 h, absoluta 12 h. *Se diferente:* duas variáveis.
6. **Q13** Avaliação útil e confiança. *Padrão:* colunas nulas e KPI "sem avaliações".
7. **Q14** Limite e escopo de leitura do assistente. *Padrão:* 200 perguntas/dia; views sem corpo de mensagem e sem telefone. *Se precisar de corpo:* nova view `assistant.*` e revisão de LGPD.
8. **Q15** Listas de perfil, canais, marcas; gestor; separador. *Padrão:* as de PF 4.9 (gestor texto livre, `|`). *Se gestor for cadastrado:* FK e seletor no formulário.
9. **Q16** Prazo da validação jurídica. *Padrão:* exibição desligada até a resposta.
10. **Q17** E-mail de alertas gerados por ações do próprio Caio. *Padrão:* envia (PN-65 aprovado). *Se não:* o painel passa `email=False` (parâmetro já existe em PF, R3).

**Novas da Fase 2:**
11. **Q-P1 (IAM)** O IAM tem papéis por aplicação ou só `is_superadmin`/`area`? Qual a lista de hosts de destino (`next`) e o domínio do cookie `iam_session` alcança `jarvis.positiveco.com.br`? *Padrão:* lista de e-mails em configuração como autoridade; host a registrar no IAM. *Se existir papel "jarvis":* `current_admin` passa a exigir o papel além do e-mail.
12. **Q-P2** Duração da sessão do painel (2 h ociosa, 12 h absoluta) e revalidação a cada 5 min.
13. **Q-P3** Auditoria e retenção como abas de Logs (sem item novo no menu): aprovado?
14. **Q-P5** `welcome_failed` e `identity_conflict` (alertas de PF) aparecem em Alertas como sistema/segurança? *Padrão:* sim, com rótulos "Falha ao enviar boas-vindas" e "Conflito de identidade".
15. **Q-P6** Gemini: chave de API (como o AUTO-1) ou Vertex AI (PRD)? Modelo exato e teto de custo. A interface isola; só `gemini.py` muda.
16. **Q-P7** PDF mensal: ReportLab (dependência nova, padrão) ou página de impressão sem dependência? E o que o relatório contém (Q3).
17. **Q-P8** Disponibilidade de `pg_trgm`/`unaccent` no Postgres do droplet e valor de `max_connections` (pool total de ~13 conexões). *Padrão:* sem extensões (4.3.3); migration 107 opcional.
18. **Q-P10** Enquanto não existir o módulo de resposta, Tickets/Métricas/Relatórios mostram só tickets de mídia e estados vazios. Aceitável para o aceite de painel.1 (testado com fixtures)?
