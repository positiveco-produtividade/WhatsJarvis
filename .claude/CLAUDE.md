# CLAUDE.md — Project Constitution

This file is loaded automatically by Claude Code in every session within this
repository. It defines the rules that govern how Claude operates on this
project. Keep it under 200 lines.

## Project context

- **Name:** Jarvis — Agente de Enablement Comercial (WhatsApp)
- **Stack:** Python 3.12 + FastAPI (padrão do AUTO-1, repo `../agente-instagram-direct-n8n`); PostgreSQL; Gemini (Flash); WhatsApp Cloud API (Meta direta)
- **Deploy:** droplet DigitalOcean `n8n-server-positive` (SSH alias `farol`), PM2 + nginx, subdomínio `*.positiveco.com.br` — mesmo modelo do AUTO-1. Servidor compartilhado com outras apps em produção: nada de comando destrutivo sem confirmação.
- **Owner:** Caio (Positive Company) — PRD original por Felipe Carvalho

## Documentation map

Source of truth for everything:

- **Product:** `docs/prd.md` — the WHAT and WHY
- **Architecture:** `docs/architecture/overview.md` — global tech decisions
  - Per-module: `docs/architecture/{module}.md`
  - ADRs: top of `overview.md` section 8
- **Epics:** `docs/epics/{id}.md`
- **Stories:** `docs/stories/{epic}.{n}.md`
- **Tasks (with specs):** `docs/tasks/{epic}.{story}/`
  - `task.md` — implementation breakdown
  - `spec/spec.md` — full specification
  - `spec/requirements.json`, `spec/complexity.json`, `spec/research.json`

Before writing any code, you should know which spec authorizes that code.

## Methodology — Spec-Driven Development

This project uses SDD. Skills `sdd-workflow` and `sdd-waves` and the
`/spec-*` commands are installed globally in `~/.claude/`.

**The four phases:**
1. Requirements (`/spec-new`) — what to build
2. Design (`/spec-plan`) — how to build it
3. Tasks (`/spec-tasks`) — atomic breakdown
4. Implementation (`/spec-implement`) — code, tests, commits

**Phase gates are non-optional.** No code before Phase 3 approval. Period.

**Lightweight mode:** for bug fixes, single-file changes, or obvious patterns,
skip SDD. Just edit and commit. SDD is for non-trivial work.

## Code conventions

Read 1-2 sibling files before writing new code in any directory. Match what's
there. Specific rules:

### Naming
- Folders: `kebab-case/`
- Files (most languages): `snake_case.{ext}` or `kebab-case.{ext}` — match
  existing convention in the directory
- Functions / variables: language idiomatic (`snake_case` Python, `camelCase` JS/TS)
- Classes / types: `PascalCase`
- Constants: `UPPER_SNAKE_CASE`

### Imports
- Absolute imports from project root (no `from .config import ...`)

### Commit messages
Conventional Commits: `type(scope): TASK-ID - description`

Examples:
- `feat(auth): AUTH-1.3 - add JWT middleware`
- `fix(api): BILLING-2.1 - handle null customer_id`
- `refactor(views): - extract brand-by-state helper` (no task ID OK for refactors)

Types: `feat`, `fix`, `refactor`, `chore`, `docs`, `test`, `style`, `perf`.

### Tests
Every implementation task ships with tests. Test boundaries you own (your
ports), not the library internals.

## What Claude must always do

- **Read specs before coding.** If asked to implement something with no
  spec, propose creating one first (or confirm it's a lightweight task).
- **Match existing patterns.** Look around before introducing a new style.
- **Atomic commits.** One logical change per commit, referencing task ID.
- **Verify before "done".** Run tests, type check, lint. No exceptions.
- **Update the spec when reality diverges.** Don't let docs rot.

## What Claude must never do

- Write code before Phase 3 of SDD is approved (for non-lightweight tasks)
- Touch files outside a task's `files_touched` whitelist
- Mark a task `[x]` without passing acceptance criteria
- Commit without referencing a task ID (except trivial chores)
- Add new dependencies without explicit spec mention
- Modify `docs/prd.md` or `docs/architecture/` without an ADR

## Sensitive files (never read)

- `.env*`
- `**/credentials*`, `**/secrets*`
- Any file under `private/` or `secrets/`

These are also denied at the Claude Code permissions layer
(`.claude/settings.json`).

## Verification commands

Run these to check the project works. Todos a partir de `backend/` (o venv
fica em `backend\.venv`); os testes `db` usam o banco `jarvis_test` via
`TEST_DATABASE_URL` no `backend/.env` e nunca o banco do droplet:

- **Unit tests:** `.venv\Scripts\python -m pytest` (suíte inteira: unit, integration, security)
- **Type check:** `.venv\Scripts\python -m mypy app`
- **Lint:** `.venv\Scripts\python -m ruff check app tests`
- **Migrations:** `.venv\Scripts\python -m app.db.migrate --check` (contra o banco de teste)
- **Build:** não se aplica (sem etapa de build; deploy via PM2, ver `docs/tasks/porta-fechada.1/spec/spec.md` 4.10)
- **Painel (testes):** `.venv\Scripts\python -m pytest tests/panel -q` (já incluído na suíte inteira; `perf` fica fora por padrão)
- **Painel (desempenho):** `.venv\Scripts\python -m pytest tests/panel -m perf -q` (semente grande, p95 <= 2 s)
- **Painel (CSS, sem `npm install`):** a partir de `frontend/`: `C:/Users/User/Documents/GitHub/agente-instagram-direct-n8n/frontend/node_modules/.bin/tailwindcss.cmd -i static/css/input.css -o static/css/tailwind.css --minify`; `tests/panel/unit/test_css_in_sync.py` confere (pula sem o binário, p.ex. CI sem Node)
- **Avaliação do agente:** `.venv\Scripts\python -m app.services.brain.eval --mode palavras` (gate offline, sem rede; deve imprimir APROVADO); `--mode ia` (precisa de `GEMINI_API_KEY`) é o gate de deploy: ≥ 98% factual, 100% preço

O CI (`.github/workflows/ci.yml`) roda os mesmos quatro comandos em Python 3.12
com PostgreSQL 16. Em desenvolvimento local o Python é o 3.14 e o Postgres é o
18: o código e as migrations devem continuar compatíveis com 3.12 e PG 16.

These are the canonical verifications. The quality-gate subagent uses them.

## Lessons learned (append-only)

Things that went wrong and the rule that prevents recurrence. Add new lessons
at the top.

### YYYY-MM-DD — {short title}
- **Trigger:** {what went wrong}
- **Fix:** {what the correct approach is}
- **Rule:** {how to prevent it next time}

---

This file is committed to Git. The whole team contributes to it. When the AI
makes a mistake, someone adds the rule here so it doesn't repeat.
