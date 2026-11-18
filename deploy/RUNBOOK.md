# Runbook — colocar o Jarvis no ar

Roteiro para subir o Jarvis num servidor Linux, do zero, e para atualizar depois.
Vale para um servidor novo (pessoal ou da empresa) e para o droplet `farol`. Base:
spec `porta-fechada.1` 4.10 e `canal-waha.1` 9.10.

> **No `farol` (servidor compartilhado com outras apps em produção):** confirmar com o
> Caio **antes de cada passo**. Nada de `apt upgrade`, `nginx restart`, `docker system prune`
> ou mexer em processos que não sejam do Jarvis. nginx só com `nginx -t` + `reload`.

Nos comandos, `SERVIDOR` é o alias SSH do servidor (ex.: `farol`) e `JARVIS_HOST` é o domínio
(ex.: `jarvis.positiveco.com.br`).

---

## 0. O que é preciso antes

| Item | Por quê |
|---|---|
| Servidor Ubuntu 24.04, 2 GB de RAM ou mais | Python 3.12 e PostgreSQL 16 vêm prontos nessa versão (as mesmas da produção) |
| Acesso SSH com chave, configurado como alias em `~/.ssh/config` | Todos os comandos usam `ssh SERVIDOR` |
| **Registro DNS** `jarvis` (tipo A) → IP do servidor, no painel da GoDaddy/Media Temple | Sem ele não há HTTPS. **Tem que ser `*.positiveco.com.br`:** o app só aceita esses domínios em produção e o login do painel usa o cookie do IAM da Positive |
| Chip do Jarvis (+55 85 98208-0355) com o celular à mão | Parear a sessão do WAHA pelo QR code |
| O `backend/.env` que funciona no PC | Base do `.env` do servidor (passo 5) |

## 1. Pacotes do sistema (servidor novo)

```bash
ssh SERVIDOR
sudo apt update
sudo apt install -y python3.12 python3.12-venv postgresql-16 nginx certbot python3-certbot-nginx docker.io
# Node + PM2 (o mesmo gerenciador de processos do AUTO-1)
sudo apt install -y nodejs npm && sudo npm install -g pm2
sudo mkdir -p /opt/jarvis /var/log/jarvis
sudo chown -R "$USER" /opt/jarvis /var/log/jarvis
```

No `farol`, a maior parte já existe: conferir com `python3.12 --version`, `psql --version`,
`pm2 ls`, `docker ps`, e ver memória livre (`free -h`) e portas em uso (`ss -ltnp`):
**8010** (Jarvis) e **3001** (WAHA do Jarvis) precisam estar livres.

## 2. Banco de dados

```bash
sudo -u postgres psql
```
```sql
-- Dono: cria e altera tabelas; so o runner de migrations usa (MIGRATION_DATABASE_URL).
CREATE ROLE jarvis_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password jarvis_owner
-- App: so le e grava dados (DATABASE_URL). As permissoes vem da migration 402 e o runner as
-- reaplica a cada `migrate`; nenhum GRANT manual.
CREATE ROLE jarvis_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password jarvis_app
CREATE DATABASE jarvis OWNER jarvis_owner;
REVOKE ALL ON DATABASE jarvis FROM PUBLIC;
-- Só se for ligar o assistente do painel (GEMINI_API_KEY):
CREATE ROLE jarvis_assistant LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password jarvis_assistant
\q
```

As senhas ficam só no `.env` do servidor. O app roda como `jarvis_app` (sem DDL, sem alterar
a auditoria) e **recusa subir em produção** com o papel dono ou um superusuário
(porta-fechada.2). No PC o app continua com o papel dono (`jarvis`).

## 3. Enviar o código (a partir do PC, na raiz do repositório, pelo Git Bash)

O servidor não usa git: recebe só o código de execução. Sem `.env`, testes ou docs.

```bash
tar -czf - \
  --exclude='__pycache__' --exclude='*.pyc' --exclude='.env*' \
  backend/app backend/migrations backend/requirements.txt \
  frontend/templates frontend/static deploy \
  | ssh SERVIDOR "tar -xzf - -C /opt/jarvis"
```

O `tar` sobrescreve, mas não apaga arquivos removidos do repositório. Se algo foi
apagado ou renomeado, remova à mão no servidor.

## 4. Ambiente Python (primeira vez e quando mudar `requirements.txt`)

```bash
ssh SERVIDOR
cd /opt/jarvis
python3.12 -m venv venv
venv/bin/pip install -r backend/requirements.txt
```

## 5. Configuração (`/opt/jarvis/backend/.env`)

Copiar o `.env` do PC sem passar por chat ou e-mail:

```bash
scp backend/.env SERVIDOR:/opt/jarvis/backend/.env
ssh SERVIDOR "chmod 600 /opt/jarvis/backend/.env"
```

Depois, no servidor, ajustar (`nano /opt/jarvis/backend/.env`):

| Variável | Valor no servidor |
|---|---|
| `PRODUCTION` | `true` |
| `DATABASE_URL` | `postgresql://jarvis_app:<senha do jarvis_app, passo 2>@localhost:5432/jarvis` |
| `MIGRATION_DATABASE_URL` | `postgresql://jarvis_owner:<senha do jarvis_owner, passo 2>@localhost:5432/jarvis` (só o runner usa) |
| `TEST_DATABASE_URL` | apagar a linha (nunca rodar testes no servidor) |
| `DEV_AUTH_EMAIL` | apagar a linha (em produção a subida é recusada se estiver preenchida) |
| `PANEL_PUBLIC_HOST` | `JARVIS_HOST` |
| `PANEL_BASE_URL` | `https://JARVIS_HOST` |
| `PANEL_ADMIN_EMAILS` | e-mails de quem administra o painel (obrigatório em produção) |
| `PANEL_CSRF_SECRET` | segredo novo: `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `JARVIS_WAHA_BASE_URL` | `http://127.0.0.1:3001` |
| `JARVIS_WAHA_API_KEY` | chave nova, a mesma do passo 7 |
| `JARVIS_WAHA_HMAC_KEY` | segredo novo, o mesmo do webhook no passo 8 |
| `JARVIS_WAHA_SESSION` | `default` (o WAHA Core só aceita a sessão `default`) |
| `ASSISTANT_DATABASE_URL` | se usar o assistente: papel `jarvis_assistant` no banco `jarvis` |

O resto (Gemini, Resend, IAM, textos, limites) pode ficar como no PC.

## 6. Migrations e primeira subida

```bash
ssh SERVIDOR
cd /opt/jarvis/backend
export MIGRATION_DATABASE_URL="$(grep -E '^MIGRATION_DATABASE_URL=' .env | cut -d= -f2-)"
../venv/bin/python -m app.db.migrate --status   # lista pendentes
../venv/bin/python -m app.db.migrate            # aplica e reaplica as permissoes do jarvis_app
                                                # e do jarvis_assistant (se o papel existir)
unset MIGRATION_DATABASE_URL

pm2 start /opt/jarvis/deploy/ecosystem.config.js
pm2 save
pm2 startup        # mostra um comando sudo; rodar para o PM2 voltar sozinho após reiniciar o servidor
curl -s http://127.0.0.1:8010/health        # {"status":"ok","channel":"down"} até o WAHA ligar
```

**Dados:** o banco nasce vazio. Dois caminhos:

- **Levar o banco do PC inteiro** (pessoas, FAQ já revisada, histórico de teste): seção 11. É o mais simples.
- **Começar limpo e carregar a FAQ:** os arquivos de origem não vão no passo 3, então mandar à parte e rodar com o `.env` no lugar:

  ```bash
  # no PC (Git Bash), na raiz do repositório
  ssh SERVIDOR "mkdir -p /opt/jarvis/carga"
  scp piloto/guia_comercial.json docs/designs/dados/*.json SERVIDOR:/opt/jarvis/carga/
  # no servidor, em /opt/jarvis/backend
  ../venv/bin/python -m app.cli faq seed --file /opt/jarvis/carga/guia_comercial.json
  ../venv/bin/python -m app.cli faq dados --dir /opt/jarvis/carga
  rm -r /opt/jarvis/carga     # Nielsen/GMR são dado licenciado: não deixar solto
  ```

## 7. WAHA do Jarvis (Docker, só em localhost)

Instalação própria do Jarvis, separada do WAHA do AUTO-1. Engine **GOWS** (sem navegador, leve).

```bash
ssh SERVIDOR
sudo mkdir -p /opt/jarvis/waha-sessions
sudo docker run -d --name jarvis-waha --restart unless-stopped \
  -p 127.0.0.1:3001:3000 \
  -v /opt/jarvis/waha-sessions:/app/.sessions \
  -e WHATSAPP_DEFAULT_ENGINE=GOWS \
  -e WAHA_API_KEY='<JARVIS_WAHA_API_KEY>' \
  -e WAHA_DASHBOARD_USERNAME=jarvis \
  -e WAHA_DASHBOARD_PASSWORD='<senha do dashboard>' \
  devlikeapro/waha
sudo docker image inspect devlikeapro/waha --format '{{.Id}}'   # anotar a versão instalada
```

- A porta fica presa em `127.0.0.1`: o WAHA nunca aparece para a internet.
- Os nomes das variáveis mudam entre versões do WAHA; conferir na documentação da versão
  instalada (`/docs/how-to/config/`) se o container não aceitar a chave.
- A pasta `waha-sessions` guarda o pareamento. **É ela que se leva numa migração** para não
  precisar ler o QR code de novo.

## 8. Parear o número e ligar o webhook

Abrir um túnel do PC para o dashboard do WAHA (fica aberto só enquanto o comando roda):

```bash
ssh -L 3001:127.0.0.1:3001 SERVIDOR
```

No navegador do PC: `http://localhost:3001/dashboard` (usuário e senha do passo 7).

1. Criar/iniciar a sessão `default` e ler o QR code com o WhatsApp do chip do Jarvis
   (Aparelhos conectados → Conectar aparelho).
2. Na configuração da sessão, webhook:
   - URL: `https://JARVIS_HOST/webhook/waha`
   - Eventos: `message`, `message.reaction`, `message.ack`, `poll.vote`, `session.status`
   - HMAC: a mesma chave do `JARVIS_WAHA_HMAC_KEY`
3. Perfil do número: foto, nome "Jarvis" e recado (reduz risco de bloqueio).

Em poucos segundos o `/health` passa a `"channel":"working"` e a faixa de canal fora some do painel.

## 9. nginx e HTTPS

```bash
ssh SERVIDOR
sudo cp /opt/jarvis/deploy/nginx/jarvis.conf.template /etc/nginx/sites-available/jarvis
sudo sed -i 's/JARVIS_HOST/jarvis.positiveco.com.br/g' /etc/nginx/sites-available/jarvis
sudo ln -s /etc/nginx/sites-available/jarvis /etc/nginx/sites-enabled/jarvis
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d jarvis.positiveco.com.br    # precisa do DNS já apontado
```

No `farol`, conferir antes como o certificado do site do AUTO-1 é emitido e seguir o mesmo mecanismo.

## 10. Teste de fumaça

1. `curl -s https://JARVIS_HOST/health` → `{"status":"ok","channel":"working"}`.
2. Abrir `https://JARVIS_HOST/dashboard` e entrar pelo login da Positive.
3. Do celular de um vendedor cadastrado, mandar "oi" para o número do Jarvis: aviso de
   privacidade → "ACEITO" → escolha de marca → pergunta do Guia respondida.
4. `pm2 logs jarvis --lines 50` sem traceback.

---

## Atualizar (o dia a dia)

```bash
# 1. no PC: enviar o código (passo 3)
# 2. se houver migration nova em backend/migrations/: aplicar (passo 6, só a parte do migrate,
#    sempre com MIGRATION_DATABASE_URL; as permissoes do app sao reaplicadas sozinhas)
# 3. reiniciar e conferir
ssh SERVIDOR 'pm2 restart jarvis --update-env && sleep 3 && \
  curl -s -o /dev/null -w "health: HTTP %{http_code}\n" http://127.0.0.1:8010/health && \
  pm2 logs jarvis --nostream --lines 5 --err'
```

Deploy OK quando `health: HTTP 200` e o log mostra `Application startup complete` sem traceback.
Se mudou `requirements.txt`, rodar o passo 4 antes de reiniciar.

## 11. Migrar de um servidor para outro (ex.: pessoal → `farol`)

1. No servidor novo: passos 1, 2, 3, 4, 5 (o `.env` pode vir do servidor antigo por `scp` entre os dois, ajustando a senha do banco).
2. Parar o Jarvis antigo para o banco não mudar durante a cópia: `ssh ANTIGO 'pm2 stop jarvis'`.
3. Banco: `ssh ANTIGO 'pg_dump -Fc -U jarvis_owner -h localhost jarvis' > jarvis.dump`
   e `ssh NOVO 'pg_restore -U jarvis_owner -h localhost -d jarvis --no-owner --no-acl' < jarvis.dump`
   (restaura como dono). Depois rodar `migrate` no novo (passo 6): não deve faltar migration, e ele
   reaplica as permissões do `jarvis_app`, que o `--no-acl` não trouxe.
4. WAHA: parar o container antigo (`docker stop jarvis-waha`), copiar a pasta
   `/opt/jarvis/waha-sessions` para o novo e subir o container (passo 7). Se a sessão não
   voltar `WORKING`, ler o QR code de novo (passo 8). **Nunca deixar os dois WAHA ligados com o mesmo número.**
5. Subir o PM2 (passo 6) e o nginx com certificado (passo 9) no novo.
6. Trocar o IP do registro `jarvis` no DNS para o servidor novo. Até propagar (minutos a poucas horas), o domínio ainda pode cair no antigo, que já está parado.
7. Teste de fumaça (passo 10). Só então desligar o servidor antigo.

O mesmo `pg_dump`/`pg_restore` serve para levar o banco do PC para o servidor na primeira vez
(no PC: `C:\Users\caiofranco\pgsql\bin\pg_dump.exe -Fc -U jarvis -h localhost jarvis > jarvis.dump`).
Lembrar que o banco do PC tem dados de teste.

## 12. Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| PM2 reinicia sem parar | `.env` incompleto ou inválido (a mensagem diz qual campo) | `pm2 logs jarvis --err --lines 30` |
| `Invalid host header` | domínio fora de `*.positiveco.com.br` | usar um subdomínio da Positive |
| Painel pede login sem fim | domínio fora de `positiveco.com.br` (o cookie do IAM não chega) | idem |
| `/health` com `"channel":"down"` | WAHA parado ou sessão caiu | `docker ps`, `docker logs jarvis-waha --tail 50`, dashboard pelo túnel (passo 8) |
| Mensagem chega no celular mas o Jarvis não responde | webhook errado ou HMAC diferente | conferir URL e chave do webhook no WAHA; nos logs, aumento de `invalid_signature` |
| `/health` 503 | banco fora do ar | `sudo systemctl status postgresql` |
