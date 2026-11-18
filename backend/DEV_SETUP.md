# Ambiente de desenvolvimento (Jarvis backend)

## Versoes
- Runtime alvo (droplet): **Python 3.12**, PostgreSQL **16**. O codigo deve continuar compativel com 3.12
  (ruff `target-version = py312`, mypy `python_version = 3.12`).
- Maquina de desenvolvimento (PC atual do Caio, desde 2026-11): **Python 3.12.10 e PostgreSQL 16.16**,
  iguais aos do droplet. O PostgreSQL e a distribuicao em ZIP, sem instalador nem administrador, em
  `C:\Users\caiofranco\pgsql` (`bin\pg_ctl.exe` para ligar; `bin\psql.exe` para o setup abaixo).
  O notebook antigo usava Python 3.14 e PostgreSQL 18: o codigo continua compativel com os dois, mas
  nenhum recurso exclusivo do PG 17/18 pode ser usado.
- Combinacao testada (3.14 no notebook antigo; 3.12 neste PC e no CI): fastapi 0.115.6, uvicorn 0.34.0, asyncpg 0.31.0, pydantic-settings 2.7.1,
  httpx 0.28.1; dev: pytest 9.0.3, pytest-asyncio 1.3.0, ruff 0.15.15, mypy 2.3.1.
  Para 3.12 valem as mesmas versoes (a confirmar no CI com Python 3.12).

## Venv
O venv usa `--system-site-packages`, reaproveitando as bibliotecas ja instaladas globalmente (sem downloads):

    cd backend
    py -3 -m venv --system-site-packages .venv

Em maquina limpa (ou no droplet), instale normalmente: `.venv\Scripts\python -m pip install -r requirements-dev.txt`.

## Testes, lint e tipos (a partir de `backend\`)

    .venv\Scripts\python -m pytest
    .venv\Scripts\python -m ruff check .
    .venv\Scripts\python -m mypy .

Testes marcados `db` pulam sozinhos sem `TEST_DATABASE_URL`.

## Banco de desenvolvimento (passo unico, feito pelo Caio)
1. Rode o script como superusuario `postgres` (usado so aqui, a aplicacao nunca o usa):

       & "C:\Users\caiofranco\pgsql\bin\psql.exe" -U postgres -h localhost -d postgres -f backend\scripts\dev_db_setup.sql

2. Defina as senhas no psql, para nao ficarem em arquivo nem no historico do shell:

       & "C:\Users\caiofranco\pgsql\bin\psql.exe" -U postgres -h localhost -d postgres
       \password jarvis
       \password jarvis_test

3. Copie `backend/configuracao-modelo.txt` (criado na PF-02) para `backend/.env` e preencha `DATABASE_URL`
   (banco `jarvis`, papel `jarvis`) e `TEST_DATABASE_URL` (banco `jarvis_test`, papel `jarvis_test`).
   Nunca aponte `TEST_DATABASE_URL` para `jarvis` nem para o banco do droplet: os testes fazem `TRUNCATE`.

## Papel da aplicacao `jarvis_app` (porta-fechada.2)
Em producao o app conecta como `jarvis_app` (so le e grava dados) e as migrations como o dono
(`MIGRATION_DATABASE_URL`). No PC o app continua com o papel dono (`jarvis`), mas **os testes
rodam o app como `jarvis_app`** para provar que ele funciona sem DDL. Por isso o script acima
cria `jarvis_app` (NOLOGIN, sem senha) e da `GRANT jarvis_app TO jarvis_test`. Quem ja tinha
rodado o script antes de 2026-11-18 roda de novo (idempotente, passo 1). Sem o papel, a suite
falha com a instrucao. As permissoes vem da migration 402 e o runner as reaplica a cada
`python -m app.db.migrate` (e a cada sessao de testes).

## WAHA no PC

O WAHA roda em Docker, e este PC nao pode usar Docker. Por isso nao ha WAHA local: os testes
simulam o WAHA (`httpx.MockTransport` e as fixtures de `tests/fixtures/waha/`), e o primeiro
contato com o WAHA real e o do go-live, no droplet (`docs/tasks/canal-waha.1/go-live.md`).
Numa maquina com Docker, o comando do passo 7 do `deploy/RUNBOOK.md` funciona igual (porta
`127.0.0.1:3001`); para o webhook chegar ao PC seria preciso um tunel publico (ngrok ou
cloudflared, permitido so fora de producao).

## Atencao: PG 18 local x PG 16 em producao
Nenhuma migration ou SQL pode usar recursos do 17/18 (`uuidv7()`, colunas virtuais geradas,
`RETURNING OLD/NEW`, `WITHOUT OVERLAPS`, `MERGE ... RETURNING`).

## Remover os bancos de dev (sem tocar no servico)

    DROP DATABASE jarvis;
    DROP DATABASE jarvis_test;
    DROP ROLE jarvis;
    DROP ROLE jarvis_test;
