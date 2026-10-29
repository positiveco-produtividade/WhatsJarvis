# História porta-fechada.1 — Receber mensagens e aplicar a lista positiva

> **Épico:** [porta-fechada](../epics/porta-fechada.md) · **Status:** Draft (requisitos aprovados em 2026-10-23; design em revisão) · **Owner:** Caio · **Criado:** 2026-10-20 · **Sincronizada com o spec em:** 2026-10-23

Fonte de verdade: `docs/tasks/porta-fechada.1/spec/spec.md` e `requirements.json`.
Em caso de divergência, vale o spec.

## Como / quero / para

**Como** responsável pelo Jarvis,
**quero** que ele receba as mensagens do WhatsApp e só deixe passar quem está
cadastrado, ativo e com aceite registrado,
**para** que preço e material comercial nunca cheguem a quem não deveria e
números desconhecidos não gerem custo.

## Escopo

- Endpoint de webhook da WhatsApp Cloud API: verificação (GET) e recebimento (POST) com limite de 64 KB e validação HMAC SHA-256 (`X-Hub-Signature-256`, App Secret).
- Identificação do remetente por telefone **e** BSUID (nomes de usuário do WhatsApp — ver `docs/STATE.md`), com tolerância ao 9º dígito brasileiro.
- Lista positiva: usuário com nome, telefone, perfil, empresa, UFs, canais, marcas, gestor responsável (todos obrigatórios), status e data do aceite. Sem validade: não há expiração automática de acesso (`valid_until` fica nulo e é ignorado).
- Cadastro, importação CSV, reenvio/reativação e revogação **sem painel** (comando/script administrativo via SSH); painel vem no incremento 5.
- Opt-in: ao cadastrar, envio do modelo de boas-vindas com botões Aceito / Não aceito; acesso liberado só após "Aceito", com data e hora. Revogado ou recusado só volta pela ação do administrador, que o leva a `pending_optin` (novo aceite).
- Número fora da lista (desconhecido, revogado ou recusado): **silêncio total**, sem resposta. A tentativa é registrada (contador e instantes) para a tela "Pedindo acesso" do painel; a primeira mensagem de número desconhecido é guardada, sujeita à validação jurídica (LGPD). **Não gera alerta, e-mail nem aviso ao grupo**, em nenhuma tentativa.
- Revogação efetiva em ≤ 1 minuto, inclusive com conversa em andamento.
- Limite de 60 mensagens/hora por usuário (janela deslizante), com um aviso ao usuário e um alerta.
- Rastro por mensagem (entrada, decisão de acesso, resposta, latência).
- Para usuário autorizado, neste incremento, o Jarvis responde a texto com uma mensagem fixa de "em construção" (eco controlado) — conteúdo real vem no incremento 2.
- Mídia (áudio, imagem etc.) de usuário autorizado: não é baixada nem interpretada; cria um ticket escalado em tabela própria (sem alerta e sem e-mail) e o usuário recebe uma resposta curta de que a equipe foi avisada (confirmado pelo Caio em 2026-10-23).
- **Alertas** (somente limite por hora, conflito de BSUID, falha de boas-vindas e falha repetida de envio): registro imediato no banco e **e-mail imediato** ao Caio (Resend, padrão AUTO-1). Tickets escalados e tentativas de acesso não enviam e-mail.
- Aviso ao grupo da equipe via WAHA: caminho implementado e testado, mas **desligado por chave** (padrão desligado).

## Contexto técnico

Padrão do AUTO-1 (`../agente-instagram-direct-n8n`): Python 3.12 + FastAPI,
Postgres (banco `jarvis`), PM2 + nginx, validação HMAC, idempotência por
`message_id`, rate limit, alertas, e-mail via Resend e WAHA para avisos internos
(desligado por chave). Desenvolvimento no número de teste da Meta
(+1 555 670 8419).

## Critérios de aceite (alto nível)

1. Requisição sem assinatura válida é descartada e não gera processamento.
2. Número fora da lista não recebe nenhuma resposta; a tentativa só é registrada, sem alerta.
3. Usuário cadastrado sem aceite não recebe nada além do pedido de aceite (lembrete no máximo 1 vez/24 h).
4. Usuário revogado ou recusado é bloqueado em ≤ 1 minuto.
5. 61ª mensagem na mesma hora é bloqueada, com um aviso e um alerta (com e-mail).
6. Toda mensagem gera um registro de rastro.
7. Mídia de usuário autorizado gera ticket escalado (sem e-mail) e uma resposta curta de equipe avisada.
