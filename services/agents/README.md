# Agents (`services/agents`)

The AI layer of the drug shortage response platform. Two pipelines live here:

| Pipeline | Owner | Entry point | What it does |
|---|---|---|---|
| **Signal → plan** | Anshdeep | `agent.py` | Reads a supplier email, works out which sites run short, proposes verified transfer plans |
| **Follow-up** | Aryan | `followup.py`, `followup_demo.py` | Watches approved transfers after approval and decides wait / nudge / replan / escalate / close |

Both run on **Amazon Bedrock** (Claude Haiku 4.5 through the Converse API) and both follow the same rules, listed below. Detailed design of the follow-up agent: [`docs/FOLLOW_UP_AGENT.md`](../../docs/FOLLOW_UP_AGENT.md).

---

## Design rules (both pipelines)

1. **Python does the numbers.** Days of cover, shortfalls, lateness and safety floors are computed in code and handed to the model. The model never does arithmetic that matters.
2. **The model only does language and judgement.** Reading free text, choosing between options Python allows, explaining, drafting messages.
3. **Every model output is validated.** Pydantic schema first, then code checks. Bad output gets one retry with feedback, then a rule-based fallback or a `needs_review` result.
4. **Untrusted text is screened first.** Emails and site replies go through `security.py` before any model sees them. Flagged text never reaches the model.
5. **Tools are read-only and least-privilege.** Each agent gets only the tools it needs. There is no tool that sends, writes or moves anything.
6. **Nothing is executed.** Every result has `execution_performed: False`. Humans approve and act.

---

## Files

### Shared building blocks (Anshdeep)

| File | Purpose |
|---|---|
| `data.py` | `CsvData`: loads the mock CSVs in `mock/` (drugs, sites, usage, inventory, open orders, substitutions). `stock(din)` returns on-hand, daily use and days of supply per site. Will be swapped for the database later. |
| `tools.py` | `AgentTool` (one tool: name, description, JSON schema, Python function) and `ToolRegistry` (the allow-list an agent may call; rejects unknown tools and bad arguments). `build_tool_registries()` makes the assessment and planning tool sets. |
| `schemas.py` | Pydantic models: `SupplyEvent`, `Move`, `PlanOption`, `PlanSet`. |
| `security.py` | `inspect_supplier_email()`: regex screen for instruction override, role impersonation and unauthorised-action wording. |
| `risk.py` | `cover_days_by_site()` and `calculate_site_risk()`: how many days each site must last and how many units it is short. |
| `checks.py` | `check_transfer()` / `filter_options()`: deterministic checks on proposed transfers (donor keeps ≥14 days, no self-transfer, shortfall fully covered, known sites). |
| `planning.py` | `PLANNING_SYSTEM_PROMPT` for the planner. |

### Signal → plan pipeline (Anshdeep): `agent.py`

```
email ─► length check ─► security screen ─► assessment agent ─► SupplyEvent
      ─► confidence ≥ 0.7, DIN known, stock exists
      ─► risk.py (cover days, at-risk sites, shortfall)
      ─► planning agent ─► PlanSet ─► checks.filter_options (1 retry with feedback)
      ─► result: plan_generated | no_verified_plan | no_shortage_detected | needs_review | error
```

Key functions: `run_agent()` (Bedrock Converse tool loop, max 8 rounds, temperature 0), `ask_for_model()` (JSON extraction + schema validation + 1 retry), `assess_email()`, `plan_response()`, `process_email()`.

Run: `python -m services.agents.agent mock/emails/clear.txt`

### Follow-up pipeline (Aryan)

| File | Purpose |
|---|---|
| `followup_schemas.py` | Data shapes: `TransferRequest`, `SiteReply`, `ReplyReading`, `FollowUpDecision`, `FollowUpAction`. |
| `followup_rules.py` | All deterministic logic: projected stock, facts, reply checks, hard rules, allowed decisions, fallback policy, recipients, message templates, rule-based replan. Thresholds are constants at the top. |
| `followup.py` | `FollowUpAgent`: the reader agent and decision agent (prompts, tools, validated calls), the main `check()` flow, `tick()` for the clock, audit logging, and `bedrock_planner()` which reuses `plan_response()` for live replans. |
| `followup_demo.py` | Simulated-clock demo (4 evenings), offline or `--live`. Writes logs to `data/followup/`. |

Data: `mock/followup/transfer_requests.csv`, `mock/followup/replies.json`. Tests: `tests/test_followup.py`.

---

## Setup

```bash
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env    # then fill in the Bedrock values below
```

`.env` (gitignored, never commit it):

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
.venv/bin/python -m services.agents.agent mock/emails/clear.txt

# Follow-up demo: offline (no AWS), live (Bedrock), with step trace, or JSON
.venv/bin/python -m services.agents.followup_demo
.venv/bin/python -m services.agents.followup_demo --live
.venv/bin/python -m services.agents.followup_demo --trace
.venv/bin/python -m services.agents.followup_demo --json

# Tests (no AWS needed)
.venv/bin/python -m pytest tests -q
```

## Known issues

- `agent.py` catches `ValidationError` in `ask_for_model()` without importing it, so invalid model JSON raises `NameError` instead of retrying. Fix: `from pydantic import ValidationError`.
- Nothing here is connected to the UI yet; there is no API layer.
- Data comes from CSVs in `mock/`; the database is not wired in.
