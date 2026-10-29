# Ambiente de desenvolvimento (Jarvis backend)

## Versoes
- Runtime alvo (droplet): **Python 3.12**, PostgreSQL **16**. O codigo deve continuar compativel com 3.12
  (ruff `target-version = py312`, mypy `python_version = 3.12`).
- Maquina de desenvolvimento: Python 3.14.3 (via `py -3`) e PostgreSQL 18.6. Nao usar recurso exclusivo do PG 17/18.
- Combinacao testada em 3.14: fastapi 0.115.6, uvicorn 0.34.0, asyncpg 0.31.0, pydantic-settings 2.7.1,
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

       & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -d postgres -f backend\scripts\dev_db_setup.sql

2. Defina as senhas no psql, para nao ficarem em arquivo nem no historico do shell:

       & "C:\Program Files\PostgreSQL\18\bin\psql.exe" -U postgres -h localhost -d postgres
       \password jarvis
       \password jarvis_test

3. Copie `backend/configuracao-modelo.txt` (criado na PF-02) para `backend/.env` e preencha `DATABASE_URL`
   (banco `jarvis`, papel `jarvis`) e `TEST_DATABASE_URL` (banco `jarvis_test`, papel `jarvis_test`).
   Nunca aponte `TEST_DATABASE_URL` para `jarvis` nem para o banco do droplet: os testes fazem `TRUNCATE`.

## Atencao: PG 18 local x PG 16 em producao
Nenhuma migration ou SQL pode usar recursos do 17/18 (`uuidv7()`, colunas virtuais geradas,
`RETURNING OLD/NEW`, `WITHOUT OVERLAPS`, `MERGE ... RETURNING`).

## Remover os bancos de dev (sem tocar no servico)

    DROP DATABASE jarvis;
    DROP DATABASE jarvis_test;
    DROP ROLE jarvis;
    DROP ROLE jarvis_test;
