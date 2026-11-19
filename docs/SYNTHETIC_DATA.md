# Synthetic hospital supply-chain data (handoff)

This document is for MediFlow teammates using the synthetic dataset that supports the Signal, Planner, and Follow-up agents.

All DINs `9900xxxx` are **synthetic**. They are not Health Canada registrations. No patient data is included. Substitution rows are review-required demo records, not clinical approvals.

## What you get

| Layer | Location | Contents |
|---|---|---|
| Current-day network | `mock/` | 6 hospitals, 22 medications, 132 inventory/usage rows, 20 open orders, supplier emails |
| Classic 3-site fixture | `tests/fixtures/baseline_three_site/` | Frozen A/B/C demo numbers for exact agent regression tests |
| 10-day progression | `scenarios/progression/days/` | Daily CsvData-compatible snapshots |
| Scenario notes | `mock/SCENARIOS.md` | Current-day shortage scenarios A–H |
| Progression report | `scenarios/progression/REPORT.md` | Generated day-by-day focus-DIN table |

Agents load CSVs through `services.agents.data.CsvData` (unchanged). Default agent path remains `mock/`.

## Quick start (PowerShell)

```powershell
# From the repository root (any machine)
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Regenerate current-day + progression (safe for protected emails / followup/)
.\.venv\Scripts\python.exe inspect_synthetic.py generate

# Or step by step:
.\.venv\Scripts\python.exe make_mock_data.py
.\.venv\Scripts\python.exe generate_progression.py

# Overview
.\.venv\Scripts\python.exe inspect_synthetic.py summary

# Inspect a simulated day (date or day number)
.\.venv\Scripts\python.exe inspect_synthetic.py day 2026-11-19 --din 99000001
.\.venv\Scripts\python.exe inspect_synthetic.py day 5 --din 99000001
.\.venv\Scripts\python.exe inspect_synthetic.py day current --din 99000001

# Deterministic risk + donor spare (existing agent tools)
.\.venv\Scripts\python.exe inspect_synthetic.py risk --day 4 --din 99000001
.\.venv\Scripts\python.exe inspect_synthetic.py spare --day 4 --site B --din 99000001 --keep-days 19

# Tests (no AWS / Bedrock required)
.\.venv\Scripts\python.exe -m pytest tests/test_agent.py tests/test_synthetic_data.py tests/test_progression.py tests/test_followup.py -q
```

On macOS/Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`. Paths below (`mock/`, `scenarios/progression/`) are relative to the repo root on current `main`.

### Restore / regenerate baseline safely

- Current-day reproducible source of truth: `make_mock_data.py` → `mock/`
- Progression: `generate_progression.py` (does **not** modify `mock/`)
- Protected forever: `mock/emails/{clear,ambiguous,hostile,sneaky}.txt` and `mock/followup/`
- Do **not** hand-edit generated CSVs/emails if you need edits to survive regeneration—change the generator instead

## Walkthrough — 10 days for Cyclophosphamide 500 mg (`99000001`)

Baseline date: **2026-11-15**. Focus hospital story: **A (Central General)**, donor **B (Northside)**, spike at **C (Harbour)**.

Quantities below come from the generated progression (`daily_risk.json` / `REPORT.md`).

| Day | Date | What happens | A stock / DoS | Notes |
|---:|---|---|---|---|
| 1 | 2026-11-15 | Baseline | 60 / 6.0 days | A already at risk under a 3-week delay lens (shortfall 180). B has 400 on hand. |
| 2 | 2026-11-16 | Supplier delay applied to O-1001 → 2026-12-07; consume | 50 / 5.0 | Ledger: `order_delay`, `supplier_disruption` |
| 3 | 2026-11-17 | Consume | 40 / 4.0 | Decline continues |
| 4 | 2026-11-18 | Consume; transfer B→A **proposed** (150) | 30 / 3.0 | **Scripted** proposal — inventory unchanged. B spare still large (e.g. ~224 @ keep_days=19). |
| 5 | 2026-11-19 | Consume; **partial transfer received** 100 B→A | 120 / 12.0 | Real stock movement. A improves but may still be short vs long horizon. |
| 6 | 2026-11-20 | C usage 6→12 **before** consume; consume | A 110 / 11.0; C 114 / 9.5 | C becomes at risk after the demand spike |
| 7 | 2026-11-21 | Scripted replan D→A proposed then **rejected** | A 100 / 10.0 | No stock movement from rejection |
| 8 | 2026-11-22 | Partial supplier delivery +80 to A (O-1001) | 170 / 17.0 | A leaves at-risk under the shared horizon; C still strained |
| 9 | 2026-11-23 | Scripted follow-up checkpoint; consume | 160 / 16.0 | |
| 10 | 2026-11-24 | C usage returns toward 6 **before** consume | A 150 / 15.0; C 72 / 12.0 | A recovering; C still watched |

### What is calculated vs scripted

| Kind | Examples | Who produces it |
|---|---|---|
| Deterministic calculations | days of supply, shortfall, spare units, transfer feasibility checks | Existing `CsvData`, `risk.py`, `tools.get_spare_units`, `checks.py` |
| Scripted ledger events | `transfer_proposed`, `transfer_rejected`, `followup_check` | `generate_progression.py` demo story — **not** Bedrock agent outputs |
| Applied stock events | consumption, `transfer_received`, `delivery_received`, usage changes | Progression engine (mathematically accounted) |
| Live agent demos | `python -m services.agents.agent mock/emails/clear.txt` | Signal/Planner with Bedrock (optional; needs AWS). Use any day’s folder via `CsvData(path)` if wiring a custom call. |

## Known limitations

1. **Order status:** `risk.py` / `CsvData.orders()` do not filter on status. Current-day data keeps `status=open`. Some emails report cancellations that are **not** applied to the ledger (`cancellation_notice.txt`).
2. **Shared risk horizon (progression Day 2+):** report/risk helper uses days until delayed O-1001 for the focus-DIN story. That is **not** each hospital’s personal delivery schedule.
3. **No autonomous clinical decisions:** substitutions are review-required records only.
4. **ZIP hygiene:** never package `.env` or `.venv` in handoff archives. Prefer `git archive` after commit.

## Repository map (synthetic-data owned)

```
make_mock_data.py              # Phase 1 current-day generator
generate_progression.py        # Phase 2 10-day snapshots
inspect_synthetic.py           # Phase 3 teammate CLI
mock/                          # active current-day dataset + emails + followup/
scenarios/progression/         # days/, REPORT.md, ledger.json
tests/fixtures/baseline_three_site/
tests/test_synthetic_data.py
tests/test_progression.py
docs/SYNTHETIC_DATA.md         # this file
```

## Relation to agents

- `services/agents/data.py` reads the CSV schemas above.
- Existing email security tests still use `mock/emails/{clear,hostile,...}.txt`.
- Follow-up demos still use `mock/followup/`.
- Exact classic A/B/C cover_days assertions use `tests/fixtures/baseline_three_site/` so expanded `mock/` can stay six-hospital.
