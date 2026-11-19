# Drug Shortage Response Platform

An agentic AI platform for hospital drug-shortage detection, coordination, allocation planning and auditable response, built by team **Breaking Good** for the Cognizant BrAInstorm Challenge 2026.

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
| Order options, order agent (supplier request, supplier replies, evening follow-up) | Built |
| Order orchestrator agent placing approved orders through SAP / EDI / supplier portal / email sub-agents | Built; simulated (`ORDER_SUBMISSION=simulate`), nothing leaves the machine |
| API (FastAPI) and web app (React) | Built |
| Sending emails or moving stock | Not built on purpose: the platform drafts and records, people act |

## How it fits together

```
 Health Canada APIs ─┐                          ┌─► Approvals (people decide)
 Synthetic hospital  ├─► ingestion ─► Postgres ─┤
 data and emails ────┘                          └─► daily run ─► stock watch ─► planner ─► code checks
                                                       │            (AI or rules)
                                                       ├─► order orchestrator ─► SAP / EDI / portal / email
                                                       └─► follow-up on approved transfers and orders
 Web app (apps/web) ◄── API (apps/api) ◄── Postgres
```

### The agent pipeline: detect → understand → plan → decide → act / follow up

| Step | Who | Where |
|---|---|---|
| **Detect** | Code | The daily run loads stock and the stock watch flags drugs that will run out; Health Canada shortage reports become supply events (`pipeline/daily_run.py`, `pipeline/shortage_signals.py`) |
| **Understand** | Code, then agent | Supplier emails go through the security screen (`rules/security.py`), then the assessment agent turns a clean one into a typed `SupplyEvent` (`agents/signal_agents.py`) |
| **Plan** | Agent or rules, then code | The planning agent (or the rules planner without a model) proposes transfer, order, transfer + order and substitute options; code works out every number and checks every option (`rules/risk.py`, `agents/orders.py`, `rules/checks.py`) |
| **Decide** | Person | One decision per plan, with role rules and a re-check against today's stock (`store/approvals.py`) |
| **Act** | Agents, code-checked | A transfer goes to follow-up. An order: the order agent drafts the supplier request, and the **order orchestrator agent** places it through one channel sub-agent per system: hospital ERP (SAP S/4HANA), supplier EDI (X12 850), supplier portal, or an email for procurement to send (`agents/order_agent.py`, `agents/ordering/`). `ORDER_SUBMISSION=simulate` (default) builds and checks real payloads with simulated answers; `live` needs real connectors (none configured); `off` leaves every order to people. Orders above `AUTO_SUBMIT_MAX_UNITS` (500) wait for procurement to release them |
| **Follow up** | Agents | Each evening the follow-up agent checks approved transfers and the order agent checks orders and reads supplier replies (confirmed, delayed, partial, backordered, cancelled): wait, nudge, escalate, replan or close (`agents/followup.py`, `agents/order_agent.py`, `agents/order_replies.py`) |

Paths are under `services/agents/`. Details, guardrails and the demo suppliers: [services/agents/README.md](services/agents/README.md).

- **One living alert per drug.** The stock watch raises it; emails and shortage reports add evidence and refresh its plan instead of opening duplicates.
- **Every plan option is checked in code** before anyone sees it: transfers keep donors above their 14-day floor, substitutes must come from the approved table. Options that fail are never offered.
- **Approval:** each plan is one decision. Choose one option, or reject the plan with a reason. The supply manager can choose any option except a substitution, which needs a pharmacist.

## Orchestration (LangGraph)

The agentic part of the platform is its own pipeline, built with [LangGraph](https://langchain-ai.github.io/langgraph/) in `services/agents/pipeline/graph/pipeline.py`. Everything an agent or a person decides happens inside it. The rest of the application (ingestion, the daily schedule, stock-watch detection, the database, the API and the UI) only starts it with a signal and resumes it when something happens.

### Alert lifecycle: one graph per drug

Each drug's alert runs in its own graph (thread `alert:<DIN>`), from the first signal to resolution. Its state is saved in Postgres, so an alert can wait days for a decision and resume exactly where it stopped.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	gap(gap)
	plan(plan)
	check(check)
	save(save)
	await_decision(await_decision)
	act(act)
	order(order)
	follow_up(follow_up)
	order_follow_up(order_follow_up)
	__end__([<p>__end__</p>]):::last
	__start__ --> gap;
	act -.-> __end__;
	act -.-> follow_up;
	act -.-> order;
	await_decision -.-> __end__;
	await_decision -.-> act;
	await_decision -.-> gap;
	check -.-> plan;
	check -.-> save;
	follow_up -.-> __end__;
	follow_up -.-> gap;
	gap -.-> plan;
	gap -.-> save;
	order --> order_follow_up;
	order_follow_up -.-> __end__;
	order_follow_up -.-> gap;
	plan -.-> check;
	plan -.-> save;
	save -.-> __end__;
	save -.-> await_decision;
	follow_up -.-> follow_up;
	order_follow_up -.-> order_follow_up;
	classDef default fill:#e8f1fb,stroke:#0b5cad,stroke-width:1px,color:#1c2330,line-height:1.2
	classDef first fill:#ffffff,stroke:#5b6575,color:#1c2330
	classDef last fill:#ffffff,stroke:#5b6575,color:#1c2330
```

| Node | Who | What it does |
|---|---|---|
| `gap` | Code | Which hospitals run short and by how much |
| `plan` | Agent | Planning agent (or rules planner) proposes up to three options |
| `check` | Code | Safety checks on every option; loops back to `plan` once with the reasons if no transfer passed |
| `save` | Code | Saves the alert and plan; continues only if an option passed |
| `await_decision` | Human | **The graph pauses.** Approve resumes it; a refused approval or new evidence loops back to `gap` |
| `act` | Code | Routes the approved option: transfer → follow-up, order (or transfer + order) → order |
| `order` | Agents | The order agent drafts the supplier request; the order orchestrator agent places each order through a channel sub-agent (SAP, EDI, portal, email) |
| `follow_up` | Agent | **Pauses until each evening**, then checks the transfer; loops until done, or back to `gap` to replan without the hospital that failed |
| `order_follow_up` | Agent | **Pauses until each evening**; the order agent reads supplier replies and checks each order (and the follow-up agent the transfer of a combined option); loops until received, or back to `gap` to replan without that supplier |

### Intake: one graph per supplier notice

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	screen(screen)
	assess(assess)
	__end__([<p>__end__</p>]):::last
	__start__ --> screen;
	screen -.-> __end__;
	screen -.-> assess;
	assess --> __end__;
	classDef default fill:#e8f1fb,stroke:#0b5cad,stroke-width:1px,color:#1c2330,line-height:1.2
	classDef first fill:#ffffff,stroke:#5b6575,color:#1c2330
	classDef last fill:#ffffff,stroke:#5b6575,color:#1c2330
```

The security screen (code) holds suspicious notices for a person; the assessment agent turns a clean one into a typed `SupplyEvent` and hands it to the drug's alert graph.

### How the rest of the app talks to it

| Event | Call |
|---|---|
| A supplier notice arrives | `read_notice` (intake), then `signal` to the drug's alert |
| Stock watch, a Health Canada report or a person asks for a plan | `signal` |
| A person approves, rejects, or the approval is refused | `decide` (from the API's approve / reject endpoints) |
| Each evening | `tick` (follow-up) |

### The daily schedule

The day itself is a simple LangGraph graph too (`services/agents/pipeline/graph/daily.py`): it loads data and detects signals in code, then hands every agentic step to the pipeline above.

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	ingest(ingest)
	inbox(inbox)
	detect(detect)
	plan_alerts(plan_alerts)
	reports(reports)
	followup(followup)
	record(record)
	__end__([<p>__end__</p>]):::last
	__start__ --> ingest;
	detect -.-> plan_alerts;
	detect -.-> reports;
	followup --> record;
	inbox --> detect;
	ingest --> inbox;
	plan_alerts --> reports;
	reports --> followup;
	record --> __end__;
	classDef default fill:#e8f1fb,stroke:#0b5cad,stroke-width:1px,color:#1c2330,line-height:1.2
	classDef first fill:#ffffff,stroke:#5b6575,color:#1c2330
	classDef last fill:#ffffff,stroke:#5b6575,color:#1c2330
```

Routing is always decided in code, never by a model, and the code checks, read-only tools, typed handoffs and approval rules are unchanged. `--orchestrator classic` (or `PIPELINE_ORCHESTRATOR=classic`) runs the same steps without LangGraph. Tests: `tests/test_graph_daily.py` (graph and classic give identical results) and `tests/test_agent_pipeline.py` (pausing, resuming, follow-up and replan loops). Diagrams: `python -m services.agents.pipeline.graph.pipeline --mermaid`.

## Repository layout

```
apps/api                      FastAPI backend: UI state, approvals, day runs, order release
apps/web                      React + Vite web app
services/agents/
  agents/                     the AI agents: signal (assessment + planning), follow-up, order agent,
                              supplier replies, ordering/ (order orchestrator + SAP/EDI/portal/email
                              sub-agents), tools, prompts and typed schemas
  rules/                      code checks, risk maths, rules planner, email security screen, follow-up rules
  data/                       CSV and Postgres data sources behind one interface
  store/                      saving results, approvals, follow-up state and escalations
  pipeline/                   daily run, shortage signals, graph/ (LangGraph pipeline and daily schedule)
ingestion                     Health Canada and synthetic-data loaders, nightly workflow
migrations                    Alembic migrations (raw SQL)
data/synthetic/mock           current-day synthetic data: CSVs, supplier emails, follow-up and order fixtures
data/synthetic/progression    10-day synthetic scenario (daily snapshots) and inbox schedule
scripts/synthetic             synthetic-data generators and inspection CLI
docker                        Postgres init (database roles)
docs                          project overview, demo script, team workflow, data notes
tests                         Python tests
```

## Getting started

### Requirements
- Python 3.14 (what the team and CI use)
- Node.js 20.19+ or 22.12+ (CI uses 22)
- Docker (for Postgres)

All commands run from the repo root unless they `cd`.

### 1. Install

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements.txt
cd apps/web && npm ci && cd ../..
```

### 2. Configure

```bash
cp .env.example .env
```

Set the three passwords in `.env` (`POSTGRES_PASSWORD`, `INGESTION_DB_PASSWORD`, `READONLY_DB_PASSWORD`) and use the same values in the three `DATABASE_URL*` lines. Never commit `.env`.

AI is optional. To use it, fill in `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION` and `BEDROCK_MODEL_ID`. Without them the platform uses the rules planner and the agents' rule fallbacks, and supplier emails wait until a model is available.

| Variable | In `.env.example` | What it does |
|---|---|---|
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | yes | The owner role and database the Postgres container creates |
| `INGESTION_DB_PASSWORD`, `READONLY_DB_PASSWORD` | yes | Passwords for the `ingestion_app` and `readonly_app` roles (created by `docker/postgres/init`) |
| `DATABASE_URL_OWNER` | yes | Owner connection: migrations |
| `DATABASE_URL` | yes | Ingestion connection: loaders, the API's writes, day runs |
| `DATABASE_URL_READONLY` | yes | Read-only connection the agents' data source uses |
| `AWS_BEARER_TOKEN_BEDROCK`, `AWS_REGION`, `BEDROCK_MODEL_ID` | yes | Amazon Bedrock for the agents (optional) |
| `SHORTAGE_API_BASE_URL`, `SHORTAGE_API_EMAIL`, `SHORTAGE_API_PASSWORD` | yes | Health Product Shortages Canada API account, for `ingestion.shortage_ingest` |
| `AGENT_DATA_SOURCE` | yes | `csv` (default: `data/synthetic/mock/`) or `postgres` (operational tables) for the agent CLIs |
| `ORDER_SUBMISSION` | no | `simulate` (default), `live` or `off`: how the order orchestrator places approved orders |
| `AUTO_SUBMIT_MAX_UNITS` | no | Orders above this many units (default 500) wait for procurement to release them |
| `ORDER_REPLY_STORY` | no | Synthetic supplier replies: `mixed` (default), `supply_problems` or `none` |
| `ORDER_LEAD_DAYS` | no | Supplier lead time used for order options (default 5) |
| `PIPELINE_ORCHESTRATOR` | no | `graph` (default, LangGraph) or `classic` |
| `MOCK_TODAY` | no | Pins "today" for the agent CLIs (default 2026-11-15) |
| `DPD_API_BASE_URL` | no | Override the Drug Product Database API URL |
| `TEST_DATABASE_URL`, `TEST_DATABASE_URL_READONLY` | no | Disposable Postgres for the database tests (see Tests) |

### 3. Start the database and create the tables

```bash
docker compose up -d
```

```bash
.venv/bin/alembic upgrade head
```

The container reads `.env`, listens on 127.0.0.1:5432 only, and creates the ingestion and read-only roles on first start.

### 4. Run the API and the web app (two terminals)

```bash
.venv/bin/uvicorn apps.api.main:app --port 8000
```

```bash
cd apps/web && npm run dev
```

Vite proxies `/api` to the API on port 8000. Open http://localhost:5173. The app opens on **Home**. Click **Run Nov 15 ▶** to load the first day's data and let the platform raise alerts and plans.

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
| Home (Overview) | Today's run and its report, tiles for each part of the platform, and **For you**: the notifications and escalations for the selected role |
| Hospitals (Network Dashboard) | One card per hospital (hover for a summary, click for details) and a drug-by-hospital heatmap |
| Shortage Alerts | Each drug's alert, its options table and, once approved, its order and transfer cards (procurement releases held orders here with **Release to supplier**) |
| Stock | Drugs that need attention, one row per drug, and the full heatmap |
| Alerts (Signal Review) | The evidence behind each alert: stock watch, supplier emails, shortage reports |
| Plans | Every option the planner proposed, by drug |
| Approvals (Approval Queue) | One card per plan: choose an option or reject the plan |
| Messages | Drafted messages for people to send |
| History (Audit Trail) | Every run, AI result, decision, order step and follow-up check |
| Data | Where each number came from: ingestion runs, raw snapshots, quarantined rows |

## Running pieces by hand

```bash
# One simulated day, end to end (what the Run button does)
.venv/bin/python -m services.agents.pipeline.daily_run --day 2026-11-15 --planner rules

# Load synthetic hospital data
.venv/bin/python -m ingestion.synthetic_loader --day 2026-11-19
.venv/bin/python -m ingestion.synthetic_loader --progression        # all 10 days

# Real Health Canada data
.venv/bin/python -m ingestion.shortage_ingest --max-pages 1
.venv/bin/python -m ingestion.dpd_ingest --din 00693820

# Nightly workflow (what a scheduler would run)
.venv/bin/python -m ingestion.workflow nightly --dry-run

# One supplier email through the signal pipeline
.venv/bin/python -m services.agents.agents.signal_agents data/synthetic/mock/emails/clear.txt

# Plans waiting for approval
.venv/bin/python -m services.agents.store.approvals list

# Place approved orders (simulated) and follow orders up
.venv/bin/python -m services.agents.agents.ordering.orchestrator --now 2026-11-15T12
.venv/bin/python -m services.agents.agents.order_agent --now 2026-11-16T18

# Synthetic data: inspect, or regenerate (Rayyan's scripts)
.venv/bin/python -m scripts.synthetic.inspect_synthetic summary
.venv/bin/python -m scripts.synthetic.inspect_synthetic generate
```

## Tests

```bash
.venv/bin/python -m pytest -q
```

```bash
cd apps/web && npx tsc --noEmit -p . && npx vitest run
```

Use `python -m pytest` from the repo root (plain `pytest` can't import `services`). `npm test` runs vitest and `npm run build` type-checks and builds, as CI does. No AWS is needed: tests use fake models.

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
- **`tests/test_progression` regenerates `data/synthetic/progression`** with Windows line endings. If those files show as changed after running tests, restore them with `git checkout -- data/synthetic/progression`.

## Working together

- Branch from `dev` (`feat/...`, `fix/...`, `docs/...`, `chore/...`), keep pull requests small, and merge back into `dev`.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- New logic comes with tests.
- Secrets stay in `.env`, which is ignored by git.
- Full contributor guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security notes: [SECURITY.md](SECURITY.md).

## More documentation

- [40-second demo](docs/DEMO.md): one drug through the five steps, with what you should see
- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [Project overview](docs/PROJECT_OVERVIEW.md): problem, users, data sources, design
- [Team and workflow](docs/TEAM_AND_WORKFLOW.md)
- [Agents](services/agents/README.md): design rules, the signal, follow-up and order pipelines, the order orchestrator
- [Follow-up agent](docs/FOLLOW_UP_AGENT.md)
- [Synthetic data](docs/SYNTHETIC_DATA.md) and [mock data](data/synthetic/mock/README.md)
- [Progression scenario](data/synthetic/progression/README.md)
