# Drug Shortage Response Platform

The drug shortage response platform, built by team **Breaking Good**, is an agentic AI platform for hospital drug-shortage detection, coordination, allocation planning, and auditable response (Cognizant BrAInstorm Challenge 2026).

Takes a hospital network from a drug shortage signal to an approved, auditable response. It loads hospital stock every day, reads supplier emails and Health Canada shortage reports, works out which drugs will run out where, proposes ways to cover the gap, and routes each plan to the right person to approve. AI agents handle the language-heavy work; code does every calculation and safety check; people make every decision.

> **Current baseline:** `main` holds the latest integrated application. Day-to-day feature work usually branches from and opens pull requests into `dev` when that integration branch is active. See [CONTRIBUTING.md](CONTRIBUTING.md).

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
| Orders agent, order replies, and order orchestrator | Built; **simulated** supplier submission by default (`ORDER_SUBMISSION` defaults to `simulate` in code; optional in `.env`) |
| Escalations and role notifications | Built |
| API (FastAPI) and web app (React) | Built |
| Real outbound email, live ERP/EDI/portal submission, or hospital system integration | Not built on purpose: the platform records decisions and simulates placement; people act |

## How it fits together

### Technical architecture

```mermaid
flowchart LR
    subgraph sources["Data sources"]
        dpd["Health Canada<br/>Drug Product Database API"]
        hps["Health Product<br/>Shortages Canada API"]
        syn["Synthetic hospital data<br/>and supplier emails<br/>(data/synthetic/)"]
    end

    ing["ingestion/<br/>Python · httpx<br/>lineage and quarantine"]
    db[("PostgreSQL 16 (Docker)<br/>SQLAlchemy · Alembic<br/>stock · alerts · plans · decisions<br/>append-only audit log<br/>LangGraph checkpoints")]

    subgraph backend["services/ (Python 3.14)"]
        pipe["pipeline/<br/>LangGraph: daily graph and<br/>one alert graph per drug"]
        agents["agents/<br/>signals · follow-up · orders<br/>Pydantic-validated output"]
        rules["rules/<br/>risk · code checks · security screen<br/>rules planner"]
        store["store/<br/>approvals · results · escalations"]
    end

    bedrock["Amazon Bedrock (optional)<br/>Claude Haiku 4.5<br/>Converse API via boto3"]
    channels["Supplier channels<br/>SAP · EDI X12 850 · portal · email<br/>(simulated)"]
    api["apps/api<br/>FastAPI · Uvicorn"]
    web["apps/web<br/>React · TypeScript · Vite"]
    people(["Supply manager · pharmacist<br/>procurement · auditor"])

    dpd --> ing
    hps --> ing
    syn --> ing
    ing --> db
    people --> web
    web -- "/api" --> api
    api --> pipe
    api --> store
    pipe --> agents
    agents --> rules
    agents -. "model calls" .-> bedrock
    agents -. "read-only role" .-> db
    pipe --> store
    store --> db
    agents -. "simulated submit" .-> channels
```

Without Bedrock settings, every AI step falls back to code: the rules planner proposes the options, the follow-up and order checks use their fallback rules, and supplier emails wait until a model is configured. The app runs end to end either way.

### Agent architecture

```mermaid
flowchart TB
    email["Supplier email<br/>(untrusted)"] --> screen["Security screen"]
    screen -- clean --> assess["Assessment agent<br/>drug · delay · confidence"]
    screen -- flagged --> held(["Held for a person"])
    assess --> gate{"Code gate<br/>schema · confidence ≥ 0.7<br/>known drug"}
    report["Health Canada<br/>shortage report"] --> alert
    watch["Stock watch<br/>days left by criticality"] --> alert
    gate -- SupplyEvent --> alert["Living alert<br/>one per drug"]
    alert --> risk["Risk calculation<br/>who runs short, by how much"]
    risk --> plan["Planning agent<br/>transfers · substitutes · orders"]
    risk -. "no model" .-> rplan["Rules planner"]
    plan --> checks{"Code checks<br/>donor floor · approved substitutes"}
    rplan --> checks
    checks -. "failed: one retry with reasons" .-> plan
    checks -- "verified options" --> approve(["A person approves one option"])
    approve -- transfer --> fu["Follow-up agent<br/>reader + decider, each evening"]
    approve -- order --> orderagent["Order agent<br/>drafts request · reads supplier replies"]
    orderagent --> orchestrator["Order orchestrator agent<br/>picks channel · reads results"]
    orchestrator --> subagents["Channel sub-agents<br/>SAP · EDI · portal · email"]
    subagents --> conn["Connectors<br/>build and check payload<br/>(simulated submit)"]
    fu -. replan .-> risk
    orderagent -. replan .-> risk
    fu -- escalate --> notify(["Role notifications"])
    orderagent -- escalate --> notify
    orchestrator -- "a person must act" --> notify

    classDef ai fill:#ede7f6,stroke:#5e35b1,color:#311b92
    classDef code fill:#e3f2fd,stroke:#1e88e5,color:#0d47a1
    classDef person fill:#e8f5e9,stroke:#43a047,color:#1b5e20
    classDef input fill:#fff3e0,stroke:#fb8c00,color:#e65100
    class assess,plan,fu,orderagent,orchestrator,subagents ai
    class screen,gate,alert,risk,rplan,checks,watch,conn code
    class approve,held,notify person
    class email,report input
```

Purple boxes are AI agents (Claude on Bedrock), blue boxes are code, green boxes are people, and orange boxes are outside input. Agents never message each other directly: they hand off typed objects (`SupplyEvent`, `PlanSet`, approved orders) through code gates.

**Inside one agent.** Each agent runs our own tool loop on boto3 (`run_agent()` in `services/agents/bedrock.py`), with no agent framework. LangGraph orchestrates the steps between agents.

```mermaid
flowchart LR
    task["Prompt and<br/>tool specs"] --> claude["Claude on Bedrock<br/>Converse API · temperature 0"]
    claude -- "tool call" --> reg["ToolRegistry<br/>allow-list · argument checks"]
    reg --> data["Read-only data<br/>PostgresData or CSV"]
    data -- "result, at most 8 rounds" --> claude
    claude -- "final JSON" --> schema{"Pydantic schema<br/>one retry with the errors"}
    schema -- valid --> codecheck{"Code checks"}
    codecheck -- passed --> saved(["Saved for a person to decide"])
```

- **One living alert per drug.** The stock watch raises it; emails and shortage reports add evidence and refresh its plan instead of opening duplicates.
- **Every plan option is checked in code** before anyone sees it: transfers keep donors above their 14-day floor, substitutes must come from the approved table. Options that fail are never offered.
- **Approval:** each plan is one decision. Choose one option, or reject the plan with a reason. The supply manager can choose any option except a substitution, which needs a pharmacist.
- **After approval:** transfers go to follow-up; orders go to the orders agent and (by default) a **simulated** multi-channel orchestrator. Escalations become role notifications on Overview.

## Orchestration (LangGraph)

The agentic part of the platform is its own pipeline, built with [LangGraph](https://langchain-ai.github.io/langgraph/) in `services/pipeline/graph/pipeline.py`. Everything an agent or a person decides happens inside it. The rest of the application (ingestion, the daily schedule, stock-watch detection, the database, the API and the UI) only starts it with a signal and resumes it when something happens.

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
| `order` | Agent | Orders agent drafts/follows the approved order; the orchestrator places it through simulated supplier channels unless held for procurement release |
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
| Each evening | `tick` (transfer and order follow-up) |

### The daily schedule

The day itself is a simple LangGraph graph too (`services/pipeline/graph/daily.py`): it loads data and detects signals in code, then hands every agentic step to the pipeline above.

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

Routing is always decided in code, never by a model, and the code checks, read-only tools, typed handoffs and approval rules are unchanged. `--orchestrator classic` (or `PIPELINE_ORCHESTRATOR=classic`) runs the same steps without LangGraph. Tests: `tests/pipeline/test_graph_daily.py` (graph and classic give identical results) and `tests/pipeline/test_agent_pipeline.py` (pausing, resuming, follow-up and replan loops). Diagrams: `python -m services.pipeline.graph.pipeline --mermaid`.

## Repository layout

```
apps/api                      FastAPI backend: endpoints (main.py) and the UI state builders (state/)
apps/web                      React + Vite web app (views/ named after the sidebar pages)
services/
  agents/                     the AI agents, plus shared bedrock.py, model_io.py, tools.py, schemas.py
    signals/                  assessment agent, planning agent, process_email / process_event
    followup/                 transfer follow-up agent and its offline demo
    orders/                   order options, order agent, supplier replies, placement/ (order
                              orchestrator and SAP / EDI / portal / email channel sub-agents)
  rules/                      code checks, risk, rules planner, email security screen, follow-up and order rules
  data/                       CSV (synthetic) and Postgres data sources
  store/                      results, approvals, follow-up and order runs on the database, escalations
  pipeline/                   daily run, shortage signals, graph/ (LangGraph pipeline and daily schedule)
ingestion                     Health Canada and synthetic-data loaders, nightly workflow
migrations                    Alembic migrations (raw SQL)
data/synthetic/mock           current-day synthetic data: CSVs, supplier emails, follow-up and order fixtures
data/synthetic/progression    10-day synthetic scenario (daily snapshots) and inbox schedule
scripts/synthetic             synthetic-data generators and inspection CLI
docker                        Postgres init (database roles)
docs                          project overview, API, demo script, team workflow, data notes
tests                         Python tests, grouped like the code (agents, store, pipeline, data, ingestion, api, synthetic)
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
cd apps/web && npm ci && cd ../..
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

| Page (sidebar label) | What it shows |
|---|---|
| Home / Overview | Today's run and report, tiles for network status, alerts, decisions waiting, follow-up and inbox, plus role notifications |
| Hospitals / Network Dashboard | One card per hospital (hover for a summary, click for details) and a drug-by-hospital heatmap |
| Shortage Alerts | Living alerts per drug, evidence, and plan options (can open an option into Approvals) |
| Stock | Drugs that need attention, one row per drug, and the full heatmap |
| Signal Review (live) / Alerts (demo) | Supplier notices and shortage signals waiting for or already assessed |
| Plans | Every option the planner proposed, by drug |
| Approvals | One card per plan: choose an option or reject the plan |
| Messages | Draft notices and procurement-facing messages |
| History | Runs, AI results, decisions, order/follow-up checks and audit trail |
| Data | Where each number came from: ingestion runs, raw snapshots, quarantined rows |

Use **Viewing as** at the bottom of the sidebar to switch roles (supply manager, pharmacist, procurement, auditor).

## Running pieces by hand

```bash
# One simulated day, end to end (what the Run button does)
.venv/bin/python -m services.pipeline.daily_run --day 2026-11-15 --planner rules

# Load synthetic hospital data
.venv/bin/python -m ingestion.synthetic_loader --day 2026-11-19
.venv/bin/python -m ingestion.synthetic_loader --progression        # all 10 days

# Real Health Canada data
.venv/bin/python -m ingestion.shortage_ingest --max-pages 1
.venv/bin/python -m ingestion.dpd_ingest --din 00693820

# Nightly workflow (what a scheduler would run)
.venv/bin/python -m ingestion.workflow nightly --dry-run

# One supplier email through the signal pipeline
.venv/bin/python -m services.agents.signals.process data/synthetic/mock/emails/clear.txt

# Plans waiting for approval
.venv/bin/python -m services.store.approvals list
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
- **`tests/synthetic/test_progression.py` regenerates `data/synthetic/progression`** with Windows line endings. If those files show as changed after running tests, restore them with `git checkout -- data/synthetic/progression`.

## Working together

- Prefer small pull requests. Feature work usually branches from `dev` and targets `dev` when that branch is the active integration line; `main` is the merged baseline reviewers should treat as current.
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- New logic comes with tests.
- Secrets stay in `.env`, which is ignored by git.
- Full contributor guide: [CONTRIBUTING.md](CONTRIBUTING.md). Security notes: [SECURITY.md](SECURITY.md).

## More documentation

- [Contributing](CONTRIBUTING.md)
- [Security](SECURITY.md)
- [API endpoints](docs/API.md)
- [Project overview](docs/PROJECT_OVERVIEW.md): problem, users, data sources, what is real vs synthetic
- [Team and workflow](docs/TEAM_AND_WORKFLOW.md) (includes historical planning notes)
- [Agents](services/agents/README.md): design rules, signal, orders, follow-up
- [Follow-up agent](docs/FOLLOW_UP_AGENT.md)
- [Synthetic data](docs/SYNTHETIC_DATA.md), [mock data](data/synthetic/mock/README.md), [progression scenario](data/synthetic/progression/README.md)
- [40-second demo runbook](docs/DEMO.md)
- [UI guidelines](apps/web/UI_GUIDELINES.md)
- [Migrations](migrations/README)
