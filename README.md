# MediFlow

**MediFlow** is an agentic AI platform for hospital drug-shortage detection, coordination, allocation planning, and auditable response (Cognizant BrAInstorm Challenge 2026).

Takes a hospital network from a drug shortage signal to an approved, auditable response. It loads hospital stock every day, reads supplier emails and Health Canada shortage reports, works out which drugs will run out where, proposes ways to cover the gap, and routes each plan to the right person to approve. AI agents handle the language-heavy work; code does every calculation and safety check; people make every decision.

> **Development branch:** `dev`. Open pull requests against `dev`, not `main`. See [CONTRIBUTING.md](CONTRIBUTING.md).

**Home:** today's run and a tile for each part of the platform.

![Home dashboard](docs/images/home.jpg)

**Hospitals:** one card per hospital, a drug-by-hospital heatmap, and everything about a hospital on click.

![Hospitals page with Central General selected](docs/images/hospitals.jpg)

**Stock:** drugs that need attention, one row per drug, with the selected hospital's stock projection.

![Stock page](docs/images/stock.jpg)

*Screens show the synthetic 10-day scenario.*

## What works today

| Area | Status |
|---|---|
| Health Canada Drug Product Database and shortage report ingestion | Built, real data |
| Hospital stock, usage, orders, supplier emails | Synthetic (10-day scenario, Nov 15 to 24), clearly labelled |
| Postgres schema, migrations, lineage, quarantine, append-only audit log | Built |
| Simulated daily run (ingest, inbox, stock watch, shortage reports, follow-up) | Built |
| Signal assessment and planning agents (Amazon Bedrock) | Built; optional |
| Rules planner (no AI) | Built; used when no model is configured |
| Approvals with role rules and stock re-check | Built |
| Follow-up agent for approved transfers | Built |
| API (FastAPI) and web app (React) | Built |
| Orders agent | In progress |
| Sending anything (emails, orders, transfers) | Not built on purpose: the platform records decisions, people act |

## How it fits together

```
 Health Canada APIs ─┐                          ┌─► Approvals (people decide)
 Synthetic hospital  ├─► ingestion ─► Postgres ─┤
 data and emails ────┘                          └─► daily run ─► stock watch ─► planner ─► code checks
                                                       │            (AI or rules)
                                                       └─► follow-up on approved transfers
 Web app (apps/web) ◄── API (apps/api) ◄── Postgres
```

- **One living alert per drug.** The stock watch raises it; emails and shortage reports add evidence and refresh its plan instead of opening duplicates.
- **Every plan option is checked in code** before anyone sees it: transfers keep donors above their 14-day floor, substitutes must come from the approved table. Options that fail are never offered.
- **Approval:** each plan is one decision. Choose one option, or reject the plan with a reason. The supply manager can choose any option except a substitution, which needs a pharmacist.

## Repository layout

```
apps/api            FastAPI backend: UI state, approvals, day runs
apps/web            React + Vite web app
services/agents     Agents, planner, checks, approvals, follow-up, daily run
ingestion           Health Canada and synthetic-data loaders, nightly workflow
migrations          Alembic migrations (raw SQL)
scenarios/progression  10-day synthetic scenario and inbox schedule
mock                Single-day mock data and supplier emails
docker              Postgres init (database roles)
docs                Project overview, team workflow, data notes
tests               Python tests
```

## Getting started

### Requirements
- Python 3.14 (what the team uses)
- Node.js 20.19+ or 22.12+
- Docker (for Postgres)

### 1. Install

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd apps/web && npm install && cd ../..
```

### 2. Configure

```bash
cp .env.example .env
```

Set the three passwords in `.env` (`POSTGRES_PASSWORD`, `INGESTION_DB_PASSWORD`, `READONLY_DB_PASSWORD`) and use the same values in the three `DATABASE_URL*` lines. Never commit `.env`.

AI is optional. To use it, fill in `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION` and `BEDROCK_MODEL_ID`. Without them the platform uses the rules planner, and supplier emails wait until a model is available.

### 3. Start the database and create the tables

```bash
docker compose up -d
.venv/bin/alembic upgrade head
```

### 4. Run the API and the web app (two terminals)

```bash
.venv/bin/uvicorn apps.api.main:app --port 8000
```

```bash
cd apps/web && npm run dev
```

Open http://localhost:5173. The app opens on **Home**. Click **Run Nov 15 ▶** to load the first day's data and let the platform raise alerts and plans.

If the page shows a scripted story ("Step 1 of 7"), the API wasn't reachable: check the API terminal. Add `?demo=1` to the URL to see that scripted demo on purpose.

### Start over

```bash
.venv/bin/alembic downgrade base
.venv/bin/alembic upgrade head
```

## Using the app

Pick who you are with **Viewing as** at the bottom of the sidebar.

| Page | What it shows |
|---|---|
| Home | Today's run and its report, plus tiles for hospitals, stock, problems, decisions waiting, follow-up and the inbox |
| Hospitals | One card per hospital (hover for a summary, click for details) and a drug-by-hospital heatmap |
| Stock | Drugs that need attention, one row per drug, and the full heatmap |
| Problems | Each drug's alert and the evidence behind it (stock watch, emails, reports) |
| Plans | Every option the planner proposed, by drug |
| Approvals | One card per plan: choose an option or reject the plan |
| History | Every run, AI result, decision and follow-up check |
| Data | Where each number came from: ingestion runs, raw snapshots, quarantined rows |

## Running pieces by hand

```bash
# One simulated day, end to end (what the Run button does)
.venv/bin/python -m services.agents.daily_run --day 2026-11-15 --planner rules

# Load synthetic hospital data
.venv/bin/python -m ingestion.synthetic_loader --day 2026-11-19
.venv/bin/python -m ingestion.synthetic_loader --progression        # all 10 days

# Real Health Canada data
.venv/bin/python -m ingestion.shortage_ingest --max-pages 1
.venv/bin/python -m ingestion.dpd_ingest --din 00693820

# Nightly workflow (what a scheduler would run)
.venv/bin/python -m ingestion.workflow nightly --dry-run

# One supplier email through the signal pipeline
.venv/bin/python -m services.agents.agent mock/emails/clear.txt

# Plans waiting for approval
.venv/bin/python -m services.agents.approvals list
```

## Tests

```bash
.venv/bin/python -m pytest -q
cd apps/web && npm test && npm run build
```

Database tests are skipped unless `TEST_DATABASE_URL` points at a **disposable** Postgres; they drop and recreate every table. To run them against a throwaway container:

```bash
docker run -d --rm --name dsr-test -e POSTGRES_USER=platform_owner -e POSTGRES_PASSWORD=test -e POSTGRES_DB=shortage_platform -e INGESTION_DB_PASSWORD=test -e READONLY_DB_PASSWORD=test -v "$PWD/docker/postgres/init:/docker-entrypoint-initdb.d:ro" -p 127.0.0.1:55432:5432 postgres:16
```

```bash
TEST_DATABASE_URL=postgresql+psycopg://platform_owner:test@127.0.0.1:55432/shortage_platform TEST_DATABASE_URL_READONLY=postgresql+psycopg://readonly_app:test@127.0.0.1:55432/shortage_platform .venv/bin/python -m pytest -q
```

```bash
docker stop dsr-test
```

## Known limits

- **Supplier emails need the AI.** With no model configured they show as "waiting: reading emails needs the model".
- **If the AI fails partway through a run** (for example an expired token), plans for that run are not made. Switch the planner to "Rules planning" and run the day again.
- **The synthetic scenario doesn't react to decisions.** Approved transfers don't show up in later days' stock and hospitals never confirm shipments, so follow-up nudges and then escalates.
- **`tests/test_progression` regenerates `scenarios/progression`** with Windows line endings. If those files show as changed after running tests, restore them with `git checkout -- scenarios/progression`.

## Working together

- Branch from `dev` (`feat/...`, `fix/...`, `docs/...`, `chore/...`), keep pull requests small, and merge back into `dev`.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- New logic comes with tests.
- Secrets stay in `.env`, which is ignored by git.
- Full contributor guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security notes: [SECURITY.md](SECURITY.md).

## More documentation

- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Project overview](docs/PROJECT_OVERVIEW.md): problem, users, data sources, design
- [Team and workflow](docs/TEAM_AND_WORKFLOW.md)
- [Agents](services/agents/README.md): design rules and the signal and follow-up pipelines
- [Follow-up agent](docs/FOLLOW_UP_AGENT.md)
- [Synthetic data](docs/SYNTHETIC_DATA.md) and [mock data](mock/README.md)
- [Progression scenario](scenarios/progression/README.md)
