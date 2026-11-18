# Go-live do Jarvis — checklist (canal-waha.1 9.10 + retorno-humano.1 + porta-fechada.2)

> Escrito em 2026-11-18 (WA-T24). Registro da execução: WA-T25, preenchido no dia do go-live.
> Os comandos estão em [`deploy/RUNBOOK.md`](../../../deploy/RUNBOOK.md); aqui fica a ordem, o que
> conferir e o que depende do Caio. **No `farol`, cada passo só com confirmação do Caio**
> (servidor compartilhado: nada de `apt upgrade`, `nginx restart`, `docker system prune`).

## 0. Antes de começar (bloqueios em 2026-11-18)

| Item | Situação | Dono |
|---|---|---|
| Acesso SSH ao `farol` a partir do PC do Caio (`ssh farol "echo ok"`) | **falta** (sem `~/.ssh/config` neste PC) | Caio |
| Registro DNS `jarvis` (A) → `159.89.55.129` | **falta** (quem administra o DNS ainda a confirmar) | Caio |
| Chip do Jarvis (+55 85 98208-0355) com o celular à mão | ok | Caio |
| Aprovação jurídica do aviso de privacidade (D6) | **falta**; só bloqueia mandar o link aos vendedores | Caio / jurídico |
| `GEMINI_API_KEY` para o gate `eval --mode ia` | com o Caio | Caio |

## 1. Servidor (RUNBOOK passos 1 a 6)

- [ ] Conferir sem mudar nada: `free -h`, `ss -ltnp` (portas **8010** e **3001** livres), `docker ps`, `pm2 ls`, como o WAHA do AUTO-1 está instalado. Engine (D4): **GOWS** se sobrar menos de ~1 GB livre; com mais, GOWS ou WEBJS.
- [ ] Banco (porta-fechada.2): papéis `jarvis_owner` (dono) e `jarvis_app` (app, só dados); `CREATE DATABASE jarvis OWNER jarvis_owner`; `jarvis_assistant` só se o assistente do painel for ligado. Conferir se o backup do droplet cobre o banco novo.
- [ ] Código em `/opt/jarvis` (tar pelo SSH), venv com Python 3.12.
- [ ] `.env` do servidor (RUNBOOK passo 5): `DATABASE_URL` com `jarvis_app`, `MIGRATION_DATABASE_URL` com `jarvis_owner`, `PRODUCTION=true`, `PANEL_*`, `JARVIS_WAHA_*`. Sem `TEST_DATABASE_URL` nem `DEV_AUTH_EMAIL`.
- [ ] Migrations pelo runner com `MIGRATION_DATABASE_URL` (aplica até a 402 e dá as permissões do `jarvis_app` e do `jarvis_assistant` sozinho). `migrate --status` sem pendentes.
- [ ] Dados: levar o banco do PC (`pg_dump`/`pg_restore --no-owner --no-acl`, depois `migrate`) ou começar limpo e carregar a FAQ (RUNBOOK passo 6).
- [ ] PM2 `jarvis` na porta 8010. Se o app recusar subir com "papel do banco ... nao pode ser usado pelo app em producao", o `DATABASE_URL` está com o papel dono: trocar para `jarvis_app`.

## 2. WAHA do Jarvis (RUNBOOK passos 7 e 8)

- [ ] Container `jarvis-waha` em `127.0.0.1:3001` (nunca exposto), chave de API própria, sessões em `/opt/jarvis/waha-sessions`. Anotar a versão da imagem.
- [ ] Sessão: o WAHA Core só aceita a sessão **`default`** (a spec 9.10 dizia `jarvis`; vale `default`, como no RUNBOOK). Parear o chip pelo QR code (túnel SSH até o dashboard).
- [ ] Webhook `https://jarvis.positiveco.com.br/webhook/waha`, HMAC = `JARVIS_WAHA_HMAC_KEY`, eventos só `message`, `message.reaction`, `message.ack`, `poll.vote`, `session.status`.
- [ ] Perfil do número: foto, nome "Jarvis" e recado.
- [ ] `/health` com `"channel":"working"`.

## 3. nginx e HTTPS (RUNBOOK passo 9)

- [ ] Site a partir de `deploy/nginx/jarvis.conf.template`, `nginx -t` e `reload` (nunca restart). Certificado pelo mesmo mecanismo do site do AUTO-1.

## 4. Conferências com o número real

- [ ] Capturar um payload real de cada evento (`message`, `message.reaction`, `message.ack`, `poll.vote`, `session.status`) e comparar com `backend/tests/fixtures/waha/` (premissas R1, R2, R3, R5 de `research.json`). Se diferir: trocar a fixture e rodar a suíte (WA-T25).
- [ ] `eval --mode ia` (com a chave) ≥ 98% factual e 100% preço.
- [ ] Teste no celular: link, aviso de privacidade, ACEITO, enquete de marca, pergunta do Guia, troca de marca, mídia, "sair".
- [ ] Painel em `https://jarvis.positiveco.com.br/dashboard` pelo login da Positive.
- [ ] `pm2 logs jarvis --lines 50` sem traceback.

## 5. Grupo da equipe (retorno-humano.1)

- [ ] Criar o grupo com o número do Jarvis e a equipe dentro; pegar o id (`...@g.us`) pela API do WAHA.
- [ ] `.env`: `TEAM_GROUP_CHAT_ID=<id>@g.us` e `TEAM_GROUP_ENABLED=true`; `pm2 restart jarvis --update-env`.
- [ ] Teste ponta a ponta: pergunta que escala → aviso no grupo com o link → link abre o Inbox na pergunta (no celular, pelo IAM) → resposta pela caixa → chega no WhatsApp do vendedor → situação "entregue/lida" no painel → sugestão em Relatórios.
- [ ] Layout de celular do Inbox conferido em aparelho real.

## 6. Liberar aos vendedores

- [ ] Aprovação jurídica do aviso (D6).
- [ ] Mandar o link aos vendedores cadastrados.
