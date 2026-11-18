#!/usr/bin/env bash
# Atualiza o Jarvis num servidor ja instalado (deploy/RUNBOOK.md, secao "Atualizar").
# Roda no PC, pelo Git Bash, na raiz do repositorio:
#
#   bash deploy/deploy.sh [SERVIDOR] [--migrate] [--pip] [--dry-run]
#
#   SERVIDOR    alias SSH (padrao: farol)
#   --migrate   aplica migrations pendentes como dono (MIGRATION_DATABASE_URL do .env do servidor)
#   --pip       reinstala backend/requirements.txt no venv do servidor
#   --dry-run   so mostra o que faria; nao conecta no servidor
#
# Servidor compartilhado: cada passo remoto pede confirmacao (s/N). Nunca le nem imprime o .env;
# a URL do dono e lida dentro do servidor so na hora do migrate.
set -euo pipefail

SERVER="farol"
MIGRATE=0
PIP=0
DRY=0
for arg in "$@"; do
  case "$arg" in
    --migrate) MIGRATE=1 ;;
    --pip) PIP=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    -*) echo "opcao desconhecida: $arg" >&2; exit 2 ;;
    *) SERVER="$arg" ;;
  esac
done

REMOTE_DIR="/opt/jarvis"
PAYLOAD=(backend/app backend/migrations backend/requirements.txt frontend/templates frontend/static deploy)

if [[ ! -d backend/app || ! -f deploy/RUNBOOK.md ]]; then
  echo "rode na raiz do repositorio (onde ficam backend/ e deploy/)" >&2
  exit 2
fi

step() { printf '\n== %s\n' "$1"; }

# Confirma e roda um comando remoto. Em --dry-run so mostra.
remote() {
  local title="$1" cmd="$2"
  step "$title"
  printf '   ssh %s "%s"\n' "$SERVER" "$cmd"
  if [[ $DRY -eq 1 ]]; then return 0; fi
  read -r -p "   continuar? [s/N] " answer
  if [[ "$answer" != "s" && "$answer" != "S" ]]; then
    echo "   parado a pedido; nada mais foi feito."
    exit 1
  fi
  ssh "$SERVER" "$cmd"
}

step "Versao local"
echo "   commit: $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
  echo "   AVISO: ha mudancas nao commitadas; elas tambem vao para o servidor."
fi

step "Enviar o codigo para $SERVER:$REMOTE_DIR (sem .env, testes, docs nem __pycache__)"
echo "   tar -czf - ${PAYLOAD[*]} | ssh $SERVER tar -xzf - -C $REMOTE_DIR"
if [[ $DRY -eq 0 ]]; then
  read -r -p "   continuar? [s/N] " answer
  if [[ "$answer" != "s" && "$answer" != "S" ]]; then echo "   parado a pedido."; exit 1; fi
  tar -czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='.env*' "${PAYLOAD[@]}" \
    | ssh "$SERVER" "tar -xzf - -C $REMOTE_DIR"
fi
echo "   lembrete: o tar nao apaga arquivos removidos do repositorio; remova a mao se houver."

if [[ $PIP -eq 1 ]]; then
  remote "Dependencias (requirements.txt mudou)" \
    "cd $REMOTE_DIR && venv/bin/pip install -r backend/requirements.txt"
fi

remote "Migrations pendentes (so leitura)" \
  "cd $REMOTE_DIR/backend && MIGRATION_DATABASE_URL=\"\$(grep -E '^MIGRATION_DATABASE_URL=' .env | cut -d= -f2-)\" ../venv/bin/python -m app.db.migrate --status"

if [[ $MIGRATE -eq 1 ]]; then
  remote "Aplicar migrations como dono (reaplica as permissoes do jarvis_app)" \
    "cd $REMOTE_DIR/backend && MIGRATION_DATABASE_URL=\"\$(grep -E '^MIGRATION_DATABASE_URL=' .env | cut -d= -f2-)\" ../venv/bin/python -m app.db.migrate"
fi

remote "Reiniciar e conferir" \
  "pm2 restart jarvis --update-env && sleep 3 && curl -s -o /dev/null -w 'health: HTTP %{http_code}\n' http://127.0.0.1:8010/health && pm2 logs jarvis --nostream --lines 5 --err"

step "Fim"
echo "   deploy OK quando aparece 'health: HTTP 200' e o log nao tem traceback."
