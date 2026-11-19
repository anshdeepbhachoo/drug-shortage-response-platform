# Agents (`services/agents`)

The AI layer of the drug shortage response platform. Two pipelines live here:

| Pipeline | Owner | Entry point | What it does |
|---|---|---|---|
| **Signal → plan** | Anshdeep | `agents/signal_agents.py` | Reads a supplier email, works out which sites run short, proposes verified transfer plans |
| **Follow-up** | Aryan | `agents/followup.py`, `agents/followup_demo.py` | Watches approved transfers after approval and decides wait / nudge / replan / escalate / close |

Both run on **Amazon Bedrock** (Claude Haiku 4.5 through the Converse API) and both follow the same rules, listed below. Detailed design of the follow-up agent: [`docs/FOLLOW_UP_AGENT.md`](../../docs/FOLLOW_UP_AGENT.md).

---

## Layout

```
services/agents/
  agents/     the model-backed agents: signal_agents (email/shortage -> plan), followup, order_agent,
              order_replies, ordering/ (order orchestrator + SAP/EDI/portal/email sub-agents), their
              tools, prompts (planning), JSON helpers (model_io) and typed handoffs (schemas, followup_schemas)
  rules/      deterministic code: checks, risk, rules_planner, security (email screen), followup_rules
  data/       data access: data (CsvData), db_data (PostgresData), data_source (default_data switch)
  store/      Postgres persistence: results_store, approvals, followup_db, escalations
  pipeline/   orchestration: daily_run, shortage_signals, graph/ (LangGraph daily + agentic pipeline)
```

Module paths in the tables below are relative to `services/agents/`.

---

## Design rules (both pipelines)

1. **Python does the numbers.** Days of cover, shortfalls, lateness and safety floors are computed in code and handed to the model. The model never does arithmetic that matters.
2. **The model only does language and judgement.** Reading free text, choosing between options Python allows, explaining, drafting messages.
3. **Every model output is validated.** Pydantic schema first, then code checks. Bad output gets one retry with feedback, then a rule-based fallback or a `needs_review` result.
4. **Untrusted text is screened first.** Emails and site replies go through `rules/security.py` before any model sees them. Flagged text never reaches the model.
5. **Tools are read-only and least-privilege.** Each agent gets only the tools it needs. Before a person approves, there is no tool that sends, writes or moves anything.
6. **Nothing is executed without approval.** Planning and follow-up results have `execution_performed: False`; humans approve. The one exception is after approval: the order orchestrator agent places approved orders (simulated in the MVP), through tools that build and check every payload in code.

---

## Files

### Shared building blocks (Anshdeep)

| File | Purpose |
|---|---|
| `data/data.py` | `CsvData`: loads the mock CSVs in `data/synthetic/mock/` (drugs, sites, usage, inventory, open orders, substitutions). `stock(din)` returns on-hand, daily use and days of supply per site. `data/db_data.py` (`PostgresData`) is the same interface over the database. Synthetic-data handoff: [`docs/SYNTHETIC_DATA.md`](../../docs/SYNTHETIC_DATA.md). Inspect helper: `python -m scripts.synthetic.inspect_synthetic summary`. |
| `agents/tools.py` | `AgentTool` (one tool: name, description, JSON schema, Python function) and `ToolRegistry` (the allow-list an agent may call; rejects unknown tools and bad arguments). `build_tool_registries()` makes the assessment and planning tool sets. |
| `agents/schemas.py` | Pydantic models: `SupplyEvent`, `Move`, `PlanOption`, `PlanSet`. |
| `rules/security.py` | `inspect_supplier_email()`: regex screen for instruction override, role impersonation and unauthorised-action wording. |
| `rules/risk.py` | `cover_days_by_site()` and `calculate_site_risk()`: how many days each site must last and how many units it is short. |
| `rules/checks.py` | `check_transfer()` / `filter_options()`: deterministic checks on proposed transfers (donor keeps ≥14 days, no self-transfer, shortfall fully covered, known sites). |
| `agents/planning.py` | `PLANNING_SYSTEM_PROMPT` for the planner. |

### Signal → plan pipeline (Anshdeep): `agents/signal_agents.py`

```
email ─► length check ─► security screen ─► assessment agent ─► SupplyEvent
      ─► confidence ≥ 0.7, DIN known, stock exists
      ─► risk.py (cover days, at-risk sites, shortfall)
      ─► planning agent ─► PlanSet ─► checks.filter_options (1 retry with feedback)
      ─► result: plan_generated | no_verified_plan | no_shortage_detected | needs_review | error
```

Key functions: `run_agent()` (Bedrock Converse tool loop, max 8 rounds, temperature 0), `ask_for_model()` (JSON extraction + schema validation + 1 retry), `assess_email()`, `plan_response()`, `process_email()`.

Run: `python -m services.agents.agents.signal_agents data/synthetic/mock/emails/clear.txt`

### Follow-up pipeline (Aryan)

| File | Purpose |
|---|---|
| `agents/followup_schemas.py` | Data shapes: `TransferRequest`, `SiteReply`, `ReplyReading`, `FollowUpDecision`, `FollowUpAction`. |
| `rules/followup_rules.py` | All deterministic logic: projected stock, facts, reply checks, hard rules, allowed decisions, fallback policy, recipients, message templates, rule-based replan. Thresholds are constants at the top. |
| `agents/followup.py` | `FollowUpAgent`: the reader agent and decision agent (prompts, tools, validated calls), the main `check()` flow, `tick()` for the clock, audit logging, and `bedrock_planner()` which reuses `plan_response()` for live replans. |
| `agents/followup_demo.py` | Simulated-clock demo (4 evenings), offline or `--live`. Writes logs to `data/followup/`. |

Data: `data/synthetic/mock/followup/transfer_requests.csv`, `data/synthetic/mock/followup/replies.json`. Tests: `tests/test_followup.py`.

### Orders

| File | Purpose |
|---|---|
| `agents/orders.py` | The order option in a plan: supplier, quantity and arrival worked out and checked in code (`order_option`, `check_order`). Also the combined transfer and order (`combined_option`, `check_combined`): when an order alone arrives too close to a hospital running out, a transfer bridges it until the order arrives (plus 2 days), and the order restocks the whole shortfall. Offered only when the bridge moves less than a transfer of the whole gap; approved by the supply manager; both agents follow it up. |
| `agents/order_agent.py` | `OrderAgent`: drafts the request to the supplier after approval, then checks every approved order each evening (wait / nudge / escalate / replan / close). Reads supplier replies first; people record placed / received (`record_order_status`). |
| `agents/order_replies.py` | Supplier replies: the reader prompt, `check_order_reading` (confidence ≥ 0.7, units ≤ ordered, real dates) and the synthetic replies loader. |

Each reply is screened by `rules/security.py`, read by the reader agent (or its scripted `offline_reading` without a model), checked in code, then acted on:

| Reply | What the order agent does |
|---|---|
| confirmed | Counts as placed (no nudge to procurement); the supplier's date becomes the expected date on the purchase order |
| delayed | New expected date; if the hospital runs out first, the existing hard rule replans (or escalates when stock is too short) |
| partial | Escalates: a person decides how to cover the missing units (a replan would re-order the whole gap) |
| backordered / cancelled | Replans without that supplier, or escalates when stock is too short |
| question, flagged, unreadable, failed checks | Escalates for a person |

Data: `data/synthetic/mock/orders/order_replies.json`. Replies are timed from the order's approval and filled in from the order. `ORDER_REPLY_STORY` picks the story (`mixed` by default, `supply_problems`, or `none`). Tests: `tests/test_orders.py`, `tests/test_order_agent.py`, `tests/test_order_replies.py`.

**Escalations and notifications** (`store/escalations.py`).

- **Code decides who receives an escalation:** whoever can act on it.
  - After approval an order belongs to procurement. So the person who approved it (the supply manager for a combined option) escalates order problems to procurement, and procurement escalates back to the supply manager when the gap needs a new plan.
  - The order agent follows the same rule: placing it and supplier questions go to procurement; running out first, a partial delivery or a cancellation goes to the supply manager.
- **Each role sees its notifications in "For you" on the Overview.** These include:
  - the agents' escalations and reminders
  - orders the orchestrator handed to procurement
  - people's escalations
- **A notification clears when someone in that role marks it read.** An order waiting on procurement clears once it is placed. Everything is in `audit_log`.

### Placing orders: the order orchestrator agent (`agents/ordering/`)

```
approval (a person) -> order agent drafts the request -> ORDER ORCHESTRATOR AGENT
                                                           |-- delegates --> SAP sub-agent ----> hospital ERP (SAP S/4HANA)
                                                           |-- delegates --> EDI sub-agent ----> supplier EDI (X12 850)
                                                           |-- delegates --> portal sub-agent -> supplier portal API
                                                           |-- delegates --> email sub-agent --> procurement, to send
                                                           '-- or escalates to procurement
                       order agent follows the order up each evening
```

| File | Purpose |
|---|---|
| `agents/ordering/orchestrator.py` | The orchestrator agent (`Orchestration`): its tools, the guardrails, the rules that finish the job when the agent can't. `submit_orders`, `release_order`. |
| `agents/ordering/subagents.py` | One channel sub-agent per system (`ChannelAgent`): what it knows about its system, its tools, the code checks on its report. |
| `agents/ordering/channels.py` | The connectors (code): build the exact payload, check it, submit it (simulated). |

**Why these are agents.** Placing an order is not a lookup: each supplier takes orders through several systems, and they fail in different ways.

- The **orchestrator agent** weighs urgency (Python's `margin_days`) against how fast each system confirms. It reads its sub-agents' reports and decides what a failure means, then writes the brief that procurement gets.
- Each **sub-agent** works its system through tools. It decides whether the order is ready for that system (missing vendor number or plant: don't submit). It also reads the system's answer, which is free text. A format problem ("ship-to not set up for this partner") means try another channel. An account problem ("credit hold") means a person must act. In that case every other electronic channel to the same supplier is closed for this order: a plain router would push the order through EDI and around the hold. The email sub-agent writes the email for procurement, including what they must sort out.

| Agent | Tools | What it decides |
|---|---|---|
| Orchestrator | `get_order_facts`, `list_channels`, `delegate_to_sub_agent(channel, brief)`, `escalate_to_procurement(message)` | Which channel next, what to brief, what a failure means, when to stop and escalate |
| Channel sub-agent | `get_order`, `get_master_data`, `build_payload`, `submit_to_target` (email: `write_email`) | Whether to submit; whether a refusal means try another channel or a person must act; the summary (and the email's wording) |

**What code keeps fixed.**

- **Guardrails before any agent runs:**
  - only orders a person approved, not already placed or cancelled
  - above `AUTO_SUBMIT_MAX_UNITS` (500), held until procurement clicks **Release to supplier**
  - one run per order
- **No model ever writes the payload.** The connector builds it from the approved order and checks it (quantity, date, reference, required fields). It refuses a payload that fails and submits at most once.
- **The tools refuse what isn't allowed:**
  - a channel the supplier doesn't have
  - a channel already tried
  - an electronic channel after "a person must act"
  - anything once the order is placed, handed to procurement or escalated
- **The system's answer is screened** (`rules/security.py`) before a sub-agent reads it. A flagged answer is withheld and goes to a person.
- **Reports are checked against what happened.** A sub-agent can't report "accepted" for a refusal, and the orchestrator's summary must match the order's real state. One retry with the problem, then rules finish from where the agent stopped. Every step records who took it (`chosen_by`, `read_by`: agent or rule).
- **Everything is stored and audited.** Each step goes into `order_submission` with the payload, the brief and the sub-agent's report, and the run's summary is in the audit log as "Order orchestration" (actor `order-orchestrator`).

`ORDER_SUBMISSION` sets the mode:

- `simulate` (default): real payloads, simulated answers, nothing leaves the machine
- `live`: needs a real connector per system; none are configured, so the sub-agents report "not submitted" and it goes to email
- `off`: people place every order

Without Bedrock, the rules take every step in the same order, and the UI shows **Rules** instead of **Agent**.

A new system (Oracle, Ariba, a GPO portal) takes three things: a connector, a sub-agent description in `EXPERTISE`, and a line in the supplier settings.

**Demo suppliers** (`data/synthetic/mock/orders/supplier_channels.json`):

| Supplier | Channels | What happens |
|---|---|---|
| Meridian Pharma | SAP, then email | SAP accepts: PO 45… |
| Apex Sterile | EDI, then portal, then email | EDI refuses (ship-to not set up), so the portal places it |
| Northgate Labs | portal, then EDI, then email | The portal refuses (credit hold), so EDI is skipped and the email to procurement explains the hold |

Tests: `tests/test_order_orchestrator.py`. They use a fake model that calls the same tools, and cover what the agents may do and what the code refuses.

---

## Setup

Install, database and web app: see the [root README](../../README.md#getting-started). For the agents, `.env` (gitignored, never commit it) needs:

```
AWS_REGION=us-east-2
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
AWS_BEARER_TOKEN_BEDROCK=<your short-term Bedrock API key>
MOCK_TODAY=2026-11-15
```

`MOCK_TODAY` pins "today" to the mock inventory date. The IAM user needs `bedrock:InvokeModel` on the inference profile and the underlying foundation model.

## Running

All commands from the repo root.

```bash
# Signal → plan
.venv/bin/python -m services.agents.agents.signal_agents data/synthetic/mock/emails/clear.txt

# Follow-up demo: offline (no AWS), live (Bedrock), with step trace, or JSON
.venv/bin/python -m services.agents.agents.followup_demo
.venv/bin/python -m services.agents.agents.followup_demo --live
.venv/bin/python -m services.agents.agents.followup_demo --trace
.venv/bin/python -m services.agents.agents.followup_demo --json

# Tests (no AWS needed)
.venv/bin/python -m pytest tests -q
```

## Testing and swapping parts

`process_email(email, data=None, llm=None, today=None)` takes its data source, model call and date as arguments. Defaults: the mock CSVs, Bedrock, and `MOCK_TODAY`. Importing `agents/signal_agents.py` needs no AWS settings; they are checked only when Bedrock is called. `agents/model_io.py` holds the JSON helpers both pipelines share.

Tests: `tests/test_agent.py` (signal → plan, fake model) and `tests/test_followup.py`.

## Data source and database

- `AGENT_DATA_SOURCE=postgres` (in `.env`) makes every agent entry point read from the operational tables through `PostgresData` (`data/db_data.py`, read-only role). Unset or `csv` keeps the mock CSVs. `data/data_source.default_data()` is the one switch.
- On the database the agents get two extra read-only tools: `get_shortage_reports` (Health Canada shortage status) and `get_drug_product` (DPD record: brand, company, status, ingredients). In CSV mode the tool lists are unchanged.
- `pipeline/shortage_signals.py`: an active shortage report for a tracked drug becomes a supply event (code, no model), then goes through `signal_agents.process_event()` (risk, planning agent, transfer checks) and is saved with `store/results_store.py`. Each report is planned once; if planning fails it stays pending. Runs as the `signals` step of `python -m ingestion.workflow nightly`, and is skipped if no model is configured.
- CLI: `python -m services.agents.agents.signal_agents <email> --db --as-of 2026-11-18 --save`.
- Approvals: `python -m services.agents.store.approvals list | approve | reject` (`store/approvals.py`). Role rules and a re-check against current stock are enforced in code; one decision per plan.
- Follow-up on the database: `python -m services.agents.store.followup_db --now 2026-11-16T18:00 --as-of 2026-11-15` (`store/followup_db.py`). Watches the transfers of approved options, runs `FollowUpAgent` unchanged, writes each check to `audit_log` and rebuilds its state from there on the next run. Scheduled form: `python -m ingestion.workflow followup`.
