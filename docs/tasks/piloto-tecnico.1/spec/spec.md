---
task_id: PILOTO-TECNICO-1
status: approved
approved_by: Caio
approved_at: 2026-10-22
owner: Caio
created: 2026-10-22
---

# Spec — Piloto técnico: o Jarvis responde no WhatsApp de teste

## 1. Objetivo

Provar a ligação ponta a ponta antes do código definitivo: uma mensagem enviada
do celular do Caio para o número de teste da Meta (+1 555 670 8419) chega a um
programa no computador do Caio, que responde com uma resposta da FAQ.

É **descartável**: roda localmente, em Node.js (o computador não tem Python),
exposto por um túnel temporário (`cloudflared`). A versão de produção segue o
ADR de 2026-10-20 (Python/FastAPI no droplet) e as specs porta-fechada.1 e
painel.1.

## 2. Escopo

**Dentro**
- Webhook da WhatsApp Cloud API: verificação (GET, `hub.verify_token`) e recebimento (POST).
- Validação da assinatura `X-Hub-Signature-256` com a chave secreta do app.
- Lista positiva mínima: só o número do Caio; qualquer outro número é ignorado em silêncio.
- Resposta **sem IA**: busca a pergunta da FAQ mais parecida com a mensagem (palavras em comum, sem acento, sem palavras vazias) e responde com a resposta cadastrada, mais a fonte quando houver.
- Fontes da FAQ: Guia de produtos (112 perguntas), perguntas Nielsen (56) e GMR (55) geradas pela tela Dados.
- Se a semelhança for baixa: "Ainda não sei responder isso. Anotei a pergunta para a equipe." (sem escalar de verdade).
- "oi", "menu" ou "ajuda": mensagem curta dizendo o que o Jarvis sabe responder.
- Mensagens que não são texto (áudio, foto, vídeo, documento, figurinha, localização): resposta curta "equipe avisada" e registro no log (decisão do Caio, 2026-10-23, revertendo a de 2026-10-22).
- Registro local de cada mensagem (entrada, pergunta encontrada, nota de semelhança, resposta, tempo) num arquivo de log fora do git.
- Assinatura do agente da marca citada na pergunta (Duda, Léo, Nico, Rita, Tati) ou Jarvis.

**Fora**
- IA (Gemini) — entra quando houver chave.
- Aceite do aviso de privacidade, revogação, limite de 60 msg/h, painel, banco de dados, e-mail.
- Qualquer número além do do Caio.

## 3. Critérios de aceite

- **PT-1** QUANDO a Meta chamar `GET /webhook` com o `verify_token` correto, ENTÃO o programa devolve o `hub.challenge`; com token errado, devolve 403.
- **PT-2** QUANDO chegar um `POST /webhook` sem assinatura válida, ENTÃO é descartado e nada é respondido.
- **PT-3** QUANDO o Caio mandar uma pergunta que existe na FAQ (ex.: "jungle tem glúten?"), ENTÃO recebe a resposta cadastrada em até 5 segundos.
- **PT-4** QUANDO a pergunta não tiver correspondência suficiente, ENTÃO recebe "Ainda não sei responder isso…" e a pergunta fica no log.
- **PT-5** QUANDO outro número mandar mensagem, ENTÃO nada é respondido e a tentativa fica no log.
- **PT-6** QUANDO a mesma mensagem for entregue duas vezes pela Meta, ENTÃO só uma resposta é enviada.
- **PT-7** Nenhuma chave (token, chave secreta) aparece no código, no log ou no git: ficam num arquivo `.env` local, ignorado pelo git, preenchido pelo Caio.
- **PT-8** QUANDO chegar áudio, foto ou outra mídia, ENTÃO o vendedor recebe "Recebi seu arquivo… Avisei a equipe…" e o tipo fica no log.

## Segurança (pedido do Caio)

- **S-1** Assinatura `X-Hub-Signature-256` conferida com HMAC-SHA256 da chave secreta do app, em comparação de tempo constante, sobre o corpo bruto; sem assinatura válida → 401, sem processar.
- **S-2** Defesa em profundidade: o evento só é aceito se `object = whatsapp_business_account` e o `phone_number_id` for o do número de teste configurado.
- **S-3** Lista positiva verificada **antes** de qualquer processamento; número de fora não recebe nada.
- **S-4** Corpo limitado a 64 KB (413 acima disso); só `GET /webhook` e `POST /webhook` existem; o resto → 404.
- **S-5** Limite de 60 mensagens por hora por número; acima disso, silêncio e registro.
- **S-6** Idempotência por id da mensagem (não responde duas vezes) e eventos com mais de 10 minutos são ignorados (reenvio antigo).
- **S-7** O servidor só escuta em `127.0.0.1` (o túnel é a única porta de entrada).
- **S-8** O log nunca grava chaves; o telefone aparece mascarado (só os 4 últimos dígitos) e o texto é guardado só para o número autorizado.
- **S-9** Falha ao enviar resposta é registrada sem derrubar o servidor; erros não expõem detalhes para quem chamou.
- **S-10** Sem dependências externas (só Node.js), para não trazer código de terceiros.

## 6. Fora de escopo (lembrete)

Tudo o que está nas specs porta-fechada.1 e painel.1 e não foi listado acima.

## 7. Plano de teste

Manual, pelo celular do Caio: PT-1 ao cadastrar o webhook na Meta; PT-3 com 5
perguntas do Guia, 2 da Nielsen e 2 da GMR; PT-4 com "qual o preço do Proteico
em PE?"; PT-6 reenviando o evento pelo botão de teste do painel da Meta. PT-2 e
PT-5 por chamada local com assinatura errada e número falso.

## Arquivos

- `piloto/` (novo, descartável): servidor Node.js, carga da FAQ, busca.
- `piloto/configuracao-modelo.txt` (modelo sem valores; arquivos `.env*` são bloqueados para o assistente) — o `.env` real é criado pelo Caio.
- `.gitignore`: acrescenta `piloto/.env` e `piloto/logs/`.
- Nenhuma dependência externa além do Node.js já instalado.
