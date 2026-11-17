# Follow-up agent: technical design

Owner: Aryan (Agentic AI lead). Branch: `feat/follow-up-agent`, built on `feat/signal-assessment-agent`.

For the agents package as a whole (files, setup, both pipelines) see [`services/agents/README.md`](../services/agents/README.md).

---

## 1. Why it exists

The signal agent and planner end at **"a manager approved a transfer."** In practice approved transfers go wrong: the sending site forgets, sends part of it, refuses, or a fake reply arrives. Meanwhile the receiving site keeps using stock.

The follow-up agent watches each approved transfer and, at every check, records one decision:

| Decision | Meaning | Who acts |
|---|---|---|
| `wait` | Nothing to do yet | Nobody |
| `nudge` | Draft a reminder to the sending site | Human reviews and sends |
| `replan` | Sending site won't deliver the rest; propose another donor | Human approves the new plan |
| `escalate` | A manager has to decide now | Approver |
| `close` | All units confirmed (or the transfer was rejected) | Nobody |

It **never sends a message or moves stock**. Every decision is a record for a human.

---

## 2. Split of responsibilities

| Layer | Does | Why |
|---|---|---|
| **Python rules** (`followup_rules.py`) | Lateness, projected cover, hard rules, which decisions are allowed, fallback, recipients, rule-based replan | Numbers and safety rules must be exact and repeatable |
| **Reader agent** (model) | Turns a site's free-text reply into a `ReplyReading` | Human replies are messy; this is a language task |
| **Decision agent** (model) | Picks one decision from Python's allowed list and drafts the message text | Grey zones need judgement and wording |
| **Code checks** | Validate every model output, retry once, then fall back to rules | The model can be wrong or manipulated |

The model can only choose from what Python allows, so even a confused or manipulated model cannot pick an unsafe action.

---

## 3. Data formats

### `mock/followup/transfer_requests.csv`

One row per approved transfer. Loaded into `TransferRequest`.

| Column | Example | Notes |
|---|---|---|
| `request_id` | `T-101` | Unique ID |
| `plan_id` | `P-2026-11-15-01` | The approved plan it came from |
| `din` | `99000001` | Must exist in `mock/drugs.csv` |
| `from_site`, `to_site` | `B`, `A` | Site IDs from `mock/sites.csv` |
| `units` | `150` | > 0 |
| `approval_status` | `approved` | `pending`, `approved` or `rejected` |
| `approver_role` | `pharmacy_manager` | Recipient of escalations |
| `deadline` | `2026-11-16T12:00:00` | ISO datetime |

Runtime-only fields (not in the CSV): `units_confirmed` (default 0), `status` (`open` / `replanned` / `escalated` / `closed`), `last_nudged_at`.

### `mock/followup/replies.json`

List of site replies. Loaded into `SiteReply`. The text is **untrusted**.

```json
{
  "request_id": "T-102",
  "received_at": "2026-11-16T10:00:00",
  "sender": "Northside pharmacy (site B)",
  "text": "We can release 20 vials of vasopressin today...",
  "offline_reading": { "kind": "partial_confirmation", "confirmed_units": 20, ... }
}
```

`offline_reading` is the answer the reader agent should give. It is used **only in offline mode** so the demo runs without AWS.

### Model outputs

`ReplyReading` (reader agent):

```json
{ "kind": "full_confirmation | partial_confirmation | refusal | question | unrelated",
  "confirmed_units": 20, "new_ship_date": null, "confidence": 0.95, "summary": "..." }
```

`FollowUpDecision` (decision agent):

```json
{ "decision": "wait | nudge | replan | escalate | close", "reason": "...", "message_draft": "..." }
```

A `message_to` field is accepted but **ignored**; code sets recipients (section 5.7).

### Output record: `FollowUpAction`

| Field | Meaning |
|---|---|
| `at` | Agent clock time of the check (simulated in the demo) |
| `request_id`, `decision`, `reason` | What was decided and why |
| `decided_by` | `rule`, `agent` or `fallback` |
| `message_to`, `message_draft` | Draft for a human to send (nudge / escalate only) |
| `facts` | All Python-computed numbers used (section 5.3) |
| `flags` | Safety or validation problems found |
| `replan` | Replan result, if any (source, shortfall, verified and rejected options) |
| `trace` | Every step taken, in order |
| `needs_human` | True for nudge, replan, escalate, or any flag |
| `execution_performed` | Always `false` |

---

## 4. The simulated clock

The mock inventory is a single snapshot taken at `MOCK_TODAY` (midnight, 2026-11-15). The demo moves a clock forward and calls `tick(now)` at 18:00 on four days.

Stock is projected forward linearly:

```
projected_on_hand = on_hand − daily_use × days_since_snapshot
```

e.g. site A, cyclophosphamide: 60 − 10 × 1.75 = 42.5 units at 18:00 on Nov 16.

`tick(now)` checks every open request and passes only replies with `last_tick < received_at ≤ now`. In production `now` would be the real time; nothing else changes.

---

## 5. One check, step by step (`FollowUpAgent.check`)

### 5.1 Screen replies
Each new reply goes through `security.inspect_supplier_email`. If it is flagged, its text is **not sent to any model**, and the reasons are added to `flags`.

### 5.2 Read replies
Clean replies go to the reader agent (or `offline_reading` in offline mode), then `check_reading()`:

| Check | Result |
|---|---|
| `confidence < 0.7` | Rejected, flagged |
| `confirmed_units > units` requested | Rejected, flagged |
| `full_confirmation` with no unit count | Units set to the full amount |
| `full_confirmation` with fewer units than requested | Downgraded to `partial_confirmation` |
| `partial_confirmation` with no unit count | Rejected, flagged |

A valid reading raises `units_confirmed`. If the reader errors (model down, bad JSON twice), the reply is flagged for a human.

### 5.3 Compute facts (`compute_facts`)

| Fact | How |
|---|---|
| `hours_overdue`, `overdue` | `now − deadline` |
| `units_outstanding` | `units − units_confirmed` |
| `receiving_on_hand` | Projected stock at `to_site` + `units_confirmed` (confirmed units assumed to arrive) |
| `receiving_cover_days` | `receiving_on_hand ÷ daily_use` |
| `nudges_sent`, `hours_since_last_nudge` | From agent state |

### 5.4 Hard rules (`hard_rule`), no model

Checked in order; the first match decides.

1. `approval_status == rejected` → **close**
2. `approval_status == pending` → **wait**
3. Any reply was flagged → **escalate**
4. `units_outstanding == 0` → **close**
5. `receiving_cover_days < ESCALATE_COVER_DAYS` (3) → **escalate**

If none match and the transfer is not overdue and has no new reading → **wait**.

### 5.5 Grey zone: allowed decisions (`allowed_decisions`)

Start with `{nudge, replan}`, then:

| Condition | Effect |
|---|---|
| `nudges_sent ≥ MAX_NUDGES` (2) or last nudge < `NUDGE_COOLDOWN_HOURS` (12) ago | remove `nudge` |
| Reply was a refusal or partial confirmation | remove `nudge` (the site already answered) |
| Cover < `REVIEW_COVER_DAYS` (4), or nudges used up, or reply was a refusal/question | add `escalate` |
| Not overdue and no refusal/partial | add `wait` |

`replan` is always allowed. If only one decision is allowed, the rule takes it without calling the model.

### 5.6 Decide
With a model: the decision agent gets the request, facts, reading and allowed list. Its answer must pass:
- schema validation, and
- `decision ∈ allowed`, and
- a `message_draft` for nudge / escalate.

On failure it gets one retry with the problem stated; on a second failure `fallback_decision()` decides.

`fallback_decision` (also the whole policy in offline mode):

1. Partial or refusal → **replan**
2. `wait` allowed → **wait**
3. `nudge` allowed → **nudge**
4. `escalate` allowed and cover < 4 days → **escalate**
5. Otherwise → **replan**

### 5.7 Messages and recipients
If a nudge or escalate has no model draft, `template_message()` writes one. The recipient is **always set by code** (`recipient_for`): `Site <from_site>` for nudges, `approver_role` for escalations. A recipient suggested by the model is ignored and noted in the trace, so a manipulated model cannot redirect a message.

### 5.8 Replan
`replan_inputs()` projects stock to `now`, removes the confirmed units from the failed donor and adds them to the receiver, and sets the shortfall to `units_outstanding`.

- **Live** (`bedrock_planner`): calls Anshdeep's `plan_response()` with the shortfall and feedback "do not use `<from_site>`", drops any option that still uses that site, then runs `checks.filter_options` on projected stock.
- **Offline or planner failure** (`deterministic_replan`): picks the site (not the failed donor or the receiver) with the most units above a 14-day reserve that covers the gap, then runs the same `filter_options`.

If no option passes the checks, the decision becomes **escalate**.

### 5.9 Record
State is updated (nudge count and time, or status `replanned` / `escalated` / `closed`; non-open requests are no longer checked), a `FollowUpAction` is built and the audit line is written.

---

## 6. Thresholds

All in `followup_rules.py`. These are **demo defaults** and need agreeing with pharmacy staff.

| Constant | Value | Used for |
|---|---|---|
| `ESCALATE_COVER_DAYS` | 3 | Hard escalate below this cover |
| `REVIEW_COVER_DAYS` | 4 | Model may escalate below this cover |
| `MAX_NUDGES` | 2 | Reminders before nudging stops |
| `NUDGE_COOLDOWN_HOURS` | 12 | Minimum gap between reminders |
| `DONOR_KEEP_DAYS` | 14 | Reserve a replan donor must keep (matches `checks.py`) |
| `MIN_READING_CONFIDENCE` | 0.7 | Minimum trusted reader confidence |

---

## 7. Agents, prompts and tools

| Agent | Prompt | Tools (all read-only) |
|---|---|---|
| Reader | `READER_SYSTEM_PROMPT` | `get_transfer_request` |
| Decision | `DECISION_SYSTEM_PROMPT` | `get_transfer_request`, `get_site_stock`, `get_follow_up_history` |

Tools are built with Anshdeep's `AgentTool` / `ToolRegistry`. They read from the in-memory request list, `CsvData`, and the agent's own history; none touch a database or send anything. Reply text is wrapped in markers and JSON-escaped (`serialize`) before going into a prompt.

The model call is injected (`llm(system_prompt, task, registry) -> str`). Live mode passes `agent.run_agent`; tests pass a fake; offline passes `None`. `FollowUpAgent._ask` is used instead of `agent.ask_for_model` because it supports custom code checks and catches `ValidationError` correctly.

---

## 8. Logging and audit

| Output | Location | Content |
|---|---|---|
| Trace | `FollowUpAction.trace`; on screen with `--trace` | One line per step |
| Step log | `data/followup/followup.log` | Real timestamp, agent-clock time, request ID, step; warnings; tool calls in live mode |
| Audit log | `data/followup/audit.jsonl` | One JSON line per decision: the full `FollowUpAction` plus `reading`, `allowed_decisions` and `logged_at` |

**Two times:** `at` is the agent's clock (what all the facts are based on, simulated in the demo); `logged_at` is the real UTC time the record was written. In production they match.

Both files live under `data/`, which is gitignored, and are recreated on each demo run. Permanent history belongs in the database once it exists.

---

## 9. Demo scenario (offline)

| Request | Situation | Nov 15 | Nov 16 | Nov 17 | Nov 18 |
|---|---|---|---|---|---|
| T-101 cyclophosphamide B→A, 150 | No reply | wait | nudge | nudge | **escalate** (cover 2.25 d) |
| T-102 vasopressin B→A, 30 | B sends 20, keeps 10 | wait | **replan** (10 from C) | | |
| T-103 norepinephrine C→A, 20 | Full confirmation | wait | **close** | | |
| T-104 cisplatin B→C, 10 | Prompt-injection reply | wait | wait | **escalate** (flagged) | |

Live mode (Haiku 4.5) produces the same story, except that on Nov 17 the decision agent escalates T-101 at 3.25 days of cover (allowed, since cover < 4), and T-102's replan comes from the planning agent.

---

## 10. Tests (`tests/test_followup.py`)

They use `FakeModel`, which returns scripted JSON and records every prompt, so no AWS is needed and results are deterministic. They cover:

- the offline timeline and every outcome
- `execution_performed` always false
- a replan never reuses the failed donor
- a hostile reply never reaches the model
- an allowed model decision is used; a disallowed one is retried then falls back
- "full" with missing units is downgraded; an over-count is flagged
- low cover escalates without calling the model
- nudges stop at the limit
- planner failure falls back to the rule replan
- pending approval waits
- every decision is traced and audited, with real and agent times
- escalate is not offered while cover is healthy, and is offered once it's short
- a partial reply replans without calling the decision model
- recipients are set by code; every draft has one

Run: `.venv/bin/python -m pytest tests -q`

---

## 11. Known limitations and next steps

- **`new_ship_date` is read but not used.** A reply promising a later date isn't yet compared with remaining cover.
- **Confirmed units count as arrived** immediately; there is no in-transit state.
- **Open orders are ignored** for the receiver's cover (conservative).
- **Linear projection** from one snapshot; real usage varies.
- **State is in memory.** Requests, nudge counts and history reset each run; a database is needed for real use.
- **Escalated / replanned requests stop being tracked.** A replan's new transfers would need to become new requests.
- **Not connected to the UI.** Needs an API layer (e.g. `POST /clock/advance` → `tick`, `GET /followups`) and a "Follow-up" step in the UI demo.
- **Thresholds are unvalidated** defaults (section 6).
