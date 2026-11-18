# Architecture Overview — Jarvis

> **Status:** Draft — stack e módulos a fechar na fundação técnica (etapa 4)
> **Owner:** Caio
> **Last updated:** 2026-10-23 (ADRs de outbox transacional e de migrations)

Global technical decisions that bind every implementation. PRD says WHAT/WHY;
this says HOW (at the highest level). Module-specific architecture lives in
sibling files (`docs/architecture/{module}.md`).

## 1. Stack

- **Language(s):** {e.g. TypeScript (frontend) + Python (backend)}
- **Framework(s):** {e.g. React + Vite, FastAPI}
- **Database:** {e.g. PostgreSQL 16, schema separation by domain}
- **Hosting:** {e.g. DigitalOcean droplet, nginx, PM2}
- **Auth:** {e.g. JWT cookie HttpOnly}
- **Observability:** {logs, metrics, traces}

## 2. Layout

```
{project-root}/
├── backend/          ← API + workers
├── frontend/         ← UI
├── docs/             ← SDD artifacts (this folder)
├── .claude/          ← Project constitution + settings
└── ...
```

## 3. Module boundaries

Each module owns a domain. Communication crosses boundaries via explicit
contracts (API schemas, message envelopes), never via shared mutable state.

| Module | Owns | Depends on |
|---|---|---|
| `auth` | users, sessions, JWT | (none) |
| `views` | query layer, read models | `auth` |
| ... | ... | ... |

## 4. Data model principles

- **Schema-per-domain.** No giant `public` schema.
- **`raw.*`** for source-of-truth ingested data; **`mart.*`** for derived /
  pre-aggregated views.
- **TIMESTAMPTZ everywhere** (no naive timestamps).
- **No nullable booleans.** Three-state needs a domain type.

## 5. Conventions

- Imports: absolute from project root.
- File naming: `snake_case.py`, `kebab-case.tsx` (match neighborhood).
- Tests: `pytest` (backend), `vitest` (frontend). Boundaries you own, not lib
  internals.
- Migrations: alembic for SQL; one migration per logical change.

## 6. Cross-cutting

- **Logging:** structured JSON, never PII, request_id correlation.
- **Errors:** typed exceptions at the boundary; never leak internals to the
  client.
- **Rate limiting:** per-user for authenticated routes; per-IP for public.
- **Caching:** TanStack Query on the client; explicit invalidation, no
  background refetch on write-heavy queries.

## 7. Trade-offs (explicit)

- **Server-rendered vs SPA:** chose SPA because {reason}. Cost: SEO requires
  separate concern.
- **PostgreSQL only vs polyglot:** chose Postgres-only. Cost: some workloads
  (graph, search) less ergonomic.
- ...

## 8. ADRs

Architectural Decision Records. New entries at the top.

### 2026-11-18 — App sem DDL: papéis `jarvis_owner` e `jarvis_app`, permissões reaplicadas pelo runner (aceito pelo Caio em 2026-11-18, Fase 2 de porta-fechada.2)
- **Context:** A spec de porta-fechada.1 previu dono e aplicação separados no banco, mas o app roda com o papel dono. As migrations do cérebro (202) e do retorno humano (401) deixaram GRANTs para fazer à mão no servidor.
- **Decision:** Em produção, `jarvis_owner` é dono do banco e roda as migrations (`MIGRATION_DATABASE_URL`); o app usa `jarvis_app` (`DATABASE_URL`), só com DML, sem UPDATE/DELETE em `ops.audit_log`. A função `ops.apply_app_grants()` (migration 402) dá as permissões e o runner a reaplica ao fim de cada `migrate`. O app recusa subir em produção com papel dono ou superusuário. Os testes rodam o app como `jarvis_app`.
- **Alternatives:** `ALTER DEFAULT PRIVILEGES` (invisível, depende de quem cria o objeto); GRANT manual a cada migration (esquecível); manter um papel só (o app poderia apagar tabelas e reescrever a auditoria).
- **Consequences:** Uma variável a mais no `.env` do servidor e um passo único no PC (`dev_db_setup.sql`). Migration que precisar de permissão diferente para o app altera a função.

### 2026-11-17 — Resposta humana fora da janela de 24 h no WAHA (exceção à WA-16) (aceito pelo Caio em 2026-11-17, Fase 2 de retorno-humano.1)
- **Context:** A equipe passa a responder os escalados pelo painel (retorno-humano.1), e a resposta sai pelo número do Jarvis. O Caio decidiu "sem prazo" para essa resposta (D2). Hoje o outbox descarta, no WAHA, qualquer saída a quem não escreveu nas últimas 24 h (WA-16, "só responde").
- **Decision:** O kind `human_reply` é o **único** que o outbox envia sem mensagem do vendedor nas últimas 24 h. Todas as outras saídas continuam "só responde". A exceção pode ser desligada por configuração (`HUMAN_REPLY_IGNORE_WINDOW=false`). No mesmo design, os avisos ao grupo da equipe passam a sair pelo WAHA do próprio Jarvis, e não mais pelo do AUTO-1 (D5).
- **Alternatives:** Manter 24 h e responder por fora depois disso; pedir confirmação de risco depois de 24 h.
- **Consequences:** O número do Jarvis pode iniciar conversa nesses casos, o que aumenta o risco de restrição no WAHA (aceito). Mitigação: só admin envia, e sempre a quem já falou com o Jarvis. Substitui em parte a WA-16 de canal-waha.1.
- **Extensão (proposta em 2026-11-18, Fase 2 de cadastro.1, D4 do Caio):** o aviso `brand_access_granted` ("Seu acesso a {marca} foi liberado"), enviado quando o admin dá ao vendedor uma marca que ele pediu, também sai fora da janela, pelo mesmo interruptor. Diferente da resposta humana, a pausa do Jarvis segura esse aviso.

### 2026-11-13 — WAHA como canal dos vendedores; Meta como segunda opção (aceito pelo Caio em 2026-11-13, Fase 2 de canal-waha.1)
- **Context:** O go-live pela WhatsApp Cloud API está parado em pendências da Meta (número próprio, verificação, pagamento, token permanente, modelo aprovado; B-001). O ADR de 2026-10-20 rejeitou o WAHA para vendedores por ser não oficial, ter risco de banimento e não ter listas interativas. A Positive já opera WAHA no droplet para o AUTO-1.
- **Decision:** Os vendedores falam com o Jarvis por um **WAHA próprio do Jarvis** (instalação separada da do AUTO-1, em `127.0.0.1`), no número +55 85 98208-0355 (decisões D1–D6 de `docs/tasks/canal-waha.1/spec/spec.md`). A Meta continua no código atrás da mesma interface `Channel`, **guardada e desligada**: não há escolha de canal na subida (revisto com o Caio em 2026-11-16), e só a suíte de testes a liga. No WAHA, o Jarvis **só responde** (o vendedor inicia pelo link `wa.me`); opt-in por texto ("ACEITO"/"NÃO"); sem avaliação 👍/👎. Iniciar conversa (comerciantes) fica para uma história futura pela Meta.
- **Alternatives:** Esperar a Meta (adia o go-live sem prazo); WAHA no mesmo serviço do AUTO-1 com sessão nova (exige WAHA Plus e põe os dois no mesmo ponto de falha); enquetes do WhatsApp no lugar de botões (suporte variável entre engines).
- **Consequences:** Risco aceito de restrição ou banimento do número, mitigado por "só responde", "visto", envio em sequência e alertas de sessão e restrição; reversão por configuração para a Meta. O KPI "Avaliados como úteis" fica sem dados no WAHA. Substitui em parte o ADR de 2026-10-20 (o WAHA deixa de ser só para avisos internos); o ADR de 2026-10-19 (interface de canal) continua valendo e é o que torna a troca barata.

### 2026-10-29 — Gemini pela API direta (chave de API), não pelo Vertex AI
- **Context:** O PRD (§10, 2026-10-19) escolheu "Gemini via Vertex AI". O cérebro (cerebro.1) foi implementado contra a Gemini API direta (`generativelanguage.googleapis.com`, chave do nível pago, header `x-goog-api-key`) e deixou a troca em aberto como D12, junto com o aceite de LGPD para o envio de dados fora do Brasil.
- **Decision:** **Aprovado pelo Caio em 2026-10-29:** o Jarvis usa o Gemini pela API direta, com chave do nível pago. Ao modelo vão só a pergunta, a janela de contexto e os itens da fonte, sem telefone, nome ou outra identificação do vendedor (CB-17, `brain/redaction.py`).
- **Alternatives:** Vertex AI com região escolhida, como no PRD. Daria controle de região e contrato GCP, mas exige projeto GCP, conta de serviço e outro cliente. Fica possível depois: o acesso ao modelo está isolado em `app/services/brain/llm.py`.
- **Consequences:** Uma chave (`GEMINI_API_KEY`) no `.env` do servidor, e nada de credencial GCP. Os dados enviados ao modelo saem do Brasil, o que foi aceito. Custo controlado pelo teto mensal do cérebro (US$ 100, D11). Substitui o "via Vertex AI" da decisão de 2026-10-19; a linha Flash continua.

### 2026-10-23 — Efeitos externos só depois do commit (outbox transacional) e decisão de acesso numa única transação
- **Context:** O webhook da Meta reentrega por até 7 dias e precisa ao mesmo tempo (a) não duplicar efeitos em reentrega e (b) não perder a mensagem se o processo cair no meio (porta-fechada.1, AC-3 e AC-7). O AUTO-1 enfileira tudo em `BackgroundTasks` sem registro durável.
- **Decision:** Em cada mensagem recebida, idempotência (`UNIQUE(wa_message_id)` como primeiro INSERT), decisão de acesso, rastro, tentativa, alerta, ticket e as linhas de envio (`chat.outbound_messages`, `ops.deliveries`) são gravados numa única transação. Toda chamada externa (WhatsApp, Resend, WAHA, modelo) acontece depois do commit, a partir dessas linhas, com claim atômico (`FOR UPDATE SKIP LOCKED`). Resposta ao usuário é at-most-once; e-mail e grupo aceitam retentativa com backoff. Vale para todos os incrementos seguintes.
- **Alternatives:** Registrar o id e processar fora da transação (perde mensagem em queda); chamadas externas dentro da transação (prende linha e conexão durante I/O lento); fila externa (Redis/RabbitMQ) — infraestrutura nova sem necessidade a 20 mil msg/dia.
- **Consequences:** Duas tabelas de saída e um laço de varredura no processo. Corretude de idempotência e limite fica no banco, o que exige Postgres real nos testes de integração.

### 2026-10-23 — Migrations como SQL numerado com runner mínimo (sem Alembic)
- **Context:** A seção 5 deste documento cita Alembic como convenção genérica; o AUTO-1 usa `migrations/NNN_*.sql` aplicados à mão no deploy, e o servidor é compartilhado em produção (cada mudança de banco precisa de confirmação humana).
- **Decision:** `backend/migrations/NNN_nome.sql`, imutáveis depois de aplicadas, mais um runner de ~80 linhas (`python -m app.db.migrate`) que aplica em ordem, uma transação por arquivo, com lock consultivo, e grava versão e checksum em `ops.schema_migrations`; recusa arquivo alterado. Não roda sozinho na subida do serviço. Schemas por domínio: `access`, `chat`, `ops`.
- **Alternatives:** Alembic (dependência nova e SQLAlchemy sem ORM, autogenerate inútil); `psql -f` sem registro (como no AUTO-1: não sabe o que já foi aplicado).
- **Consequences:** Sem migration "down": reversão é migration nova compensatória ou restauração de backup (ver spec 9). A linha "alembic" da seção 5 fica superada para este projeto.

### 2026-10-20 — Stack e hospedagem iguais ao AUTO-1 (substitui TypeScript + GCP do PRD)
- **Context:** A Positive já opera o AUTO-1 (agente de DMs do Instagram) em Python/FastAPI + Postgres no droplet DigitalOcean `n8n-server-positive`, com login IAM corporativo, sync de FAQ por planilha via n8n, Gemini e escalonamento via WAHA. O Guia de produtos vem da FAQ desse projeto.
- **Decision:** Jarvis segue o mesmo padrão: Python 3.12 + FastAPI, PostgreSQL no droplet (banco próprio `jarvis`), PM2 + nginx em subdomínio `positiveco.com.br`, login do painel via IAM (`accounts.positiveco.com.br`), Gemini Flash. O canal com os vendedores é a **WhatsApp Cloud API oficial**; WAHA só para avisos internos (escalonamento ao Caio), como no AUTO-1.
- **Alternatives:** TypeScript + Google Cloud São Paulo (PRD original) — dados no Brasil, mas começa do zero e cria infra e custo novos. WAHA como canal dos vendedores — rejeitado: não oficial, risco de banimento com 100 usuários, sem listas interativas.
- **Consequences:** Reaproveita código e operação conhecidos. Dados ficam fora do Brasil (droplet DigitalOcean) — validar com o responsável por LGPD. Servidor compartilhado com ~10 apps (2 vCPU, 8 GB RAM, ~2,4 GB livres em 2026-10-20) — suficiente para 100 usuários; disponibilidade de 99,5% passa a depender desse servidor único. pgvector precisa ser verificado/instalado no Postgres do droplet.

### 2026-10-19 — WhatsApp Cloud API direto pela Meta
- **Context:** O PRD deixou aberto o acesso à API do WhatsApp (Meta direta ou provedor).
- **Decision:** Integração direta com a WhatsApp Cloud API da Meta, isolada atrás de uma interface de canal (`sendText`, `sendList`, `sendMedia`, `verifyWebhook`).
- **Alternatives:** Twilio — taxa por mensagem; mídia via URL buscada e armazenada pelo provedor (conflita com "sem link público"); operador de dados adicional fora do Brasil.
- **Consequences:** Upload de mídia com cache de `media_id`, como o PRD descreve; troca de provedor futura fica restrita a um adaptador.

## 9. Open questions

- Q1: {question}. Owner: @{name}. Deadline: YYYY-MM-DD.
