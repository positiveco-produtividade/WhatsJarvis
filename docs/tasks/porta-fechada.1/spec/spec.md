---
task_id: PORTA-FECHADA-1
status: approved
phase: 3-tasks
design_approved_by: Caio
design_approved_at: 2026-10-23
approved_by: Caio
approved_at: 2026-10-23
---

# Spec — Receber mensagens da WhatsApp Cloud API e aplicar a lista positiva com opt-in

> **Task ID:** PORTA-FECHADA-1
> **Story:** [porta-fechada.1](../../../stories/porta-fechada.1.md)
> **Epic:** [porta-fechada](../../../epics/porta-fechada.md)
> **Status:** Fase 1 (Requisitos) **aprovada pelo Caio em 2026-10-23**. Fase 2 (Design: seções 4, 5 e 8–10, `research.json`, `complexity.json`) **redigida em 2026-10-23, aguardando aprovação** antes da Fase 3 (tarefas). Emendas pós-Fase 1 em 2026-10-23: AC-31 com a ordem "tamanho, assinatura"; Caio confirmou o AC-36 como aprovado (mídia recebe a resposta curta); design ajustado aos pedidos R1–R4 do painel.1 (4.11).
> **Size:** XL (21 pontos, ~104 h; ver `complexity.json`)

Fonte de verdade dos critérios: `requirements.json` (mesma pasta). Este texto
resume; em caso de divergência, atualizar os dois.

## Decisões (2026-10-23)

10. **Mídia de usuário autorizado RECEBE a resposta curta de "equipe avisada"**, exatamente como o AC-36 aprovado. O Caio confirmou em 2026-10-23, revertendo a nota do PRD §10 de 2026-10-22 ("Áudio e foto não recebem resposta"), que precisa ser corrigida no Decision Log do PRD. A chave `MEDIA_ACK_ENABLED` existe, **ligada por padrão**. Resolve a antiga Q20.
11. **AC-31 reordenado** para "tamanho, assinatura, …", coerente com o AC-4 (413 antes de validar a assinatura).

## Decisões (2026-10-22)

Tomadas pelo Caio ao aprovar o protótipo do painel e em seguida:

1. **Desconhecido: sem resposta, mas guarda a 1ª mensagem.** O texto da PRIMEIRA mensagem de cada número desconhecido é armazenado (as seguintes guardam só contagem e instantes), para o painel exibir em "Pedindo acesso". **Dependente de validação jurídica (LGPD)**; retenção é questão para o jurídico (Q17). Mídia na 1ª mensagem guarda só tipo e tamanho.
2. **Revogado pode ser reativado** pelo administrador: volta a `pending_optin` e precisa tocar em Aceito de novo. Toque em botão antigo não reativa sozinho (AC-19, AC-35).
3. **Cadastro exige todos os campos do PRD:** nome, telefone, perfil, empresa, UFs, canais, marcas e gestor responsável (AC-15, AC-16).
4. **Grupo WAHA desligado por chave.** O caminho de envio ao grupo da equipe existe e é testado, mas fica desligado por chave de configuração, desligada por padrão. Desligado, nada vai a nenhum grupo (AC-13).
5. **Escalonamento imediato,** sem limiar de atraso.
6. **Mídia vira ticket escalado, não alerta.** O escalonamento de mídia de usuário autorizado (AC-36) cria um ticket escalado em tabela própria. Continuam sendo alertas: limite por hora (AC-29), conflito de BSUID (AC-8) e falhas de envio (AC-17, AC-33).
7. **E-mail imediato para cada novo alerta,** ao e-mail corporativo do Caio configurado no servidor (o endereço nunca é escrito aqui), no padrão Resend do AUTO-1. Falha de e-mail nunca bloqueia o processamento: é logada e retentada. Independe da chave do grupo (AC-37).
8. **Tentativas de acesso NÃO geram alerta.** Número desconhecido (e revogado/recusado) não gera alerta, e-mail nem aviso ao grupo, nem no 1º contato nem na 3ª tentativa. São apenas registradas para "Pessoas autorizadas → Pedindo acesso" do painel (AC-10, AC-11, AC-12, AC-14, AC-27). O default Q2' deixa de existir.
9. **Ticket escalado NÃO envia e-mail.** Nem o de mídia (AC-36) nem qualquer escalonamento futuro. O e-mail é só para novos alertas; o ticket fica no banco/painel (e no grupo, se a chave estiver ligada).

## Decisões (2026-10-21)

- **Silêncio (Q1/Q2/Q15).** Número desconhecido não recebe NENHUMA resposta. Revogado ou recusado recebe o mesmo tratamento. A única mensagem a quem recusa é o texto de recusa do aviso, ao tocar em Não aceito.
- **Q3 — Canal de alertas:** grupo de WhatsApp da equipe via WAHA, padrão AUTO-1 (dashboard e respostas pelo grupo vêm depois). Reformulado em 2026-10-22: grupo desligado por padrão (decisões 4 a 9 acima).
- **Q4 — Cadastro:** CLI/CSV via SSH; único usuário de teste inicial é o telefone do Caio. Layout do CSV e valores de perfil seguem em aberto, baixa prioridade.
- **Q6 — Sem expiração de acesso por ora.** Sem validade de 180 dias, expiração automática ou revalidação em lote (AC-24 e AC-25 removidos; IDs não reutilizados). `valid_until` fica nulo no modelo, ignorado pela decisão de acesso. Revogação em até 1 minuto permanece.
- **Q7 — Limite de 60 msg/h mantido**, janela deslizante de 60 min; um aviso por janela ao usuário ("Você atingiu o limite de mensagens por hora. Tente novamente em alguns minutos.") e alerta.
- **Q11 — Corpo da mensagem** de usuário autorizado é guardado (como no AUTO-1), 12 meses. De bloqueado, nunca. De desconhecido, só a 1ª mensagem (decisão 1 de 2026-10-22).
- **Q14 — Sem áudio nem imagem.** RF-02 sai do MVP. Mídia de usuário autorizado gera ticket escalado e o usuário recebe resposta curta (AC-36).
- **Q16 — Default adotado:** o registro de mídia leva só tipo, usuário e message_id; o arquivo nunca é baixado.

## 1. Goal

Ser a "porta fechada" do Jarvis: receber os webhooks da WhatsApp Cloud API e
só deixar passar quem está cadastrado e com aceite do aviso de privacidade
registrado, sempre **antes** de qualquer custo (mídia, busca, modelo). Quem
está fora da lista não recebe resposta alguma: a tentativa é apenas registrada
(com o texto da primeira mensagem, sujeito à validação jurídica) para a tela
"Pedindo acesso" do painel, sem alerta. Revogação vale em até 1 minuto,
inclusive com conversa em andamento, e o revogado pode ser reativado pelo
administrador (com novo aceite). Cada usuário tem limite de 60 mensagens por
hora e toda mensagem deixa rastro. Não há expiração automática de acesso.
O usuário autorizado recebe só uma mensagem fixa de "em construção" para
texto; mídia (áudio, imagem etc.) cria um ticket escalado, sem e-mail, e o
usuário recebe uma resposta curta de que a equipe foi avisada. Eventos
anômalos viram alertas, e cada alerta novo gera um e-mail imediato ao Caio; o
envio ao grupo da equipe via WAHA existe, mas começa desligado. É a fundação
de segurança e LGPD sobre a qual os incrementos de conteúdo serão construídos.

Contexto: PRD §3, §4, §7, RF-17 e RF-20 (parcial). Stack e hospedagem: ADR de
2026-10-20 (Python 3.12 + FastAPI, Postgres no droplet, PM2 + nginx, WhatsApp
Cloud API oficial; WAHA só para avisos internos), padrão AUTO-1 (inclusive o
envio de e-mail via Resend).

Divergências do PRD já decididas pelo Caio e ainda sem registro no Decision
Log do PRD (fora desta fase): (a) regra 5 da lista positiva ("uma única
resposta fixa" e "a partir de 3, alerta ao administrador") virou silêncio, com
guarda da 1ª mensagem e sem alerta; (b) regra 3 (validade de 180 dias e
bloqueio do vencido) suspensa; (c) RF-02 saiu do MVP; (d) regra 1 ("cadastro
só pelo painel") atendida por CLI/CSV até o incremento 5; (e) o grupo WAHA
começa desligado e os canais iniciais são painel e e-mail (só para alertas);
(f) a entrada do PRD §10 de 2026-10-22 ("Áudio e foto não recebem resposta")
foi revertida pelo Caio em 2026-10-23: mídia recebe a resposta curta de
"equipe avisada" (AC-36). O PRD §10 precisa ser corrigido nesse ponto.

## 2. User Story

**Como** responsável pelo Jarvis,
**quero** que ele receba as mensagens do WhatsApp e só deixe passar quem está
cadastrado, ativo e com aceite registrado,
**para** que preço e material comercial nunca cheguem a quem não deveria e
números desconhecidos não gerem custo.

## 3. Acceptance Criteria

Formato EARS (GIVEN/WHEN/THEN). Detalhe completo e notas de teste em
`requirements.json`. Vocabulário: **tentativa de acesso** (desconhecido ou
revogado/recusado) é só registrada; **alerta** é registro imediato na tabela
de alertas e é o único que gera e-mail; **ticket escalado** é registro em
tabela própria, sem e-mail. AC-24 e AC-25 foram removidos (Q6).

**Webhook**
- **AC-1** — GIVEN webhook no ar WHEN GET com verify token correto/errado THEN 200 com o challenge / 403. *(integration)*
- **AC-2** — GIVEN App Secret configurado WHEN POST sem assinatura ou com HMAC SHA-256 inválido THEN descartado sem parsing, sem rastro de mensagem e sem resposta. *(integration)*
- **AC-3** — GIVEN assinatura válida WHEN o payload chega THEN 200 sem esperar envios nem e-mails; falha interna transitória devolve 5xx para a Meta reentregar. *(integration)*
- **AC-4** — GIVEN limite de 64 KB WHEN o corpo excede THEN 413 sem processar. *(integration)*
- **AC-5** — GIVEN payload só com statuses/leitura/entrega WHEN recebido THEN 200, sem decisão de acesso, sem resposta, sem rastro de mensagem e sem contar no limite; status `failed` de envio do Jarvis atualiza o rastro de origem. *(unit)*
- **AC-6** — GIVEN phone_number_id configurado WHEN o payload é de outro número ou traz várias mensagens THEN o primeiro é descartado e cada mensagem do segundo é processada de forma independente. *(unit)*

**Idempotência e identidade**
- **AC-7** — GIVEN message_id já processado WHEN a Meta reentrega THEN sem nova resposta, alerta, ticket ou e-mail, sem incremento de limite ou de tentativas. *(integration)*
- **AC-8** — GIVEN usuário com telefone e/ou BSUID WHEN chega mensagem com qualquer um deles THEN é resolvido ao usuário e o identificador faltante é associado; BSUID já de outro usuário não é reassociado, o remetente fica em silêncio e um alerta de conflito é criado. *(unit)*
- **AC-9** — GIVEN número brasileiro cadastrado com 9º dígito WHEN o wa_id vem sem ele (ou o inverso) THEN é reconhecido como o mesmo usuário. *(unit)*

**Número fora da lista (silêncio, sem alerta)**
- **AC-10 [texto da 1ª msg depende de jurídico, Q17]** — GIVEN remetente desconhecido na primeira tentativa WHEN envia qualquer tipo de mensagem THEN nenhuma resposta, tentativa registrada (contador, instante) para "Pedindo acesso", zero custo, sem alerta/e-mail/grupo; se for texto, o texto da 1ª mensagem é guardado, se for mídia só tipo e tamanho. *(integration)*
- **AC-11** — GIVEN remetente desconhecido com tentativas anteriores WHEN reenvia THEN silêncio; contador e último instante atualizados; texto não guardado e o da 1ª nunca sobrescrito. *(integration)*
- **AC-12 [decisão 2026-10-22]** — GIVEN remetente desconhecido ou revogado/recusado WHEN faz qualquer número de tentativas THEN nenhum alerta, e-mail ou aviso ao grupo; só o registro para o painel; não há limiar de 3 tentativas. *(integration)*
- **AC-13** — GIVEN chave do grupo WAHA (desligada por padrão) WHEN um alerta ou ticket escalado é criado THEN desligada: só banco, zero chamadas ao WAHA; ligada: envio ao grupo, e falha do WAHA mantém o registro, marca a entrega como falha e retenta sem afetar o fluxo; e-mail de alerta independe da chave e ticket nunca envia e-mail; tentativas de acesso nunca vão ao grupo. *(integration)*
- **AC-14** — GIVEN payload só com BSUID, sem cadastro WHEN recebido THEN tratado como desconhecido, com o BSUID como chave: silêncio e registro, sem alerta. *(unit)*

**Cadastro e opt-in**
- **AC-15** — GIVEN comando de cadastro (CLI via SSH) WHEN faltar qualquer um de nome, telefone, perfil, empresa, UFs, canais, marcas ou gestor responsável THEN rejeita indicando o campo e não cria nada; com todos válidos cria `pending_optin` com `valid_until` nulo, envia a boas-vindas e audita; duplicado é rejeitado. *(integration)*
- **AC-16** — GIVEN CSV WHEN importado THEN mesma exigência de campos do AC-15; validação total antes de gravar; qualquer erro não grava nada e lista linha, campo e motivo. *(integration)*
- **AC-17** — GIVEN usuário que ainda não escreveu (fora da janela de 24h) WHEN a boas-vindas é enviada THEN só como modelo aprovado, nunca texto livre; falha mantém `pending_optin`, cria alerta (com e-mail) e permite reenvio. *(integration)*
- **AC-18** — GIVEN `pending_optin` WHEN toca em Aceito THEN `active`, `accepted_at` com data e hora, telefone e BSUID associados, confirmação enviada. *(integration)*
- **AC-19** — GIVEN `active`, `declined` ou `revoked` WHEN toca em Aceito de botão antigo THEN nada muda; reativar revogado ou recusado só pelo administrador (AC-35). *(unit)*
- **AC-20** — GIVEN `pending_optin` WHEN toca em Não aceito THEN `declined`, recebe só o texto de recusa do aviso, acesso negado, depois silêncio. *(integration)*
- **AC-21** — GIVEN `pending_optin` WHEN envia texto ou áudio no lugar do botão (inclusive "aceito") THEN nenhum aceite, nenhum conteúdo, mídia não baixada; lembrete interativo com os botões no máximo 1 vez/24h. *(integration)*

**Revogação e troca de número**
- **AC-22** — GIVEN `active` WHEN revogado THEN a mensagem seguinte (e qualquer uma após 60 s) é bloqueada; decisão lê o banco, cache ≤ 30 s. *(integration)*
- **AC-23** — GIVEN resposta em preparação WHEN a revogação ocorre antes do envio THEN acesso reavaliado antes de enviar e a resposta é descartada (`revoked_before_send`). *(integration)*
- **AC-26** — GIVEN usuário com telefone A WHEN troca para B THEN, na mesma transação, A é revogado e B nasce `pending_optin` com novo aceite. *(integration)*
- **AC-27** — GIVEN `revoked` ou `declined` WHEN envia mensagem THEN silêncio, tentativa registrada com o motivo, sem alerta (AC-12), texto não guardado. *(integration)*

**Mensagem ativa, limite e usuário autorizado**
- **AC-28** — GIVEN `active` WHEN envia "sair" THEN `proactive_opt_out` ligado, consulta continua; `can_send_proactive` é falso se não estiver `active` com aceite ou se tiver opt-out. *(unit)*
- **AC-29** — GIVEN 60 mensagens aceitas nos últimos 60 min (janela deslizante) WHEN chega a 61ª THEN bloqueada (`rate_limited`); um aviso por janela ("Você atingiu o limite de mensagens por hora. Tente novamente em alguns minutos."), seguintes em silêncio; alerta imediato (máx. 1/h por usuário) com e-mail; duplicatas e statuses não contam. *(unit)*
- **AC-30** — GIVEN usuário `active` e abaixo do limite WHEN envia texto THEN resposta fixa "em construção", sem modelo ou busca; corpo guardado no rastro. *(integration)*
- **AC-36 [default adotado; resposta confirmada pelo Caio em 2026-10-23]** — GIVEN usuário `active` WHEN envia áudio, imagem ou outra mídia THEN nada é baixado, transcrito ou interpretado; TICKET ESCALADO imediato em tabela própria (status `aberto`; usuário, tipo, message_id), sem registro em alertas e SEM e-mail; grupo só com a chave ligada; usuário recebe resposta curta de que a equipe foi avisada; rastro `escalated_media`. Atendimento do ticket e encaminhar a resposta do grupo estão fora. *(integration)*

**Avisos, ordem, rastro e administração**
- **AC-37** — GIVEN e-mail do destinatário e credenciais do provedor configurados no servidor (padrão Resend do AUTO-1) WHEN um NOVO alerta é criado THEN e-mail imediato sem texto de mensagem e sem bloquear o webhook; falha é logada sem PII, marcada e retentada; independe da chave do grupo; tentativas de acesso e tickets escalados não geram e-mail. *(integration)*
- **AC-31** — GIVEN qualquer mensagem assinada WHEN processada THEN ordem: tamanho, assinatura, idempotência, remetente, acesso (lista e aceite), limite (tamanho antes da assinatura, como no AC-4); nada que custe ocorre antes de `allowed`. *(integration)*
- **AC-32** — GIVEN mensagem recebida e não duplicada WHEN o processamento termina THEN exatamente 1 rastro (ids, tipo, decisão e motivo, resposta ou `none`, latência, resultado do envio); corpo guardado só para usuário autorizado (12 meses), salvo a 1ª mensagem de desconhecido (AC-10); logs sem PII nem o e-mail do destinatário. *(integration)*
- **AC-33** — GIVEN falha de envio WHEN a Meta recusa THEN erro no rastro, sem retentativa infinita, alerta (com e-mail) se repetir. *(integration)*
- **AC-34** — GIVEN usuário em qualquer status WHEN revogado por comando THEN `revoked` com autor e data, idempotente, auditado. *(integration)*
- **AC-35** — GIVEN `pending_optin`, `declined` ou `revoked` WHEN o administrador reenvia/reativa THEN boas-vindas reenviada, usuário em `pending_optin` sem acesso até tocar em Aceito de novo (revogado tem `accepted_at` zerado, histórico na auditoria); `active` recusa. *(integration)*

**Requisitos não funcionais (resumo):** 200 do webhook < 1 s; decisão de acesso
< 50 ms; alerta e ticket criados no mesmo processamento; e-mail de alerta sem
bloquear o webhook; HMAC em tempo constante sobre bytes brutos; segredos,
credencial do e-mail e endereço do destinatário só em ambiente; unicidade de
`message_id` por constraint; chave do grupo desligada por padrão; logs JSON sem
PII; corpo de autorizado e rastro retidos 12 meses; texto da 1ª mensagem de
desconhecido só após validação jurídica.

## 4. Arquitetura e Design

Base: padrão do AUTO-1 (`C:\Users\User\Documents\GitHub\agente-instagram-direct-n8n`, `backend/app`),
estudado em `research.json`. A tarefa **possui o modelo de dados compartilhado**
com o `painel.1` (ver 4.2.8 e 4.11). Python 3.12, FastAPI, asyncpg, httpx (todas já
fixadas nas versões já instaladas na máquina do Caio, compatíveis com 3.12 e 3.14:
fastapi 0.115.6, uvicorn 0.34.0, asyncpg 0.31.0, pydantic-settings 2.7.1,
httpx 0.28.1 — atualizado em 2026-10-23 a partir das do AUTO-1, para não
baixar nada). Dependências de desenvolvimento:
pytest, pytest-asyncio, ruff, mypy. **Nenhuma outra dependência nova**
(sem Alembic, sem `phonenumbers`, sem Redis, sem `respx`).

### 4.1 Visão geral e componentes

```
Meta ──POST──► nginx ──► uvicorn (1 worker; PM2 "jarvis"; 127.0.0.1:<porta>)
                              │
   api/webhook.py ── tamanho ► HMAC ► JSON ► phone_number_id ──► services/inbound.py (parser puro)
                              │
                    services/pipeline.py ── 1 TRANSAÇÃO por mensagem ──► Postgres "jarvis"
                              │ commit                                     schemas access | chat | ops
                              ▼
          BackgroundTasks(ids) + laço de varredura (workers.py)
              │                         │
       outbox.dispatch          deliveries.dispatch
        (ReplyPolicy)              ├─► Resend  (e-mail; SÓ alertas)
              ▼                    └─► WAHA    (grupo; só com a chave ligada)
   Channel ─► WhatsApp Cloud API
CLI (python -m app.cli, por SSH) ─► services/users.py (mesma camada de serviço) ─► Postgres
```

| Componente | Ação | Caminho (sob `backend/app/`) | Responsabilidade |
|---|---|---|---|
| App FastAPI | Create (adapta AUTO-1) | `main.py` | lifespan (pool, laços de fundo, fecha clientes, **instala o `ReplyPolicy` de `reply_policy.py`**), TrustedHost, headers, request_id, handlers de erro sem traceback, `/health` (banco + migrations em dia) |
| Config | Create (adapta) | `config.py` | `Settings` com `SecretStr`, validadores fail-fast (4.5) |
| Núcleo transversal | Create | `core/{clock,errors,logging_json,log_redaction,security,values}.py` | relógio injetável; exceções tipadas; log JSON; máscara de segredos/telefone/e-mail (reuso AUTO-1 + padrões novos); HMAC e comparação em tempo constante sobre bytes; enums e listas de valores (status, decisões, perfis, UFs, marcas, canais) |
| Banco | Create | `db/{connection,migrate}.py` | pool asyncpg (cópia AUTO-1); runner de migrations (ADR 2026-10-23) |
| Borda HTTP | Create | `api/webhook.py`, `services/rate_limit.py` | GET/POST; só HTTP; limite por IP (reuso AUTO-1, webhook 600/60 s) |
| Funções puras (sem I/O) | Create | `services/{phone,inbound,access,replies}.py` | normalização e validação de telefone; parser do payload; tabela de decisão de acesso; textos fixos |
| Núcleo transacional | Create | `services/{pipeline,identity,attempts,limits,trace,optin,tickets,alerts,audit,flags}.py` | tudo o que muda estado de uma mensagem, numa transação; `tickets` grava em `chat.tickets` (R1); `alerts.raise_alert` com `email` (R3); `flags` com `paused`/`brand_enabled:*` (R4) |
| Saída | Create | `services/{channel,whatsapp,outbox,reply_policy,deliveries,notifications,waha,answering}.py` | interface de canal e cliente Cloud API; despacho de respostas; política de resposta (pausa e marca, R4); entrega de alertas/tickets (e-mail e grupo); ponto de extensão do cérebro |
| Administração | Create | `services/users.py`, `cli.py` | `validate_row` pura e `register` reutilizável (R2); revogação, reenvio/reativação, troca de número, chaves; CLI fino sobre o serviço (o painel chama o mesmo serviço) |
| Fundo | Create | `services/{workers,counters}.py` | varredura de outbox/entregas, recuperação de travados, gravação em lote dos contadores de segurança |
| Migrations | Create | `../migrations/001..005_*.sql` (bloco 001–099 é de PF) | ver 4.2 |
| Testes | Create | `../tests/{unit,integration,security}/`, `fixtures/meta/` | ver 4.7 |
| Deploy | Create | `../../deploy/{ecosystem.config.js,nginx/jarvis.conf.template,RUNBOOK.md}`, `.github/workflows/ci.yml` | ver 4.10 |

Regra de camadas: `api` só fala HTTP; `services/*` puros não importam banco nem
rede; só `pipeline`, `users` e os despachantes tocam o banco; só `whatsapp.py`,
`notifications.py` e `waha.py` tocam a rede. Importações absolutas a partir de
`app` (CLAUDE.md).

### 4.2 Modelo de dados

#### 4.2.1 Convenções

- Banco `jarvis` (Postgres 16 do droplet). Três schemas por domínio (overview §4): `access` (quem pode falar), `chat` (o que foi dito, inclusive tickets), `ops` (alertas, entregas, auditoria, estado). O painel acrescenta o schema `content` (FAQ).
- `timestamptz` sempre; nenhum boolean anulável; `bigserial` como chave; enums como `text` + `CHECK` (acrescentar valor = migration nova, barata).
- Janelas de tempo usam o relógio da aplicação passado como parâmetro (`$now`), nunca `now()` solto, para o teste controlar o tempo (AC-22, AC-29).
- **Sem PII** em `ops.alerts`, `ops.audit_log`, `ops.security_counters` e logs: identifica-se pessoa por `user_id`.
- Parâmetro de SQL usado em mais de um lugar de um `INSERT ... WHERE NOT EXISTS` leva cast explícito (`$1::varchar`) — lição do AUTO-1 (AmbiguousParameterError, 2026-09-11).

#### 4.2.2 Schema `access`

```sql
CREATE TABLE access.users (
  id                      bigserial PRIMARY KEY,
  name                    text NOT NULL,
  phone_e164              text NOT NULL,                 -- forma canônica cadastrada (BR móvel COM o 9); destino de TODA resposta
  phone_key               text NOT NULL UNIQUE,          -- chave de comparação (BR móvel SEM o 9); ver 4.4.8
  bsuid                   text UNIQUE,                   -- associado no 1º aceite/mensagem autorizada (AC-8, AC-18)
  profile                 text NOT NULL CHECK (profile IN
        ('vendedor','gestor_comercial','executivo','parceiro_externo','administrador')),
  company                 text NOT NULL,
  ufs                     text[] NOT NULL CHECK (cardinality(ufs) > 0),
  channels                text[] NOT NULL CHECK (cardinality(channels) > 0),
  brands                  text[] NOT NULL CHECK (cardinality(brands) > 0),
  manager_name            text NOT NULL,                 -- gestor responsável (texto livre; Q15)
  valid_until             timestamptz,                   -- sempre NULL; ignorado pela decisão (Q6)
  status                  text NOT NULL CHECK (status IN ('pending_optin','active','declined','revoked')),
  status_changed_at       timestamptz NOT NULL,
  accepted_at             timestamptz,
  accepted_notice_version text,
  declined_at             timestamptz,
  revoked_at              timestamptz,
  revoked_by              text,
  revoked_reason          text CHECK (revoked_reason IN ('left_company','number_change',
        'partner_contract_end','monthly_reconciliation','declined_notice','other')),
  proactive_opt_out       boolean NOT NULL DEFAULT false,
  proactive_opt_out_at    timestamptz,
  optin_reminder_at       timestamptz,                   -- último lembrete interativo (AC-21: 1/24h)
  rate_limit_notice_at    timestamptz,                   -- último aviso de limite (AC-29)
  created_at              timestamptz NOT NULL,
  created_by              text NOT NULL,
  updated_at              timestamptz NOT NULL,
  CONSTRAINT active_needs_acceptance CHECK (status <> 'active'
        OR (accepted_at IS NOT NULL AND accepted_notice_version IS NOT NULL)),
  CONSTRAINT revoked_needs_author CHECK (status <> 'revoked'
        OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL)),
  CONSTRAINT opt_out_consistent CHECK (proactive_opt_out = (proactive_opt_out_at IS NOT NULL))
);
CREATE INDEX users_status_idx ON access.users (status);

-- Histórico de aceite: uma linha por aviso emitido (cadastro, reenvio, reativação, troca de número).
CREATE TABLE access.optin_requests (
  id              bigserial PRIMARY KEY,
  user_id         bigint NOT NULL REFERENCES access.users(id),
  status          text NOT NULL CHECK (status IN ('open','accepted','declined','superseded')),
  notice_version  text NOT NULL,                         -- versão do aviso apresentada
  issued_at       timestamptz NOT NULL,
  issued_by       text NOT NULL,
  responded_at    timestamptz,
  responded_wa_message_id text
);
CREATE UNIQUE INDEX optin_one_open_per_user ON access.optin_requests (user_id) WHERE status = 'open';

-- Tentativas de acesso (desconhecido, revogado, recusado): alimenta "Pedindo acesso". Uma linha por remetente.
CREATE TABLE access.access_attempts (
  id               bigserial PRIMARY KEY,
  phone_key        text UNIQUE,
  bsuid            text UNIQUE,
  user_id          bigint REFERENCES access.users(id),   -- preenchido se o remetente é (ou virou) cadastro
  reason           text NOT NULL CHECK (reason IN ('unknown','revoked','declined','identity_conflict')),
  attempt_count    integer NOT NULL CHECK (attempt_count >= 1),
  first_attempt_at timestamptz NOT NULL,
  last_attempt_at  timestamptz NOT NULL,
  first_msg_type   text NOT NULL,
  first_msg_chars  integer,                              -- só texto; mídia: NULL (ver 4.9, Q21)
  first_msg_mime   text,                                 -- só mídia, vindo do payload (nada é baixado)
  dismissed_at     timestamptz,                          -- "Ignorar" (painel PN-38); nova tentativa zera
  CONSTRAINT has_identifier CHECK (phone_key IS NOT NULL OR bsuid IS NOT NULL)
);

-- 1ª mensagem de desconhecido, separada de propósito (Q17): permissão própria, DROP/TRUNCATE simples,
-- expurgo por stored_at. Só é escrita se UNKNOWN_FIRST_TEXT_STORE=true e a mensagem for texto.
CREATE TABLE access.unknown_first_messages (
  attempt_id bigint PRIMARY KEY REFERENCES access.access_attempts(id) ON DELETE CASCADE,
  body       text NOT NULL,
  stored_at  timestamptz NOT NULL
);
```

#### 4.2.3 Schema `chat`

```sql
-- UM registro por mensagem recebida e não duplicada (AC-32). O UNIQUE é a idempotência (AC-7).
CREATE TABLE chat.message_trace (
  id                 bigserial PRIMARY KEY,
  wa_message_id     text NOT NULL UNIQUE,
  received_at        timestamptz NOT NULL,               -- relógio do servidor
  wa_timestamp       timestamptz,                        -- relógio da Meta
  msg_type           text NOT NULL,
  user_id            bigint REFERENCES access.users(id),
  attempt_id         bigint REFERENCES access.access_attempts(id),
  access_decision    text NOT NULL,                      -- definido uma vez (lista abaixo)
  decision           text NOT NULL,                      -- final: = access_decision, exceto send_failed / revoked_before_send
  decision_reason    text,
  counted_in_limit   boolean NOT NULL DEFAULT false,
  body               text,                               -- SÓ usuário autorizado (allowed). Nunca de bloqueado/desconhecido
  reply_kind         text NOT NULL DEFAULT 'none',       -- none | pending | <kind de chat.outbound_messages>
  reply_wa_message_id text,
  reply_sent_at      timestamptz,
  latency_ms         integer,                            -- reply_sent_at - received_at
  send_result        text,                               -- sent | failed | discarded | unknown | abandoned
  send_error         text,                               -- código da Meta, sem PII
  ticket_number      text,
  CONSTRAINT has_sender CHECK (user_id IS NOT NULL OR attempt_id IS NOT NULL OR access_decision = 'error')
);
CREATE INDEX message_trace_user_time_idx  ON chat.message_trace (user_id, received_at DESC);
CREATE INDEX message_trace_limit_idx      ON chat.message_trace (user_id, received_at) WHERE counted_in_limit;
CREATE INDEX message_trace_received_idx   ON chat.message_trace (received_at);

-- Fila durável de respostas ao usuário (ADR 2026-10-23). Inclui boas-vindas sem mensagem de origem.
CREATE TABLE chat.outbound_messages (
  id                bigserial PRIMARY KEY,
  user_id           bigint NOT NULL REFERENCES access.users(id),
  to_phone          text NOT NULL,                       -- users.phone_e164 no momento do enfileiramento
  kind              text NOT NULL CHECK (kind IN ('welcome_template','optin_reminder','optin_confirmation',
        'optin_declined','under_construction','rate_limit_notice','media_ack','opt_out_confirmation')),
  required_status   text NOT NULL,                       -- status que o usuário PRECISA ter na hora do envio (AC-23)
  body              text,                                -- texto livre/interativo
  template          jsonb,                               -- {name, lang, params, button_payloads}
  inbound_trace_id  bigint REFERENCES chat.message_trace(id),
  optin_request_id  bigint REFERENCES access.optin_requests(id),
  status            text NOT NULL CHECK (status IN ('pending','sending','sent','failed','discarded','unknown')),
  attempts          smallint NOT NULL DEFAULT 0,
  next_attempt_at   timestamptz NOT NULL,
  claimed_at        timestamptz,
  wa_message_id     text UNIQUE,
  error_code        text,
  error_subcode     text,
  discard_reason    text,
  created_at        timestamptz NOT NULL,
  sent_at           timestamptz
);
CREATE INDEX outbound_due_idx ON chat.outbound_messages (next_attempt_at) WHERE status = 'pending';

-- Entidade ÚNICA de ticket (R1, pedido do painel.1). Esta tarefa só escreve kind='media'; o futuro módulo
-- de resposta escreve kind='question'. Tabela PRÓPRIA, separada de ops.alerts (decisão 6).
CREATE TABLE chat.tickets (
  id bigserial PRIMARY KEY,
  ticket_number text NOT NULL UNIQUE,                 -- f"J-{n:04d}" da sequência ops.ticket_seq (lpad TRUNCA acima de 4 dígitos)
  user_id bigint NOT NULL REFERENCES access.users(id),
  trace_id bigint REFERENCES chat.message_trace(id),
  kind text NOT NULL CHECK (kind IN ('question','media')),
  brand text CHECK (brand IN ('jungle','plant_power','possible','a_tal_da_castanha','zaya')),  -- NULL em mídia
  classification text NOT NULL DEFAULT 'NAO_IDENTIFICADO'
      CHECK (classification IN ('PRODUTO','PRECO','ARGUMENTO','MATERIAL','CAMPANHA','MIDIA','NAO_IDENTIFICADO')),
  confidence numeric(4,3),
  message text, summary text, escalation_reason text,  -- mídia: "[áudio]", "[imagem]"… (nunca o conteúdo)
  escalated boolean NOT NULL DEFAULT false,
  status text NOT NULL CHECK (status IN ('answered_bot','answered_human','awaiting','send_error')),
  urgency text CHECK (urgency IN ('muito_urgente','urgente','pouco_urgente')),   -- só escalados
  answered_by text, answered_at timestamptz,
  responsible text, meta jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
  CONSTRAINT escalated_needs_urgency CHECK (NOT escalated OR urgency IS NOT NULL));
CREATE INDEX tickets_created_idx  ON chat.tickets (created_at DESC);
CREATE INDEX tickets_user_idx     ON chat.tickets (user_id, created_at DESC);
CREATE INDEX tickets_awaiting_idx ON chat.tickets (classification, urgency, created_at) WHERE status = 'awaiting';
-- Ticket de mídia criado por esta tarefa: kind='media', classification='MIDIA', escalated=true,
-- status='awaiting', urgency='urgente', brand NULL, message='[áudio]' etc., trace_id = rastro da mensagem.

CREATE VIEW ops.v_tickets AS SELECT * FROM chat.tickets;   -- leitura do painel; o painel pode estendê-la (CREATE OR REPLACE, só colunas a mais)
```
O mapeamento do vocabulário de PF: `aberto` = `status='awaiting' AND escalated`; `concluido` = `answered_human` (gravado só pelo painel, `UPDATE … WHERE status='awaiting'`). As colunas de busca (`search_key`) e os índices por marca/status são do painel (migration 1xx), porque dependem das telas dele.

`access_decision` ∈ `allowed, unknown, pending_optin, optin_accepted, optin_declined, declined, revoked, rate_limited, escalated_media, ignored_type, identity_conflict, error` e o valor **transitório** `processing` (escrito pelo primeiro INSERT, sempre trocado antes do commit; teste garante que nunca fica commitado). `decision` aceita ainda `send_failed` e `revoked_before_send` (AC-23, AC-32), gravados pelo despachante. `discard_reason` de `outbound_messages`: `revoked_before_send`, `status_changed`, `paused`, `brand_disabled`.

#### 4.2.4 Schema `ops`

```sql
CREATE TABLE ops.system_state (key text PRIMARY KEY, value jsonb NOT NULL, updated_at timestamptz NOT NULL, updated_by text NOT NULL);
-- chaves: team_group_enabled (bool), paused (bool), brand_enabled:<marca> (bool) — ver 4.3.2 (R4). O painel acrescenta as suas.

CREATE TABLE ops.audit_log (               -- imutável: trigger recusa UPDATE/DELETE; falha ao gravar aborta a ação
  id bigserial PRIMARY KEY, at timestamptz NOT NULL,
  actor_type text NOT NULL CHECK (actor_type IN ('cli','panel','system')), actor text NOT NULL,
  action text NOT NULL, target_type text NOT NULL, target_id text NOT NULL,
  old_value jsonb, new_value jsonb);       -- sem nome, telefone, BSUID nem texto: só ids e status

CREATE TABLE ops.alerts (                  -- espelha as colunas do AUTO-1 (o painel reaproveita as telas)
  id bigserial PRIMARY KEY,
  severity text NOT NULL CHECK (severity IN ('info','warn','high','critical')),
  kind text NOT NULL,                      -- hoje: rate_limit | identity_conflict | welcome_failed | send_failure
  message text NOT NULL,                   -- PT-BR, sem PII, sem texto de mensagem
  payload jsonb NOT NULL DEFAULT '{}',     -- ids e contagens; nunca telefone/nome/texto
  user_id bigint REFERENCES access.users(id), ticket_number text, auto_action text,
  dedup_key text, acknowledged_at timestamptz, acknowledged_by text, created_at timestamptz NOT NULL);
CREATE INDEX alerts_created_idx ON ops.alerts (created_at DESC);
CREATE INDEX alerts_dedup_idx   ON ops.alerts (dedup_key, created_at DESC) WHERE dedup_key IS NOT NULL;

CREATE SEQUENCE ops.ticket_seq;            -- numeração J-nnnn de chat.tickets

CREATE TABLE ops.deliveries (              -- 1 linha por (alerta|ticket) x canal; ticket => source_id = chat.tickets.id
  id bigserial PRIMARY KEY,
  source_type text NOT NULL CHECK (source_type IN ('alert','ticket')),
  source_id bigint NOT NULL,
  channel text NOT NULL CHECK (channel IN ('email','group')),
  status text NOT NULL CHECK (status IN ('pending','sending','sent','failed','panel_only','skipped')),
  attempts smallint NOT NULL DEFAULT 0, next_attempt_at timestamptz NOT NULL, claimed_at timestamptz,
  last_error text, created_at timestamptz NOT NULL, sent_at timestamptz,
  UNIQUE (source_type, source_id, channel),
  CONSTRAINT ticket_never_emails CHECK (NOT (source_type = 'ticket' AND channel = 'email')));  -- decisão 9, garantida no banco
-- panel_only = chave do grupo desligada (AC-13); skipped = e-mail não configurado (PN-66) ou alerta criado com email=false (R3); failed = desistiu.

CREATE TABLE ops.security_counters (day date NOT NULL, kind text NOT NULL, count bigint NOT NULL, PRIMARY KEY (day, kind));
-- kind: invalid_signature | oversize_payload | bad_verify_token | invalid_json | wrong_recipient
CREATE TABLE ops.schema_migrations (version text PRIMARY KEY, checksum text NOT NULL, applied_at timestamptz NOT NULL);
```

#### 4.2.5 Invariantes garantidas pelo banco (não só pelo código)

| Invariante | Mecanismo |
|---|---|
| Mensagem processada no máximo uma vez (AC-7) | `UNIQUE(chat.message_trace.wa_message_id)`, primeiro comando da transação |
| `active` sempre tem aceite com data e versão (AC-18) | `CHECK active_needs_acceptance` |
| Um único aviso aberto por usuário; botão antigo não vale (AC-19) | índice parcial `optin_one_open_per_user` + payload do botão leva o id do aviso |
| Um telefone (mesmo com/sem 9º dígito) é um usuário (AC-9, AC-15) | `UNIQUE(phone_key)` |
| BSUID nunca de dois usuários (AC-8) | `UNIQUE(bsuid)` |
| Ticket nunca gera e-mail (decisão 9) | `CHECK ticket_never_emails` |
| Ticket escalado sempre tem urgência | `CHECK escalated_needs_urgency` |
| Auditoria imutável (PN-55) | trigger `BEFORE UPDATE OR DELETE ... RAISE` + `jarvis_app` sem UPDATE/DELETE |
| 61ª mensagem concorrente não passa (AC-29) | `SELECT ... FROM access.users WHERE id=$1 FOR UPDATE` antes de contar |

#### 4.2.6 Migrations

`backend/migrations/` — **PF usa o bloco 001–099; o painel reserva 101–107** (sem colisão, runner aplica em ordem numérica do prefixo e recusa prefixo duplicado):

| Migration | Conteúdo |
|---|---|
| `001_schemas_ops_base.sql` | schemas `access`, `chat`, `ops`; `system_state`; `audit_log` + trigger |
| `002_access.sql` | `users`, `optin_requests`, `access_attempts`, `unknown_first_messages` |
| `003_chat.sql` | `message_trace`, `outbound_messages` |
| `004_ops_alerts_deliveries.sql` | `alerts`, `ticket_seq`, `deliveries`, `security_counters` |
| `005_chat_tickets.sql` | `chat.tickets` + índices + `ops.v_tickets` (R1) |

SQL puro, `IF NOT EXISTS`, imutáveis depois de aplicadas, só para frente. `python -m app.db.migrate` aplica em ordem (uma transação por arquivo, lock consultivo, grava versão e SHA-256 em `ops.schema_migrations`, recusa checksum diferente); `--status` lista; `--check` sai com código ≠ 0 se faltar migration (usado no `/health` e no CI). **Nunca roda sozinho na subida** e, no servidor, só com confirmação do Caio (servidor compartilhado). Emendas futuras de PF usam `006..099`. Papéis: `jarvis_owner` (DDL, usado pelo runner) e `jarvis_app` (DML; sem UPDATE/DELETE em `ops.audit_log`). Reversão: ver seção 9. Consequência para o painel: a migration `102` dele deixa de criar `chat.tickets` (já existe em `005`) e passa a só acrescentar colunas, índices por marca/status, `search_key` e, se quiser, estender a visão.

#### 4.2.7 Retenção e LGPD no modelo

| Dado | Onde | Quem lê | Retenção |
|---|---|---|---|
| Cadastro (nome, telefone, UFs…) | `access.users` | CLI, painel | enquanto houver acesso (prazo pós-desligamento: jurídico) |
| Aceite (data, versão do aviso, histórico) | `users.accepted_*`, `optin_requests`, `audit_log` | painel | com o cadastro; auditoria sem PII |
| Corpo da mensagem de autorizado | `chat.message_trace.body` | painel | 12 meses (índice por `received_at` pronto) |
| Rastro de qualquer mensagem (sem corpo) | `chat.message_trace` | painel | 12 meses |
| 1ª mensagem de desconhecido | `access.unknown_first_messages` | só Pedindo acesso | 12 meses (Q17), **gravada só com a chave ligada** |
| Alertas / entregas | `ops.*` | painel | 12 meses; sem PII |
| Tickets | `chat.tickets` | painel | 12 meses; mídia guarda só o rótulo do tipo, nunca o conteúdo |

**Nesta tarefa nada é expurgado** (a rotina de retenção e a exclusão a pedido são do painel, PN-57/58); o modelo permite: anonimizar = sobrescrever `name`, `phone_e164`, `phone_key`, `bsuid` com valores `anon:<id>` e anular `body`/`to_phone`/`template` das linhas do usuário (e `message`/`summary` dos tickets). Como `audit_log`, `alerts` e `security_counters` não têm PII, nada nelas precisa ser reescrito.

#### 4.2.8 Contrato com o painel.1 (esta tarefa é a dona do modelo)

| Tela / ação do painel | Lê | Escreve (via serviço, nunca SQL solto) |
|---|---|---|
| Pessoas → Autorizadas / Retiradas | `access.users` (status, `revoked_*`, `declined_at`) | `services/users.py`: `register`, revogar (motivo da lista fixa), reenviar/reativar, `validate_row` para importar (o painel grava só as válidas, o CLI exige todas) |
| Pessoas → Pedindo acesso | `access_attempts` (`user_id IS NULL AND dismissed_at IS NULL`), `unknown_first_messages` (exibição atrás de chave própria do painel, PN-37) | `services/attempts.py`: `dismiss`; cadastrar a partir do pedido preenche `access_attempts.user_id` |
| Inbox / Tickets | `chat.message_trace` + `chat.outbound_messages` (linha do tempo) e `ops.v_tickets` | concluir / reclassificar / urgência: `UPDATE chat.tickets … WHERE status='awaiting'` (concorrência PN-17), só pelo módulo de administração de tickets do painel |
| Escalados | `ops.v_tickets` (`escalated AND status='awaiting'`, `urgency`, `classification`) | idem |
| Alertas | `ops.alerts` (+ `ops.deliveries` para o estado de e-mail/grupo) | `acknowledged_*`; novos alertas por `alerts.raise_alert(..., email=…)` |
| Controles (pausa, marca, chave do grupo) | `ops.system_state` | `services/flags.py` (grava, audita e invalida o cache); o envio consulta `reply_policy` (4.3.2) |
| Métricas | `message_trace` por `decision`, `ops.security_counters`, `ops.alerts` | — |
| Auditoria / LGPD | `ops.audit_log`; exportação por `user_id` em `users`, `message_trace`, `outbound_messages`, `chat.tickets` | exclusão = anonimização (4.2.7) |

Fora do contrato: os "tickets" J-nnnn de **toda** pergunta respondida pelo bot (`kind='question'`) passam a ser escritos pelo módulo de resposta; a tabela já os comporta. Qualquer coluna nova que o painel peça entra por migration aditiva.

### 4.3 Contratos

#### 4.3.1 HTTP

```
GET  /webhook/whatsapp?hub.mode=subscribe&hub.verify_token=…&hub.challenge=…
  200 text/plain  <challenge>            (token correto; comparação em tempo constante sobre bytes)
  403                                     (token errado/ausente; challenge nunca é ecoado)

POST /webhook/whatsapp        header X-Hub-Signature-256: sha256=<hex>
  200 {"status":"ok"}   assinatura válida (inclui payload só de statuses, de outro phone_number_id, ou sem mensagens)
  403                   assinatura ausente/mal formada/errada — corpo vazio, SEM parse, SEM banco, SEM resposta (AC-2)
  413                   Content-Length ou corpo acima de 65536 bytes — antes da assinatura (AC-4)
  400                   assinatura válida mas JSON inválido
  429                   limite HTTP por IP (X-Real-IP do nginx)
  503                   falha transitória de banco (a Meta reentrega) — AC-3

GET  /health   200 {"status":"ok"} | 503 {"status":"degraded"}   (SELECT 1 + migrations em dia; sem versão/nome em produção)
```
Nenhuma outra rota existe (nginx também só encaminha estas duas; o painel acrescenta as dele). 403 em vez de 401 segue o AUTO-1; nenhum cliente legítimo depende do código.

#### 4.3.2 Interfaces internas

```python
# services/channel.py — interface de canal (ADR 2026-10-19), só o que este incremento usa
class Channel(Protocol):
    async def send_text(self, to: str, body: str) -> SendResult: ...
    async def send_template(self, to: str, template: TemplateMessage) -> SendResult: ...
    async def send_reply_buttons(self, to: str, body: str, buttons: Sequence[ReplyButton]) -> SendResult: ...
    def verify_signature(self, raw_body: bytes, header: str) -> bool: ...
@dataclass(frozen=True)
class SendResult: ok: bool; wa_message_id: str|None; error_code: str|None; error_subcode: str|None; transient: bool; ambiguous: bool
# sendList / sendMedia entram nos incrementos que precisarem.

# services/answering.py — PONTO DE EXTENSÃO DO CÉREBRO (chamado DEPOIS do commit, fora de transação)
@dataclass(frozen=True)
class AnswerContext: user_id: int; profile: str; ufs: list[str]; channels: list[str]; brands: list[str]; text: str; inbound_wa_message_id: str; received_at: datetime
@dataclass(frozen=True)
class Answer: kind: str; text: str            # kind precisa existir no CHECK de chat.outbound_messages
class AnswerProvider(Protocol):
    async def answer(self, ctx: AnswerContext) -> Answer: ...
class UnderConstructionProvider: ...           # ESTA tarefa: devolve Answer("under_construction", TEXT_UNDER_CONSTRUCTION)

# services/outbox.py + services/reply_policy.py — política de resposta (R4)
class ReplyPolicy(Protocol):
    async def allow(self, conn, user: UserRow, kind: str, brand: str|None = None) -> PolicyDecision: ...  # PolicyDecision(ok, reason)
class AllowAllPolicy: ...                      # padrão de outbox e dos testes
class FlagsReplyPolicy:                        # reply_policy.py — instalada por main.py no lifespan: outbox.set_reply_policy(FlagsReplyPolicy(flags))
    # paused=true            -> nega TODA resposta automática conversacional (reason='paused')
    # brand_enabled:<m>=false -> nega respostas de conteúdo daquela marca (reason='brand_disabled'); sem marca (respostas fixas): ignora
    # isentas da pausa: welcome_template, optin_reminder, optin_confirmation, optin_declined (consentimento/LGPD não depende de pausa)
    async def brand_allows(self, brand: str) -> bool: ...   # async (lê Flags com cache); usado também pelo futuro módulo de resposta

# services/flags.py (R4)
class Flags:
    async def get_bool(self, key: str, default: bool) -> bool          # cache TTL 30 s por processo
    async def set_bool(self, conn, key: str, value: bool, actor: str)  # grava system_state, audita (mesma transação) e INVALIDA o cache
    # chaves aceitas: team_group_enabled | paused | brand_enabled:<marca> (marca ∈ core/values.BRANDS; outra => erro). Padrões: false | false | true
    async def team_group_enabled(self) -> bool

# services/alerts.py (R3)
async def raise_alert(conn, kind, severity, message, payload, *, user_id=None, dedup_key=None,
                      dedup_window=None, email: bool = True) -> int | None
    # email=False: NÃO cria a linha de entrega 'email' (ações do próprio Caio, Q17 do painel); a linha 'group' segue a chave do grupo.
    # dedup_key com dedup_window=None: qualquer alerta anterior com a chave suprime (PF-16). Chamadores de PF sempre passam janela (AC-8: 24 h; AC-29: 1 h).
    # Parâmetros nomeados opcionais acrescentados em PF-16: flags=, settings=, clock=.
    # Todos os chamadores de PF usam o padrão (email=True).

# services/users.py (R2)
def validate_row(row: Mapping[str, Any], *, now: datetime) -> ValidatedUser | list[FieldError]   # PURA: sem banco, sem rede, sem relógio global
async def register(conn, data: ValidatedUser, *, actor: str, actor_type: str = "cli", enqueue_welcome: bool = True) -> UserRow
    # roda na transação de quem chama (CSV em lote = uma transação); duplicata (phone_key) => erro tipado DuplicatePhone;
    # enqueue_welcome=False só para testes/migração. O painel e a importação de pessoas chamam estas duas funções.
```
Regra do ponto de extensão do cérebro: um texto de usuário `active`, abaixo do limite, que não é "sair", fecha a transação 1 com `reply_kind='pending'`; em seguida (fora da transação) `AnswerProvider.answer()` é chamado, e uma transação 2 curta grava a linha de `chat.outbound_messages`. Quem implementar o cérebro troca o provedor e acrescenta o `kind` ao CHECK; **não toca no pipeline**. A política de resposta é consultada **no despacho** (4.4.7), portanto cobre também respostas de um provedor futuro.

#### 4.3.3 CLI (`python -m app.cli`, executado por SSH no servidor)

| Comando | Efeito | AC |
|---|---|---|
| `user add --name --phone --profile --company --ufs SP\|RJ --channels … --brands … --manager … [--by]` | `validate_row` → `register`; valida os 8 campos (rejeita indicando o campo), cria `pending_optin` + `optin_request` + linha de boas-vindas, audita, **espera até 20 s** o resultado do envio e imprime `enviado`/`falhou: <motivo>`/`na fila` | 15, 17 |
| `user import --file x.csv [--dry-run]` | cabeçalho `nome,telefone,perfil,empresa,ufs,canais,marcas,gestor`; listas separadas por `\|`; delimitador `,` ou `;`; UTF-8 (BOM aceito); `validate_row` em todas as linhas; **tudo ou nada** (uma transação com `register` por linha); erro lista linha, campo, motivo | 16 |
| `user revoke (--phone\|--id) --reason <código> [--by]` | `revoked`, idempotente, audita, descarta resposta pendente na hora do envio | 34, 22, 23 |
| `user resend (--phone\|--id)` | `pending_optin`/`declined`/`revoked` → novo aviso, `pending_optin`; `revoked` zera `accepted_at` (histórico na auditoria); `active` recusa com mensagem clara | 35 |
| `user swap-number --from A --to B` | numa transação: revoga A (`number_change`), cria B `pending_optin` com os mesmos dados, novo aviso | 26 |
| `user list [--status]` | lista com telefone mascarado | — |
| `flag get\|set team-group on\|off` | lê/grava `team_group_enabled`, audita | 13 |
| `flag set paused on\|off` · `flag set brand <marca> on\|off` | grava `paused` / `brand_enabled:<marca>`, audita (efeito em ≤ 1 min; invalida o cache do processo local) | R4 |

O CLI não envia nada: enfileira e consulta. Um único remetente (o servidor) evita duplicidade e dispensa o token do WhatsApp na sessão SSH. `--by` assume o usuário do sistema operacional. Observação: o CLI roda em outro processo; a invalidação de cache vale para o processo que escreve, e o servidor enxerga a mudança em até 30 s pelo TTL (dentro do teto de 1 min do painel).

#### 4.3.4 Textos fixos (propostas até a aprovação da Q9; todos sobrescrevíveis por ambiente)

| Chave | Texto proposto |
|---|---|
| `TEXT_UNDER_CONSTRUCTION` | "Oi! Ainda estou em construção e não consigo responder perguntas por aqui. Em breve estarei pronto para te ajudar. 🚧" |
| `TEXT_OPTIN_CONFIRMED` | "Obrigado! Seu aceite foi registrado. Já pode me mandar suas dúvidas." |
| `TEXT_OPTIN_DECLINED` | "Tudo bem. Sem o aceite eu não consigo te atender por aqui. Se mudar de ideia, fale com seu gestor." (de `docs/legal/aviso-privacidade.md`) |
| `TEXT_OPTIN_REMINDER` | "Para começar, preciso do seu aceite do aviso de privacidade. Toque em um dos botões." |
| `TEXT_RATE_LIMIT` | "Você atingiu o limite de mensagens por hora. Tente novamente em alguns minutos." (Caio) |
| `TEXT_OPT_OUT_CONFIRMED` | "Pronto, você não receberá mais avisos do Jarvis. Continua podendo fazer perguntas por aqui." |
| `TEXT_MEDIA_ACK` | "Recebi sua mensagem. Ainda não consigo ouvir áudios nem ver imagens, mas avisei a equipe." |

### 4.4 Fluxos

#### 4.4.1 POST do webhook — ordem obrigatória (AC-31)

0. Limite HTTP por IP real (reuso AUTO-1; `X-Real-IP`, nunca `X-Forwarded-For`).
1. **Tamanho:** `Content-Length` > 64 KB → 413; senão leitura em stream com teto (AC-4). *(AC-31 foi reescrito para "tamanho, assinatura", coerente com o AC-4, que exige 413 sem validar assinatura de corpo completo.)*
2. **Assinatura:** HMAC SHA-256 do App Secret sobre os **bytes brutos**; comparação de **bytes** com `hmac.compare_digest` (str não-ASCII no header derruba a versão do AUTO-1 com TypeError → 500; aqui vira 403). Falha → contador `invalid_signature`, 403, nada mais.
3. JSON; `object == whatsapp_business_account` (senão 200 sem fazer nada).
4. Para cada `entry/changes` com `field == messages`: `metadata.phone_number_id` ≠ configurado → contador `wrong_recipient`, descarta **todo** o `value` (mensagens e statuses) sem tocar no banco (AC-6).
5. `statuses[]` → `pipeline.handle_status` (AC-5): `failed` de envio do Jarvis atualiza `outbound_messages` e o rastro de origem (casa por `wa_message_id`) e avalia o alerta de falha repetida (4.4.6); `sent/delivered/read` são ignorados. Nunca há decisão de acesso, resposta nem contagem no limite.
6. `messages[]` → cada uma em **transação própria** (AC-6: falha de uma não derruba as outras):
   1. `INSERT INTO chat.message_trace … ON CONFLICT (wa_message_id) DO NOTHING RETURNING id` com decisão `processing` e `access_decision` provisório `error` (exigido pelo CHECK `has_sender`, pois o remetente só é conhecido em 6.2; `trace.finalize` sobrescreve, e `trace.ensure_finalized` é chamado antes do COMMIT — PF-22). Sem linha retornada = duplicata → fim, sem efeito algum (AC-7, inclusive duplicata concorrente: o segundo INSERT espera o commit do primeiro no índice único).
   2. **Remetente** (`identity.resolve`): `wa_id`→`phone_key`; BSUID; `SELECT … WHERE phone_key=$1 OR bsuid=$2 FOR UPDATE`. Dois usuários diferentes, ou BSUID já guardado diferente do recebido, ou BSUID recebido que pertence a um usuário com outro `phone_key` (acrescentado na implementação PF-14, 2026-10-23: BSUID que reaparece sob outro número não é aceito) → `identity_conflict` (AC-8). Nenhum → desconhecido.
   3. **Acesso** (`access.decide`, função pura, 4.4.2). Só aqui se lê o status; sem cache (AC-22).
   4. **Limite** (só `active`; 4.4.5). 61ª → `rate_limited`.
   5. **Tratador por conteúdo** (texto, "sair", mídia, botão, ignorado).
   6. Grava rastro final, tentativa, alerta, ticket, linhas de `outbound_messages`/`deliveries`; **COMMIT**.
7. Depois do commit (e depois da resposta 200): `BackgroundTasks` recebe os ids de saída → `answer_and_enqueue` (só para texto `allowed`), `outbox.dispatch`, `deliveries.dispatch`. Nada disso é aguardado pela resposta (AC-3).
8. Retorna 200. Se qualquer mensagem falhou por erro **transitório de banco** (conexão, deadlock, pool esgotado), as demais seguem e a resposta final é **503** — a Meta reentrega o payload todo e a idempotência absorve as já processadas. Erro **não** transitório (bug) em uma mensagem: log ERROR com `request_id`, rastro `error` gravado em transação separada, 200 (reentregar um bug por 7 dias só gera ruído; ver Q22).

Nada que custa (mídia, busca, modelo, resposta) acontece antes de `allowed`: não existe no código cliente de mídia, e o `AnswerProvider` só é chamado no passo 7 para texto `allowed`.

#### 4.4.2 Decisão de acesso (`access.decide`, tabela)

| Remetente | Conteúdo | `access_decision` | Efeitos (tudo na transação) |
|---|---|---|---|
| sem cadastro | qualquer | `unknown` | upsert em `access_attempts` (+1, `last_attempt_at`, reabre `dismissed_at`); na 1ª vez, tipo/tamanho e — se `UNKNOWN_FIRST_TEXT_STORE` e for texto — `unknown_first_messages`; **sem resposta, sem alerta, sem entrega** |
| conflito de identidade | qualquer | `identity_conflict` | tentativa (`reason=identity_conflict`) + alerta `identity_conflict` (dedup 24 h por BSUID) |
| `declined` / `revoked` | qualquer (inclui toque em Aceito antigo) | `declined` / `revoked` | tentativa com o motivo, sem corpo, sem alerta (AC-12, AC-27) |
| `pending_optin` | toque em **Aceito** do aviso aberto | `optin_accepted` | `active`, `accepted_at`, versão, associa BSUID, `optin_request=accepted`, confirmação na saída |
| `pending_optin` | toque em **Não aceito** do aviso aberto | `optin_declined` | `declined`, `declined_at`, aviso `declined`, texto de recusa na saída |
| `pending_optin` | texto, mídia, botão de aviso antigo | `pending_optin` | se `optin_reminder_at` nulo ou ≥ 24 h: lembrete interativo + atualiza `optin_reminder_at`; senão silêncio. Nada de conteúdo, nada baixado (AC-21) |
| `active` | (limite estourado) | `rate_limited` | 4.4.5 |
| `active` | texto `sair` (normalizado) | `allowed` (`reason=proactive_opt_out`) | `proactive_opt_out=true`; confirmação na saída; corpo guardado |
| `active` | texto | `allowed` | `counted_in_limit=true`, corpo guardado, `reply_kind='pending'` |
| `active` | áudio, imagem, vídeo, documento, figurinha, localização, contatos | `escalated_media` | ticket em `chat.tickets` (R1) + entrega ao grupo (`panel_only` se chave desligada); `media_ack` ("equipe avisada") na saída se `MEDIA_ACK_ENABLED` (padrão ligado); **sem alerta, sem e-mail** (AC-36) |
| `active` | botão, interativo, reação, `system`, `unsupported` | `ignored_type` | rastro apenas (Q21) |

A tabela é testada exaustivamente como função pura (status × conteúdo × botão atual/antigo).

#### 4.4.3 Opt-in

1. `users.register` (CLI/painel): valida, cria `access.users` (`pending_optin`), `optin_requests` (`open`, `notice_version` = `NOTICE_VERSION`), `outbound_messages` (`welcome_template`, `required_status='pending_optin'`), `audit_log` — **uma transação**; depois o servidor envia.
2. Envio = **modelo aprovado** (`WELCOME_TEMPLATE_NAME`, idioma `pt_BR`), com o nome e dois botões de resposta rápida cujos payloads são `OPTIN_ACCEPT:<id do aviso>` e `OPTIN_DECLINE:<id do aviso>`. Nunca texto livre (AC-17). Lembrete (AC-21): mensagem interativa com os mesmos payloads, dentro da janela de 24 h aberta pelo usuário.
3. O toque chega como mensagem `button` (modelo) ou `interactive.button_reply` (lembrete); o parser os unifica em `ButtonReply(payload)`. Vale **só** se `<id>` é o aviso `open` do usuário (AC-19). Reenvio/reativação marca o aviso anterior `superseded` e abre outro.
4. Falha da boas-vindas (qualquer erro do envio): usuário permanece `pending_optin`, `outbound_messages.status='failed'`, **alerta `welcome_failed`** (high; dedup 1 h por usuário) com e-mail, e o CLI mostra o motivo; `user resend` tenta de novo (AC-17). Nenhum fallback de texto livre.
5. Reativação (`resend` de `revoked`/`declined`): `status → pending_optin`, `accepted_at`/`revoked_*` zerados (valores antigos vão para `audit_log.old_value`), novo aviso (AC-35).

#### 4.4.4 Revogação em até 1 minuto, inclusive com conversa em andamento

- **Sem cache:** a decisão lê `access.users` a cada mensagem (consulta indexada, < 5 ms a 20 mil msg/dia). A próxima mensagem após o `COMMIT` da revogação já é bloqueada (AC-22; o teto de 60 s do AC é folga).
- **Antes de enviar** (AC-23): `outbox.dispatch` relê o status do usuário **no banco** imediatamente antes de chamar a Meta e exige `status = required_status`. Diferente → `status='discarded'`, `discard_reason='revoked_before_send'`, rastro `decision='revoked_before_send'`, nada é enviado. Vale também para um futuro `AnswerProvider` demorado, que passa pelo mesmo despachante.
- A revogação não depende de job periódico nem de reinício.

#### 4.4.5 Limite de 60 mensagens/hora (AC-29)

Na transação, depois de `access.decide` ≥ `active`: o `SELECT … FOR UPDATE` da linha do usuário (passo 6.2) serializa mensagens concorrentes do mesmo usuário; então `count(*) FROM chat.message_trace WHERE user_id=$1 AND counted_in_limit AND received_at > $now - 60 min` (índice parcial). `≥ 60` → `rate_limited`, `counted_in_limit=false` (**bloqueadas não prolongam a janela**). Aviso ao usuário: se `rate_limit_notice_at` nulo ou ≥ 60 min, enfileira `rate_limit_notice` e atualiza o campo; senão silêncio. Alerta `rate_limit` (warn; `dedup_key = rate_limit:<user_id>`, 1 por hora, `INSERT … WHERE NOT EXISTS` sob a mesma trava da linha do usuário) → e-mail. Duplicatas nunca chegam aqui (passo 6.1) e statuses não passam por aqui (passo 5).

#### 4.4.6 Alertas, e-mail, grupo e tickets

`alerts.raise_alert(conn, kind, severity, message, payload, user_id, dedup_key, dedup_window, email=True)` (na transação do chamador): aplica o dedup sob `pg_advisory_xact_lock(hashtextextended(dedup_key, 0))`; se criar, insere em `ops.deliveries` a linha `group` (`pending` se a chave estiver ligada, senão `panel_only`) e, **só se `email=True`**, a linha `email` (`pending`, ou `skipped` se destinatário/chave do provedor não configurados). `tickets.create_media_ticket` insere o ticket em `chat.tickets` (kind `media`, classificação `MIDIA`, `escalated`, `awaiting`, urgência `urgente`; número `J-nnnn` da sequência) e **uma** linha `group` com `source_id = chat.tickets.id` (nunca `email`; o CHECK impede).

`deliveries.dispatch`: claim atômico (`UPDATE … SET status='sending' WHERE id=$1 AND status='pending' RETURNING`), envio, e `sent` | `pending` com backoff (60 s, 5 min, 15 min, 1 h, 3 h; `EMAIL_MAX_ATTEMPTS=6` → `failed`, **sem novo e-mail**) | `skipped`. Falha nunca propaga ao webhook (AC-37); log sem PII nem endereço. Conteúdo:

- **E-mail:** assunto `[ALTA] Jarvis: <rótulo do tipo>`; corpo com severidade, tipo, texto do alerta (PT-BR, sem PII), contagem e instante (America/Sao_Paulo), "usuário #<id>" quando houver, e link `PANEL_BASE_URL/dashboard/alerts` se configurado. **Nunca** telefone, nome ou texto de mensagem. Sem botões.
- **Grupo (chave ligada):** uma linha curta sem telefone/nome/texto ("Mídia de usuário #12 (áudio) aguardando a equipe — ticket J-0012"). Chamado só se `flags.team_group_enabled()` (cache de 30 s) for verdadeiro **na hora do envio**; desligado depois da criação, a linha vira `panel_only`.
- **Falha de envio repetida (AC-33):** a cada falha definitiva de `outbound_messages` (exceto boas-vindas, que têm alerta próprio), conta falhas dos últimos `SEND_FAILURE_WINDOW_MINUTES` (30); `≥ SEND_FAILURE_ALERT_THRESHOLD` (3) → alerta `send_failure` (high; dedup global 1 h).

#### 4.4.7 Despacho de saída (`outbox.dispatch`)

Claim atômico; relê o usuário (4.4.4); consulta `ReplyPolicy.allow` (R4: pausa e marca desligada; negado → `status='discarded'`, `discard_reason='paused'|'brand_disabled'`, rastro `send_result='discarded'`, nada enviado, **sem alerta**); envia pelo `Channel`; atualiza `outbound_messages` e o rastro (`reply_wa_message_id`, `reply_sent_at`, `latency_ms`, `send_result`). Tentativas: **no máximo 2**, e a segunda só se a requisição comprovadamente não saiu (`ConnectError`/`ConnectTimeout`) ou a Meta respondeu 429/503; `ReadTimeout` é ambíguo → `unknown`, **não reenvia** (resposta ao usuário é at-most-once; sem retentativa infinita). Erro 4xx = definitivo → `failed`, rastro `decision='send_failed'`. Recuperação (laço de fundo e na subida): `sending` há mais de 2 min → `unknown` (sem reenviar); rastro com `reply_kind='pending'` há mais de 5 min → `send_result='abandoned'`; entrega `sending` há mais de 5 min → volta a `pending`.

#### 4.4.8 Telefone (`services/phone.py`, pura)

Entrada tolerante (espaços, parênteses, hífens, `+`). Exige DDI. Brasil (`55`): DDD na lista de DDDs válidos; celular (primeiro dígito 9, 9 dígitos) → canônico com o 9; 8 dígitos começando em 6–9 (celular antigo) → insere o 9; começando em 2–5 = fixo → recusa ("Telefone fixo não recebe WhatsApp"). Outros países: E.164 (8–15 dígitos). `phone_e164` = forma canônica (destino das respostas — lição do piloto: a Meta entrega o `wa_id` brasileiro sem o 9 e o número de teste só envia para a forma cadastrada); `match_key` = `phone_e164` sem o `+` e, se for celular BR de 13 dígitos, sem o 9º dígito. A lista de DDDs é conferida contra a Anatel na implementação.

### 4.5 Configuração e segredos

`backend/.env` no servidor (`/opt/jarvis/backend/.env`, modo 600, lido por pydantic-settings), **nunca** versionado nem lido por agentes; o repositório traz `backend/configuracao-modelo.txt` sem valores (mesmo truque do piloto). Segredos são `SecretStr` (mascarados em `repr`/log pelo `log_redaction`, que registra todo `SecretStr` do `Settings`).

| Variável | Padrão | Segredo | Observação |
|---|---|---|---|
| `DATABASE_URL` | — (obrigatória) | sim | role `jarvis_app` |
| `WHATSAPP_APP_SECRET` / `WHATSAPP_VERIFY_TOKEN` / `WHATSAPP_ACCESS_TOKEN` | obrigatórias | sim | |
| `WHATSAPP_PHONE_NUMBER_ID` | obrigatória | não | filtro do AC-6 |
| `WHATSAPP_GRAPH_VERSION` / `WHATSAPP_API_BASE` | `v25.0` (a do piloto) / `https://graph.facebook.com` | não | base validada contra allow-list (SSRF) |
| `WELCOME_TEMPLATE_NAME` / `_LANG` / `NOTICE_VERSION` | — / `pt_BR` / `rascunho-2026-10-19` | não | versão gravada no aceite |
| `MAX_WEBHOOK_PAYLOAD_BYTES` | 65536 | não | AC-4 |
| `RATE_LIMIT_PER_HOUR` / `_WINDOW_MINUTES` | 60 / 60 | não | AC-29 |
| `OPTIN_REMINDER_INTERVAL_HOURS` | 24 | não | AC-21 |
| `UNKNOWN_FIRST_TEXT_STORE` | **false** | não | ligar só após Q17; testes usam true |
| `MEDIA_ACK_ENABLED` | **true** | não | decisão 10 (AC-36); flag mantida para desligar sem deploy |
| `TEAM_GROUP_ENABLED` | **false** | não | valor inicial; `ops.system_state` sobrepõe em tempo de execução (cache 30 s) |
| `WAHA_BASE_URL` / `WAHA_API_KEY` / `WAHA_SESSION` / `TEAM_GROUP_CHAT_ID` | `http://127.0.0.1:3000` / vazio / `default` / vazio | chave sim | exigidos só se a chave do grupo estiver ligada no ambiente |
| `RESEND_API_KEY` / `RESEND_FROM_EMAIL` | vazio | chave sim | vazio = e-mail `skipped`, aviso único no log |
| `ALERT_RECIPIENT_EMAIL` | **sem padrão** | sim (`SecretStr`) | vazio = idem; o AUTO-1 tem o endereço no código, aqui é proibido |
| `PANEL_BASE_URL` | vazio | não | link no e-mail, se houver |
| `EMAIL_MAX_ATTEMPTS` / `EMAIL_BACKOFF_SECONDS` | 6 / `60,300,900,3600,10800` | não | Q19 |
| `SEND_FAILURE_ALERT_THRESHOLD` / `_WINDOW_MINUTES` | 3 / 30 | não | AC-33 |
| `TEXT_*` | seção 4.3.4 | não | Q9 |
| `RL_WEBHOOK_MAX` / `RL_WEBHOOK_WINDOW` | 600 / 60 | não | borda HTTP |
| `PRODUCTION` / `LOG_LEVEL` | true / INFO | não | |

Validadores fail-fast: `DATABASE_URL`, segredos do WhatsApp e `WHATSAPP_PHONE_NUMBER_ID` presentes; hosts de URL base na allow-list; `TEAM_GROUP_ENABLED=true` no ambiente exige `WAHA_API_KEY` e `TEAM_GROUP_CHAT_ID`. Ausência do e-mail do destinatário **não** derruba o serviço (PN-66). `paused` e `brand_enabled:*` não têm variável de ambiente: vivem só em `ops.system_state` (padrões: não pausado, todas as marcas ligadas).

### 4.6 Logging e PII (AC-32)

JSON em uma linha (`ts`, `level`, `logger`, `event`, `request_id`, campos extras), formatter da biblioteca padrão, para stdout (PM2 grava em `/var/log/jarvis/{out,error}.log`). Eventos **como registrados no código** (reconciliado na PF-41; `event` é o primeiro argumento do logger):

| Origem | Evento (nível) | Campos extras |
|---|---|---|
| `api/webhook.py` | `webhook_received` (info) | `messages`, `statuses` |
| | `webhook_payload_too_large` (warning) | , |
| | `webhook_signature_invalid` (warning) | , |
| | `webhook_wrong_recipient` (warning) | `count` |
| | `webhook_processing_error` (exception) | `item` (`status` ou `message`) |
| `services/pipeline.py` | `message_duplicate` (info) | `msg_type` |
| | `message_decision` (info) | `decision`, `user_id`, `msg_type`, mais `attempt_id` (tentativa de acesso) ou `reply_kind` (usuário autorizado); `decision=error` com `reason=sender_missing` quando não há remetente |
| | `message_processing_error` (error) | `error_class`, `msg_type` |
| | `trace_mark_error_failed` (error) | `error_class` |
| | `status_processing_error` (error) | `error_class` |
| `services/outbox.py` | `outbound_sent`, `outbound_failed`, `outbound_discarded`, `outbound_unknown` (info; nome = `outbound_` + status final) | `outbound_id`, `user_id`, `kind` (`outbound_unknown` na recuperação traz `reason=stuck`) |
| | `reply_abandoned` (info) | `trace_id` |
| `services/alerts.py` | `alert_created` (info) | `alert_id`, `kind`, `severity`, `channels` |
| `services/deliveries.py` | `delivery_dispatched` (info; o resultado vai em `status`: sent, failed, gave_up, skipped...) | `delivery_id`, `channel`, `status`, `attempts` |
| `services/answering.py` | `answer_provider_failed` (error) | `error` (classe da exceção) |
| `services/workers.py` | `worker_step_failed` (error) | `step`, `error` |
| `main.py` | `app_started`, `app_stopped` (info) | , |
| | `unhandled_error` (exception) | `path` |
| | `health_degraded` (warning) | `error_type` |
| | `dispatch_step_failed` (error) | `step`, `error` |
| | `answer_failed` (error) | `error` |

Os adaptadores externos (`whatsapp.py`, `waha.py`, `notifications.py`) registram mensagens curtas sem evento nomeado (por exemplo "envio WhatsApp falhou" com `kind`, `error_code`, `error_subcode`, `transient`, `ambiguous`; "waha send failed: ..."; "email send failed: ..."), sempre só com a classe da exceção ou o código HTTP.

**Não existem** logs próprios para `ticket_created`, `flag_changed` e `migration_applied` (previstos no desenho original): tickets, flags e migrations ficam registrados no banco (`chat.tickets`, auditoria e tabela de migrations) e o runner de migrations imprime em stdout/stderr. `latency_ms` não vai no log de decisão: fica no rastro (`chat.message_trace`) e só quando a resposta é enviada. O rastro nasce com `access_decision='error'` provisório (`trace.begin`) e é sempre finalizado ou marcado como erro antes do COMMIT.

**Proibido em log:** corpo de mensagem, telefone completo (máscara `***1234`), BSUID, e-mail do destinatário, qualquer segredo. Exceções do processador vão para `logger.exception`, portanto o pipeline nunca coloca texto de mensagem em mensagens de exceção. Defesa em profundidade: `log_redaction` do AUTO-1 + padrões de telefone (E.164 e `55\d{10,11}`) e e-mail. Contadores de segurança agregam em memória e são gravados em lote a cada 30 s (`ops.security_counters`) para um flood de assinaturas inválidas não virar escrita no banco por requisição.

### 4.7 Estratégia de testes

- **Camadas** (`backend/tests/`): `unit/` (puros, sem banco, sempre rodam), `integration/` (Postgres real; marcador `db`; pulam sem `TEST_DATABASE_URL`), `security/` (intrusão e ordem). `pytest.ini` com `asyncio_mode = auto` (AUTO-1). Suíte final da PF-41: 963 testes, 0 pulados.
- **Banco de teste:** `postgres:16` (service container no CI; local se houver). Fixture de sessão aplica as migrations pelo runner; cada teste faz `TRUNCATE … RESTART IDENTITY CASCADE`. Testes de concorrência (duplicata simultânea, 61ª, aceite × revogação) usam conexões reais do pool, não mocks. **Nunca** o banco do droplet. Testes que acessam o banco fora do loop do `TestClient` abrem conexão própria (o pool do app pertence ao loop do `TestClient`).
- **Dublês:** `Clock` falso; `FakeChannel` (`tests/fakes.py`; registra chamadas em ordem e permite falhar por tipo de erro); `httpx.MockTransport` para Resend, WAHA e Graph (sem lib nova); `SpyAnswerProvider`; hook que revoga entre o commit e o envio (AC-23).
- **Fixtures reais da Meta** (`tests/fixtures/meta/*.json`): texto, botão de modelo, botão interativo, áudio, imagem, status `sent/delivered/read/failed`, payload múltiplo, outro `phone_number_id`, remetente só com BSUID. Começam da documentação da Cloud API e são **substituídas por capturas do número de teste** no marco M2 (Q5).
- **Garantias de "zero custo" (AC-31/AC-36):** (1) o `AnswerProvider` espião só é chamado para texto `allowed`; (2) teste de arquitetura varre o código-fonte e falha se algum módulo referenciar download de mídia da Graph API (`security/test_zero_cost.py`); (3) teste de ordem com `FakeChannel` e espiões registrando a sequência (`security/test_order.py`).
- **Divisões de arquivo em relação ao desenho original** (um único dono por arquivo): `integration/test_alerts_deliveries.py` virou `integration/test_alerts.py` + `integration/test_deliveries.py`; `integration/test_reply_policy_flags.py` virou `integration/test_flags.py` + `integration/test_reply_policy.py`. Os testes de pipeline ficaram em `test_pipeline_core.py`, `test_pipeline_access.py` e `test_pipeline_active.py`.
- **Mapa AC → testes (definitivo, PF-41).** Caminhos relativos a `backend/tests/`. AC-24 e AC-25 foram removidos (Q6). Todo AC ativo tem ao menos um teste automatizado; as únicas partes sem automação são o envio real do modelo de boas-vindas na Meta (parte do AC-17) e o envio real do e-mail pelo provedor (parte do AC-37), ambos manuais (PF-44).

| AC | Testes |
|---|---|
| AC-1 | `integration/test_webhook_http.py`, `security/test_signature.py`, `integration/test_app_e2e.py` |
| AC-2 | `security/test_signature.py`, `integration/test_webhook_http.py`, `security/test_replay_intrusion.py`, `integration/test_counters.py` |
| AC-3 | `integration/test_app_e2e.py`, `integration/test_webhook_http.py` (5xx transitório) |
| AC-4 | `integration/test_webhook_http.py`, `security/test_signature.py`, `security/test_replay_intrusion.py`, `integration/test_counters.py` |
| AC-5 | `unit/test_inbound.py` (statuses não geram mensagens; `failed` com código), `integration/test_pipeline_core.py` (status `failed` atualiza o rastro) |
| AC-6 | `unit/test_inbound.py` (divisão por destinatário, payload múltiplo), `integration/test_webhook_http.py`, `security/test_replay_intrusion.py` |
| AC-7 | `integration/test_idempotency.py`, `integration/test_trace.py`, `security/test_replay_intrusion.py` |
| AC-8 | `integration/test_identity.py` |
| AC-9 | `unit/test_phone.py`, `integration/test_identity.py` |
| AC-10 | `integration/test_attempts.py`, `integration/test_pipeline_access.py`, `security/test_zero_cost.py` |
| AC-11 | `integration/test_pipeline_access.py`, `integration/test_attempts.py` |
| AC-12 | `integration/test_pipeline_access.py`, `integration/test_deliveries.py`, `security/test_zero_cost.py` |
| AC-13 | `integration/test_deliveries.py`, `integration/test_tickets.py`, `integration/test_email_privacy.py`, `unit/test_notifications_waha.py` |
| AC-14 | `unit/test_inbound.py` (remetente só com BSUID), `integration/test_pipeline_access.py` |
| AC-15 | `integration/test_users_admin.py`, `integration/test_cli_users.py`, `unit/test_validate_row.py` |
| AC-16 | `integration/test_users_admin.py`, `integration/test_cli_users.py` |
| AC-17 | `integration/test_optin_service.py`, `integration/test_optin.py`, `integration/test_users_admin.py`, `integration/test_outbox_enqueue.py`, `integration/test_outbox_dispatch.py`, `integration/test_outbox_recovery.py`; envio real do modelo na Meta: **manual** |
| AC-18 | `integration/test_optin.py`, `integration/test_pipeline_active.py` |
| AC-19 | `integration/test_optin.py`, `integration/test_users_resend_swap.py`, `unit/test_access.py` |
| AC-20 | `integration/test_optin.py` |
| AC-21 | `integration/test_optin.py`, `integration/test_optin_service.py` |
| AC-22 | `integration/test_revocation.py`, `integration/test_pipeline_access.py`, `integration/test_outbox_dispatch.py` |
| AC-23 | `integration/test_revocation.py`, `integration/test_optin_service.py` |
| AC-26 | `integration/test_users_resend_swap.py`, `integration/test_cli_resend_swap.py`, `integration/test_revocation.py` |
| AC-27 | `integration/test_pipeline_access.py`, `unit/test_access.py` |
| AC-28 | `unit/test_access.py`, `integration/test_replies_media.py`, `integration/test_pipeline_active.py`, `integration/test_idempotency.py` |
| AC-29 | `unit/test_rate_limit.py`, `integration/test_limits.py`, `integration/test_rate_limit.py` |
| AC-30 | `integration/test_replies_media.py`, `integration/test_answering.py`, `integration/test_app_e2e.py` |
| AC-31 | `security/test_order.py`, `security/test_zero_cost.py`, `security/test_signature.py`, `security/test_replay_intrusion.py`, `integration/test_trace_logs.py` |
| AC-32 | `integration/test_trace_logs.py`, `integration/test_trace.py`, `integration/test_email_privacy.py`, `unit/test_log_redaction.py` |
| AC-33 | `integration/test_outbox_dispatch.py` (4xx definitivo e transitório sem terceira tentativa), `integration/test_outbox_recovery.py` (alerta `send_failure`) |
| AC-34 | `integration/test_users_admin.py`, `integration/test_audit.py`, `integration/test_revocation.py` |
| AC-35 | `integration/test_users_resend_swap.py`, `integration/test_cli_resend_swap.py`, `integration/test_optin_service.py`, `integration/test_revocation.py` |
| AC-36 | `integration/test_replies_media.py`, `integration/test_tickets.py`, `integration/test_deliveries.py`, `security/test_zero_cost.py` |
| AC-37 | `integration/test_deliveries.py`, `integration/test_alerts.py`, `integration/test_email_privacy.py`, `integration/test_trace_logs.py`; envio real do e-mail pelo provedor: **manual** |

- **Requisitos de arquitetura R1–R4:** R1 (tickets) `integration/test_tickets.py` e `test_schema_ops_tickets.py`; R2 (cadastro/revogação) `unit/test_validate_row.py`, `integration/test_users_admin.py`; R3 (`email=False`) `integration/test_alerts.py`; R4 (política de resposta: pausa descarta sem enviar, marca desligada, cache invalidado na escrita, isenção do opt-in) `integration/test_flags.py` e `integration/test_reply_policy.py`.
- **Comandos de verificação** (registrados em `.claude/CLAUDE.md`, a partir de `backend/`): `.venv\Scripts\python -m pytest`, `.venv\Scripts\python -m ruff check app tests`, `.venv\Scripts\python -m mypy app`, `.venv\Scripts\python -m app.db.migrate --check`. CI (`.github/workflows/ci.yml`; só roda quando houver remoto GitHub) roda os quatro com Python 3.12 e Postgres 16.

### 4.8 Trade-offs

Resumo; alternativas completas e razões em `research.json` (`alternatives_considered`).

- **Idempotência por `UNIQUE` no próprio rastro e tudo numa transação** em vez de tabela separada e processamento fora da transação, porque só assim AC-3 (5xx e reentrega) e AC-7 (sem duplicar) valem juntos. Custo: estado transitório `processing` e testes com Postgres real.
- **Outbox (`outbound_messages`, `deliveries`) em vez de enviar inline**, porque o webhook não pode esperar a Meta/Resend/WAHA e nenhum alerta/ticket pode se perder em queda. Custo: um laço de varredura e duas tabelas.
- **Sem cache de status** (decisão lê o banco) em vez de cache de 30 s: custo de uma consulta indexada por mensagem, ganho de revogação imediata e zero estado divergente.
- **Limite de 60/h derivado do rastro, sob lock da linha do usuário**, em vez de contador/memória: exato e deslizante; custo: um `count` por mensagem de usuário ativo (índice parcial).
- **Resposta ao usuário at-most-once** (sem reenvio em timeout ambíguo) em vez de at-least-once: preferível perder uma resposta a duplicar mensagem a vendedor. E-mail/grupo, ao contrário, retentam.
- **`AnswerProvider` pós-commit** em vez de resposta dentro da transação: custo de uma segunda transação e de uma varredura; ganho de não refatorar o núcleo no incremento do cérebro.
- **Texto da 1ª mensagem em tabela separada, desligado por padrão em produção**: custo de perder o texto de quem escrever antes do "sim" do jurídico; ganho de nunca armazenar sem base legal.
- **Ticket único `chat.tickets` em vez de uma tabela só de escalados** (R1): ganho de uma entidade para o painel e para o módulo de resposta; custo de PF criar já agora colunas que só o painel usa (todas anuláveis ou com padrão).
- **Três schemas, migrations em SQL com runner mínimo, CLI sobre camada de serviço, 1 worker uvicorn, telefone sem biblioteca**: ver ADRs de 2026-10-23 e `research.json`.

### 4.9 Premissas de projeto atreladas às perguntas em aberto

Cada linha: o que o desenho assume, onde fica isolado, e o que muda se a resposta for outra. **Nenhuma bloqueia a Fase 3.**

| Pergunta | Premissa adotada | Isolamento | Se a resposta for diferente |
|---|---|---|---|
| **Q17** (jurídico, 1ª msg) | Guardar o texto **só com a chave ligada**; produção sobe **desligada**; retenção 12 meses | `UNKNOWN_FIRST_TEXT_STORE`; tabela `unknown_first_messages` | Jurídico nega: `DROP TABLE` + remover a chave (1 migration). Retenção menor: acrescentar expurgo por `stored_at` (painel PN-58) |
| **Q13** (dados fora do BR) | Aceitável (ADR 2026-10-20) | — | Se não: muda a hospedagem (ADR), não este desenho |
| **Q5** (BSUID) | Parser extrai `wa_id` e BSUID em **uma** função; BSUID-only vira desconhecido (AC-14) | `inbound.extract_sender` + fixtures | Outro nome/posição do campo: ajuste de uma função e das fixtures; se a Meta não enviar BSUID, a coluna fica nula sem efeito |
| **Q8** (modelo de boas-vindas) | Modelo com 1 parâmetro (nome) e 2 botões de resposta rápida; payloads `OPTIN_ACCEPT/DECLINE:<id>` | `whatsapp.build_welcome_template`; `WELCOME_TEMPLATE_*` | Parâmetro nomeado vs posicional, aviso como documento no cabeçalho, outro texto de botão: só o construtor muda |
| **Q9** (textos) | Textos propostos em 4.3.4 (inclui "equipe avisada") | `TEXT_*` no ambiente | Troca de valor, sem código |
| **Q20** (resposta a mídia — **resolvida** em 2026-10-23) | Caio confirmou o AC-36: responde "equipe avisada"; o PRD §10 de 2026-10-22 será corrigido | `MEDIA_ACK_ENABLED=true` (flag mantida) | Se o Caio mudar de ideia: desligar a flag (`reply_kind='none'`), ajustar 1 teste |
| **Q21** (tipos sem tratamento previsto; tamanho de mídia) | Reação/`system`/`unsupported`/botão de usuário ativo = `ignored_type` (rastro, sem resposta/ticket). O payload da Meta **não traz tamanho em bytes** de mídia; guarda-se tipo + `mime_type` (obter o tamanho exigiria chamada à Graph API por remetente desconhecido, contra "zero custo") | `access.decide`; colunas `first_msg_mime` | Se `unsupported` deve escalar: 1 linha na tabela de decisão |
| **Q22** (erro não transitório em 1 mensagem) | Log ERROR + rastro `error` + 200, sem alerta novo | `pipeline` (bloco de exceção) | Se quiser alerta: novo `kind` no mesmo `raise_alert` |
| **Q23** (e-mail: AC-37 "identificador mascarado" × painel PN-65 "sem telefone/nome") | E-mail leva "usuário #id" (id interno), nunca telefone/nome/texto | `deliveries.build_email` | Se quiser nome: 1 linha, mas contraria PN-65 |
| **Q18/Q19** (grupo; conteúdo e retentativa do e-mail; domínio Resend) | Grupo desligado; e-mail com 6 tentativas e backoff 1 min→3 h; remetente = conta do AUTO-1 | `TEAM_GROUP_*`, `EMAIL_*`, `RESEND_FROM_EMAIL` | Só configuração; se for remetente novo, verificar domínio no Resend |
| **Q4/Q15** (CSV, perfis, canais, marcas, gestor) | Perfis = os 5 do PRD §3; marcas = as 5 do PRD (`jungle, plant_power, possible, a_tal_da_castanha, zaya`); canais: lista inicial em `core/values.py`; gestor = **texto livre**; CSV como em 4.3.3 | `core/values.py`, `users.validate_row` | Gestor escolhido entre cadastrados: FK nova (migration aditiva); novos valores: lista em `values.py` + `CHECK` de `profile` (migration) |
| **Q6/Q7** | `valid_until` sempre nulo e ignorado; 60/h deslizante | `access.decide` não lê `valid_until` | Reativar validade: a decisão ganha um `OR` |
| **Q10** (64 KB) | 64 KB | `MAX_WEBHOOK_PAYLOAD_BYTES` | Configuração |
| **Q12** (conta Positive Co no mesmo app) | Webhook descarta tudo que não for do `WHATSAPP_PHONE_NUMBER_ID` (AC-6) e **não é inscrito em produção** enquanto a Positive Co estiver no app | filtro da etapa 4 do fluxo | — (é pré-requisito de go-live, 8.2) |
| **Q26** (quem expurga os 12 meses) | Nada é expurgado aqui; rotina fica com o painel (PN-58) | índices por `received_at`/`stored_at` | Se o jurídico exigir antes, tarefa extra de ~3 h |
| **Pausa × opt-in** (novo, R4) | A pausa do painel (PN-45) bloqueia respostas conversacionais; mensagens de consentimento (boas-vindas, lembrete, confirmação e recusa) **não** são pausadas | `FlagsReplyPolicy` (lista de isenções) | Se o Caio quiser pausar tudo: esvaziar a lista de isenções (1 linha) |
| Ordem AC-31 × AC-4 (**resolvida**) | AC-31 reescrito: "tamanho, assinatura" | `api/webhook.py` | — |

### 4.10 Deploy e operação

Os arquivos abaixo são **entregues versionados** por esta tarefa; **aplicá-los ao servidor é da fundação técnica/operação**, uma etapa de cada vez, **com confirmação do Caio antes de cada mudança** (servidor compartilhado com ~10 apps; nada destrutivo; nginx só `nginx -t` + `reload`, nunca restart).

- **Processo PM2 `jarvis`** (`deploy/ecosystem.config.js`, modelo do AUTO-1): `/opt/jarvis/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port <porta> --workers 1`, `cwd /opt/jarvis/backend`, `PYTHONPATH`, logs em `/var/log/jarvis/{out,error}.log`, `max_memory_restart: 300M`, `restart_delay: 3000`. **Porta proposta: 8010** (AUTO-1 usa 8001); **confirmar livre** com `ss -ltnp` antes. **1 worker** de propósito (4.8, `research.json`). O painel roda no mesmo processo (painel.1 4.1).
- **Subdomínio:** `jarvis.positiveco.com.br` → droplet `159.89.55.129`. **DNS ainda não apontado e dono não identificado (STATE.md)** — pendência externa; sem ele o webhook não pode ser inscrito na Meta. Plano B para o smoke test: o túnel temporário do piloto apontando para a porta local do serviço (decisão do Caio; não substitui o go-live).
- **Site nginx** (`deploy/nginx/jarvis.conf.template`): TLS (certificado pelo **mesmo mecanismo do site do AUTO-1**, a confirmar no servidor), `proxy_pass http://127.0.0.1:<porta>`, `proxy_set_header X-Real-IP $remote_addr` (o limitador HTTP depende disso), `client_max_body_size 64k`, `proxy_read_timeout 15s`, `location = /webhook/whatsapp` e `location = /health` apenas; `location /` devolve 404 (o painel acrescenta suas rotas depois).
- **Banco:** `CREATE DATABASE jarvis` + roles `jarvis_owner` e `jarvis_app`; migrations pelo runner; **confirmar com o Caio** e checar se a rotina de backup do droplet cobre o banco novo.
- **Diretórios:** `/opt/jarvis/{backend,venv,backup}`, `/var/log/jarvis` (rotação como a dos logs do AUTO-1, a confirmar). Deploy = `rsync` de `backend/app`, `backend/migrations` e `deploy` (sem `.env`, sem testes, sem docs), como no `/deploy` do AUTO-1; `pm2 restart jarvis --update-env`; `GET /health` 200 e log sem traceback.
- **Meta:** callback `https://jarvis.positiveco.com.br/webhook/whatsapp`, verify token = `WHATSAPP_VERIFY_TOKEN`, campo assinado **`messages`** apenas.

### 4.11 Decisões de design a pedido do painel.1 (R1–R4, 2026-10-23)

O `painel.1` (Fase 2, seção 4.15) pediu quatro ajustes a esta tarefa. Todos aceitos; cada um é uma decisão de design registrada aqui.

| # | Pedido | Decisão | Onde |
|---|---|---|---|
| **R1** | Tickets de mídia em `chat.tickets` (entidade única), lidos pelo painel por `ops.v_tickets`, em vez de `ops.escalation_tickets` | **Aceito.** `ops.escalation_tickets` deixa de existir. PF cria `chat.tickets` e `ops.v_tickets` na migration `005`; o AC-36 grava `kind='media'`, `classification='MIDIA'`, `escalated=true`, `status='awaiting'`, `urgency='urgente'`. "Tabela própria, separada de alertas" (decisão 6) continua verdadeira. Mapeamento: `aberto` = `awaiting` + escalado. A linha `ops.deliveries` de grupo usa `source_id = chat.tickets.id`. Alternativa rejeitada: duas tabelas + `UNION` (plano B do painel) — dobra a escrita do painel e o risco de divergência. | 4.2.3, 4.4.6 |
| **R2** | `users.validate_row` pura; `users.register` reutilizável pelo painel e pela importação | **Aceito.** `validate_row(row, now)` sem banco/rede/relógio global, devolve valor validado ou lista de erros por campo; `register(conn, data, actor, enqueue_welcome=True)` roda na transação de quem chama (lote CSV = uma transação). CLI "tudo ou nada" e painel "só as válidas" usam as mesmas duas funções, mudando só a política. | 4.3.2, 4.3.3 |
| **R3** | `raise_alert(..., email: bool)` | **Aceito**, padrão `email=True` (todos os chamadores de PF e o AC-37 não mudam). `email=False` não cria a linha de entrega `email`; a de grupo segue a chave. Serve à Q17 do painel (ações do próprio Caio sem e-mail). | 4.3.2, 4.4.6 |
| **R4** | `flags` aceita `paused` e `brand_enabled:<marca>`, invalida cache na escrita; `main.py` instala o `ReplyPolicy` | **Aceito.** PF é a dona de `services/flags.py` e de `services/reply_policy.py` (`FlagsReplyPolicy`), evitando dois donos do mesmo arquivo; o painel só chama `flags.set_bool`. `outbox.dispatch` consulta a política antes de enviar (cobre também respostas futuras do cérebro). Escolha de projeto: a pausa **não** bloqueia mensagens de consentimento (boas-vindas, lembrete, confirmação, recusa). Cache de 30 s com invalidação na escrita do mesmo processo; escrita por CLI em outro processo vale em ≤ 30 s. | 4.3.2, 4.4.7, 4.9 |

**Numeração de migrations:** PF usa `001–099` (hoje `001–005`); o painel reserva `101–107`. O runner ordena pelo prefixo numérico e recusa prefixo duplicado. A migration `102` do painel deixa de criar `chat.tickets` (vem de PF `005`) e só acrescenta o que é dele (`search_key`, índices por marca/status). O painel deve também retirar `reply_policy.py` e a extensão de `flags.py` da sua lista de arquivos (ficam em PF). Sizing: +~4 h (ver `complexity.json`).

## 5. Requisitos Não Funcionais (detalhe)

- **Desempenho.** Resposta 200 do webhook < 1 s (tipicamente < 150 ms: 1 transação com ~6 comandos indexados). Decisão de acesso < 50 ms (consulta por `phone_key`/`bsuid`, índices únicos). p95 do eco "em construção" ≤ 8 s (meta do PRD §6): commit → despacho imediato por `BackgroundTasks` → 1 chamada à Graph API (~0,3–0,8 s). Pool asyncpg 2–10 conexões. Capacidade: 20 mil msg/dia ≈ 0,25 msg/s média (pico 10×) ≈ 7,3 milhões de linhas de rastro/ano no teto; sem particionamento nesta fase (índice por `received_at` permite expurgo em lotes). Memória do processo < 300 MB (limite do PM2).
- **Segurança.** HMAC SHA-256 sobre bytes brutos, comparação de bytes em tempo constante, antes de qualquer parse ou banco; tamanho limitado e lido em stream; filtro por `phone_number_id`; só `GET/POST /webhook/whatsapp` e `/health` expostos; `TrustedHost`; headers (`nosniff`, HSTS, `Cache-Control: no-store`); docs/redoc desligados; respostas de erro sem detalhe nem traceback; rate limit HTTP por `X-Real-IP`; SSRF: allow-list nas URLs base (Graph, WAHA); segredos só em ambiente (`SecretStr`, mascarados em log); CLI exige acesso SSH (sem superfície HTTP administrativa); `jarvis_app` sem DDL e sem UPDATE/DELETE em `audit_log`. O teste de intrusão do critério de pronto do épico é a suíte `tests/security/` + roteiro manual.
- **Privacidade (LGPD).** Matriz em 4.2.7. Corpo só de autorizado; 1ª mensagem de desconhecido só com a chave ligada e em tabela separada; e-mails, alertas, auditoria, logs e contadores sem telefone, nome ou texto; versão do aviso gravada no aceite; mídia nunca baixada (o ticket guarda só o rótulo do tipo); dados de tratamento no droplet (Q13 pendente).
- **Confiabilidade.** Idempotência e limite garantidos pelo banco; efeitos externos após commit com recuperação na subida; resposta ao usuário at-most-once; e-mail/grupo com backoff e fim definido; falha de qualquer integração externa nunca bloqueia nem perde o registro da mensagem. Disponibilidade 99,5% depende do servidor único (risco aceito pelo ADR de 2026-10-20). Após queda, a Meta reentrega por até 7 dias: o backlog é processado em ordem de chegada pela idempotência (respostas a mensagens muito antigas podem falhar fora da janela de 24 h e viram `send_failed` — documentado no runbook).
- **Observabilidade.** Logs JSON com `request_id`; rastro por mensagem no banco; contadores de assinatura inválida, payload grande, token de verificação errado, JSON inválido e destinatário errado (`ops.security_counters`); decisões por tipo, `latency_ms` e estado das entregas consultáveis por SQL. `/health` verifica banco e migrations. Sem métricas externas nesta fase (alertas por e-mail cobrem os eventos anômalos).
- **Manutenibilidade.** Funções puras separadas do I/O; um único construtor de payload de modelo; mapa de códigos de erro da Meta em uma tabela substituível; migrations imutáveis; lista de valores (perfis, UFs, marcas, canais) em um módulo.
- **Acessibilidade.** Não se aplica (sem interface; mensagens de texto curto em PT-BR, até 700 caracteres).

## 6. Out of Scope

- Respostas com conteúdo (produto, preço, argumentos, arquivos): incremento 2 em diante.
- Alertas, e-mails ou avisos ao grupo para tentativas de acesso de desconhecido ou bloqueado (só registro para o painel).
- E-mail para tickets escalados (mídia ou qualquer escalonamento futuro).
- O painel web em si (telas "Pessoas autorizadas → Pedindo acesso", alertas, tickets, cadastro): incremento 5. Aqui só se gravam os dados que ele vai exibir (alertas, tickets escalados e tentativas de acesso) e se entregam os serviços que ele reutiliza (R1–R4).
- Fluxo de atendimento do ticket escalado (atribuir, responder, fechar, SLA, lembretes): história futura. O ticket só nasce `awaiting`.
- Expiração automática de acesso (180 dias), revalidação em lote e aviso de vencimento. `valid_until` existe nulo, sem bloqueio por data.
- Transcrição de áudio e interpretação de imagens (RF-02 fora do MVP). Mídia só gera ticket escalado e a resposta curta de equipe avisada; o arquivo nunca é baixado nem encaminhado.
- Ativar o envio ao grupo da equipe: o caminho WAHA é implementado e testado, mas a chave fica desligada.
- E-mail para outros destinatários, digest, agrupamento de avisos e templates elaborados.
- Encaminhar ao usuário a resposta da equipe no grupo (história futura) e dashboard interno de alertas (futuro).
- Painel de cadastro/revogação (RF-20 completo): incremento 5. Aqui só comando/script e CSV via SSH.
- Lista completa de usuários: o cadastro inicial é só o telefone do Caio.
- Uso do modelo de linguagem.
- Mensagens ativas além da boas-vindas. Entram apenas o predicado de elegibilidade e o "sair".
- Reconciliação mensal com a lista de vendedores ativos (3C).
- Filtro de conteúdo por perfil/UF/canal/marca (RF-18): campos guardados, ainda não usados.
- Provisionamento do droplet, DNS, nginx, banco, conta/domínio do provedor de e-mail e deploy (fundação técnica).
- Aprovação jurídica do aviso e aprovação do modelo na Meta (dependências externas).
- Exclusão de dados a pedido do usuário.

## 7. Test Plan

| Camada | O que testar | Como |
|---|---|---|
| Unit | Decisão de acesso (tabela status x aceite x opt-out), normalização de telefone/9º dígito, resolução telefone/BSUID, janela deslizante de 60 min, `can_send_proactive`, validação de campos obrigatórios do cadastro (`validate_row`), parsing de payloads (mensagem, status, múltiplas, mídia) | pytest com relógio injetável e fixtures reais de payload da Meta |
| Integration | Webhook completo (GET/POST, HMAC, 413, duplicata concorrente), silêncio e registro sem alerta para desconhecido e bloqueado, guarda só da 1ª mensagem de desconhecido, fluxo de opt-in, reativação de revogado, revogação durante conversa, limite de 60 msg/h com aviso único, ticket escalado imediato em `chat.tickets` (sem linha em alertas, sem e-mail, com resposta curta ao usuário), e-mail de alerta (envio, supressão, `email=False`, falha sem bloquear, independência da chave do grupo, zero e-mail para tentativa de acesso e para ticket), chave do grupo desligada/ligada/WAHA com falha, pausa e marca desligada, rastro por decisão, comandos administrativos (cadastro, CSV, troca, revogação, reenvio/reativação) | pytest + FastAPI TestClient + Postgres de teste; clientes WhatsApp, WAHA, e-mail, modelo e mídia mockados com spies |
| Segurança / intrusão | Zero conteúdo e zero resposta a número fora da lista, ordem das verificações, assinatura inválida, replay, ausência de e-mail/endereço em logs e specs | Suíte automatizada sobre AC-2, AC-10, AC-31, AC-37 e roteiro manual de intrusão (critério de pronto do épico) |
| Manual | Fluxo real no número de teste da Meta (+1 555 670 8419) com o telefone do Caio: cadastro, boas-vindas, Aceito, Não aceito, texto no lugar do botão, revogação e reativação, "sair", envio de áudio (ticket criado, sem e-mail, resposta "equipe avisada"), estouro do limite (alerta e e-mail chegando), mensagem de número não cadastrado (silêncio, aparece só no registro, sem e-mail) | Roteiro de smoke test; depende de Q8, Q12 e Q19 |

Meta de qualidade: todo AC com ao menos um teste automatizado, exceto o envio
real do modelo e do e-mail (manuais). O mapa AC → arquivo de teste está em 4.7.

## 8. Plano de Implementação

Insumo para a Fase 3 (`task.md`): **marcos em ordem de dependência**, não tarefas
(a quebra em tarefas de 1–4 h com lista de arquivos é a Fase 3). Cada marco deixa
a suíte verde e é um commit lógico `type(porta-fechada): PORTA-FECHADA-1.x - …`.

### 8.1 Marcos

| # | Marco | Entrega | ACs | Horas |
|---|---|---|---|---|
| M0 | Esqueleto | `backend/` (config, log JSON, redaction, relógio, erros, pool, `main` + `/health`), `requirements*.txt`, ruff/mypy/pytest, CI, `configuracao-modelo.txt` | base de AC-31/32 | 6–8 |
| M1 | Banco | migrations 001–005 (inclui `chat.tickets` e `ops.v_tickets`), runner, fixtures de banco de teste, testes das restrições (CHECKs, UNIQUEs, trigger de auditoria) | AC-7 (base), 18 (CHECK) | 9–11 |
| M2 | Núcleo puro ∥ M1 | `phone`, `inbound` (+ fixtures capturadas do número de teste), `access.decide`, `replies`, `core/values` | AC-5, 6, 9, 14, 19, 21, 28 (predicado) | 10–12 |
| M3 | Webhook e rastro | GET/POST, tamanho/HMAC/JSON/`phone_number_id`, idempotência, `identity`, `attempts`, `trace`, silêncio de desconhecido/bloqueado, contadores de segurança | AC-1–4, 7, 8, 10–12, 22, 27, 31, 32 | 14–16 |
| M4 | Saída e opt-in | `channel`/`whatsapp` (texto, modelo, botões), `outbox` (claim, rechecagem, falhas, gancho `ReplyPolicy`), `optin`, boas-vindas, aceite/recusa/lembrete, texto "em construção" via `AnswerProvider` | AC-17–21, 23, 30, 33 (rastro) | 14–16 |
| M5 | Limite, mídia, alertas, política | `limits`, "sair", `tickets` (`chat.tickets`), `alerts` (`email`), `deliveries` (e-mail + grupo), `flags` (`paused`, `brand_enabled:*`, cache), `reply_policy`, `notifications`, `waha`, alertas de falha repetida | AC-13, 28, 29, 33, 36, 37, R3, R4 | 16–20 |
| M6 | Administração | `users` (`validate_row`, `register`), `audit`, `cli` (add, import, revoke, resend, swap-number, list, flag) | AC-15, 16, 26, 34, 35, R2 | 9–11 |
| M7 | Segurança e fechamento | suíte `tests/security/` (intrusão, ordem, logs sem PII, replay), lacunas de AC, revisão de mapa AC → teste | AC-2, 10, 31, 32, 37 | 6–8 |
| M8 | Deploy | `deploy/` (PM2, nginx, runbook), `.claude/CLAUDE.md` com comandos de verificação, `docs/STATE.md` | — | 4–6 |
| M9 | Go-live e smoke | checklist 8.2 e roteiro manual de 7 | manuais | 4–6 |

Ordem: M0 → (M1 ∥ M2) → M3 → M4 → (M5 ∥ M6) → M7 → M8 → M9. Escopos de escrita
disjuntos para ondas paralelas: puros (M2) × banco (M1); alertas/entregas × limite/tickets
× flags/reply_policy dentro de M5 (arquivos distintos); M6 só depende de `outbox` (M4). Gargalos de arquivo
compartilhado (`core/values.py`, `main.py`, `pipeline.py`) ficam com uma tarefa dona por vez.
Para o painel, o que destrava a implementação dele é M1 (migrations 001–005), `users`/`flags`/`reply_policy`/`alerts` (M5–M6).

### 8.2 Checklist de go-live (cada passo no servidor exige confirmação do Caio)

1. Pré-requisitos externos: DNS de `jarvis.positiveco.com.br` e certificado; criar o banco `jarvis` e as roles; **Q12 resolvido** (conta Positive Co fora do app, ou ao menos webhook ainda não inscrito); modelo de boas-vindas aprovado e pagamento no WhatsApp Manager (Q8).
2. `/opt/jarvis`, venv, `.env` (modo 600, preenchido pelo Caio), `pm2 start deploy/ecosystem.config.js`.
3. `python -m app.db.migrate` (com `--status` antes); `GET /health` 200.
4. Site nginx: `nginx -t` e `nginx -s reload`; conferir `X-Real-IP`.
5. Meta: callback + verify token (AC-1 pelo console), campo `messages`.
6. `user add` com o telefone do Caio; boas-vindas; Aceito; texto → "em construção"; áudio → ticket (sem e-mail) e resposta "equipe avisada"; estouro do limite → alerta + e-mail; número não cadastrado → silêncio e linha só no registro; `flag set paused on` → sem resposta, `off` → volta; revogação e reativação.
7. Chaves: `UNKNOWN_FIRST_TEXT_STORE` e `TEAM_GROUP_ENABLED` continuam **desligadas** até Q17/Q18.

## 9. Plano de Reversão

- **Código:** `rsync` da versão anterior (cópia em `/opt/jarvis/backup/app-<data>.tgz` feita antes de cada deploy) + `pm2 restart jarvis`. O processo é novo e isolado: não toca o AUTO-1 nem outros apps.
- **Banco:** migrations só para frente. Primeira implantação: reversão = `DROP DATABASE jarvis` (nada depende dele). Depois: migration compensatória nova ou restauração do `pg_dump` tirado antes de cada migration em produção. Colunas novas são sempre aditivas, então código antigo continua funcionando sobre o schema novo. Como o painel (101–107) depende de `chat.tickets` e `ops.v_tickets`, reverter PF `005` exige reverter antes o que do painel as usa.
- **Chaves de desligamento sem deploy:** `TEAM_GROUP_ENABLED`, `MEDIA_ACK_ENABLED`, `UNKNOWN_FIRST_TEXT_STORE` (ambiente + reinício, ou `flag set` para as do `system_state`); `flag set paused on` interrompe as respostas conversacionais sem derrubar o recebimento; e-mail some ao esvaziar `ALERT_RECIPIENT_EMAIL`.
- **Parada de emergência:** desinscrever o callback do app na Meta (ou `pm2 stop jarvis`); mensagens de autorizados ficam sem resposta e a Meta reentrega por até 7 dias — ao voltar, o backlog é processado (respostas muito antigas podem falhar fora da janela de 24 h). Preferir `flag set paused on`, que evita a reentrega.
- **Dados:** usuários e avisos já enviados não se desfazem; para cancelar um cadastro, `user revoke`. Boas-vindas enviada não pode ser recolhida.
- **Reversão do desenho:** o piloto Node (`piloto/`) continua funcional como fallback de demonstração até a aprovação da Fase 4.

## 10. Referências

- `docs/prd.md` (§3, §4, §7, §10 — a entrada de 2026-10-22 sobre áudio e foto precisa ser corrigida, ver decisão 10), `docs/architecture/overview.md` (ADRs de 2026-10-19, 2026-10-20 e os dois de 2026-10-23), `docs/STATE.md`
- `docs/epics/porta-fechada.md`, `docs/stories/porta-fechada.1.md` (sincronizada com este spec em 2026-10-23)
- `docs/tasks/porta-fechada.1/spec/{requirements.json,research.json,complexity.json}`
- `docs/tasks/painel.1/spec/{spec.md,requirements.json}` (consumidor do modelo de dados, 4.2.8; pedidos R1–R4 em 4.15 do painel, decisões em 4.11 daqui)
- `docs/tasks/piloto-tecnico.1/spec/spec.md`, `piloto/server.js`
- `docs/legal/aviso-privacidade.md` (modelo de boas-vindas, texto de recusa, itens do jurídico)
- AUTO-1 (`C:\Users\User\Documents\GitHub\agente-instagram-direct-n8n`): `backend/app/{main,config}.py`, `api/webhook.py`, `services/{rate_limit,notifications,waha}.py`, `core/{security,log_redaction,errors,retry}.py`, `migrations/001_schema.sql` e `004_monitoring.sql`, `deploy/ecosystem.config.js`, `.claude/commands/deploy.md`, `docs/{technical-flow,architecture/overview}.md`, `docs/specs/{banco-proprio-auto1,alertas-email-e-bot-parado}.md`

## Questões em aberto

Resolvidas ou substituídas: Q1, Q2, Q2' (substituída pela decisão 8), Q3, Q6,
Q7, Q11, Q14, Q15, Q16, **Q20** (decisão 10: o Caio confirmou a resposta curta
a mídia, AC-36 como aprovado) e os campos obrigatórios do cadastro. Detalhe em
`requirements.json` (`open_questions`, que já inclui Q21 a Q26 da Fase 2).
Nenhuma bloqueia a Fase 3; as premissas estão em 4.9.

1. **Q17 (jurídico)** Base legal LGPD e retenção do texto da 1ª mensagem de número desconhecido. Sem resposta, AC-10 fica com esse armazenamento condicionado (desenho: chave desligada em produção).
2. **Q18 (baixa prioridade)** Quando e quem liga a chave do grupo; qual grupo e sessão WAHA; o que o aviso ao grupo pode conter.
3. **Q19 (baixa prioridade)** Conteúdo mínimo e retentativas do e-mail de alerta; reutilizar a conta/domínio Resend do AUTO-1 ou remetente próprio.
4. **Q5** A Meta já envia o BSUID para o nosso app? Como cadastrar quem só tem nome de usuário?
5. **Q8** Modelo de boas-vindas: nome, categoria (custo), aprovação, forma de pagamento pendente, aviso como PDF ou link (jurídico).
6. **Q9** Textos fixos a aprovar: "em construção", confirmação de aceite, confirmação de "sair", lembrete de aceite, resposta "equipe avisada".
7. **Q10** Confirmar limite de payload de 64 KB.
8. **Q12** Webhook só na conta de teste/dedicada; resolver o acesso indevido à Positive Co.
9. **Q13** LGPD: dados (inclusive corpo das mensagens e avisos de alerta por e-mail) fora do Brasil aceitáveis? (pendência do ADR de 2026-10-20)
10. **Q4 (baixa prioridade)** Layout do CSV e valores de perfil.
11. **Q21 (nova)** Tipos sem tratamento no AC: reação, `system`, `unsupported`, botão/interativo de usuário ativo. Padrão: só rastro (`ignored_type`). E o "tamanho" da mídia da 1ª mensagem de desconhecido (AC-10): o payload não traz bytes; padrão: tipo + `mime_type`.
12. **Q22 (nova)** Erro inesperado (bug) ao processar uma mensagem: padrão é log + rastro `error` + 200, sem alerta. Quer alerta?
13. **Q23 (nova)** E-mail: "identificador mascarado" (AC-37) × "sem telefone nem nome" (painel PN-65). Padrão: "usuário #id".
14. **Q24 (nova)** Porta (8010 proposta), diretório (`/opt/jarvis`), subdomínio e quem gerencia o DNS; mecanismo do certificado.
15. **Q25 (nova)** Ambiente de desenvolvimento: onde roda Python 3.12 + Postgres para os testes (máquina local sem Python, como no piloto, ou só CI)? Existe remoto Git/CI?
16. **Q26 (nova)** Quem constrói a rotina de expurgo dos 12 meses (padrão: painel, PN-58) e a exclusão a pedido.
