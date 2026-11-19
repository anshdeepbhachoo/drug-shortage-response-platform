# Team and workflow

Current development practices are documented in [CONTRIBUTING.md](../CONTRIBUTING.md) and the root [README.md](../README.md). Project capabilities are summarized in [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Current practice (as of the integrated `main` baseline)

- **`main`** holds the latest merged application.
- Day-to-day feature work usually branches from **`dev`** and opens pull requests into **`dev`** when that branch is the active integration line.
- Prefer small, focused pull requests and conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- New logic should come with tests. Secrets stay in `.env` (never commit credentials).
- Automated checks: GitHub Actions **CI** (Python tests without external services, frontend test/build, secret scan). Database-backed tests need a disposable Postgres URL locally.
- Do not force-push shared branches or overwrite unfinished teammate work.

## Historical planning notes

> The sections below are **early hackathon planning material**. They are kept for context. They do **not** describe the current repository layout, staffing assignments, or delivery status. Prefer CONTRIBUTING.md and the root README for how to work in the repo today.

### Original role sketch (placeholders)

| Role | Owns (planning intent) |
|---|---|
| Agentic AI | Extraction, explanation, drafting agents, tool definitions, prompt-injection defenses |
| Backend and data | Schema, migrations, ingestion, FastAPI, audit, Docker, CI |
| Planning and risk | Days-of-supply, constraints, plan ranking, synthetic inventory |
| Frontend and demo | Console views, demo script, pitch support |

### Original layout sketch (superseded)

The early plan mentioned folders such as `/services/planner`, `/packages/schemas`, and `/infra`. The **current** layout on `main` is:

```
apps/web                    Frontend (React + Vite)
apps/api                    FastAPI backend
services/agents             AI agents: signal, follow-up, order agent, order orchestrator (orders/placement/)
services/rules              code checks, risk, rules planner, email security screen
services/data               CSV and Postgres data sources
services/store              results, approvals, follow-up state, escalations
services/pipeline           daily run, shortage signals, LangGraph graphs
ingestion                   Health Canada and synthetic-data loaders, nightly workflow
migrations                  Alembic migrations
data/synthetic              Synthetic hospital data: data/synthetic/mock/ (current day) and progression/ (10 days)
scripts/synthetic           Synthetic-data generators and inspection CLI
docker, docker-compose.yml  Postgres and its database roles
docs                        Overview, demo script, team workflow, data notes
tests                       Python tests
```

### Original working agreements (partially realized)

Ideas that remain useful: small PRs, conventional commits, secrets out of git, honest labeling of real vs synthetic vs planned. Treat any unfulfilled items (CODEOWNERS, Notion board, fixed two-week plan) as historical intent, not current policy.
