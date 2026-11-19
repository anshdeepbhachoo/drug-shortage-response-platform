# Synthetic shortage scenarios (current-day)

All data under `mock/` is **synthetic**. DINs `9900xxxx` are **not** Health Canada registrations.

Baseline date: **2026-11-15** (`MOCK_TODAY`).

## Protected demo assets

| Asset | Notes |
|---|---|
| `emails/clear.txt`, `ambiguous.txt`, `hostile.txt`, `sneaky.txt` | Never overwritten by `scripts/synthetic/make_mock_data.py` |
| `followup/` | Untouched by the generator |
| A/B/C rows for DINs `99000001`–`99000010` | Numerically identical to the original demo |

## Scenario catalogue

### A — Critical shortage with a clear donor
- **DIN:** `99000001` (Cyclophosphamide 500 mg)
- **Email:** `emails/clear.txt`
- **Sites:** A short; B has surplus; C limited spare
- **Expected:** Signal finds delay; risk marks A; planner can use B as donor
- **Limitation:** none for this path

### B — Multiple donor hospitals required
- **DIN:** `99000011` (Epinephrine 1 mg/mL)
- **Email:** `emails/delay_epinephrine.txt`
- **Sites:** A large shortfall; B and C each have partial spare (~100 @ 21 keep-days)
- **Expected:** single-donor transfer of full shortfall fails checks; multi-source or escalate
- **Limitation:** planner may need multi-move options; checker allows multiple moves

### C — No feasible transfer
- **DIN:** `99000012` (Propofol)
- **Sites:** all six sites tight vs a 21-day cover
- **Expected:** positive shortfalls; spare units ~0 everywhere
- **Limitation:** none

### D — Multiple hospitals at risk
- **DIN:** `99000017` (Amiodarone)
- **Email:** `emails/multi_site_amiodarone.txt`
- **Orders:** O-1007 (A), O-1008 (D)
- **Expected:** A and D both appear at risk under the shared delay
- **Limitation:** none

### E — Ambiguous supplier communication
- **Email:** `emails/ambiguous.txt` or `emails/ambiguous_strengths.txt`
- **Expected:** assessment chooses review / clarification, not invented DIN detail

### F — Potential substitution
- **DIN:** `99000001` → `99000002` (and demo row `99000011` → `99000008`)
- **Expected:** `get_approved_substitutions` returns the recorded option for human review only

### G — No shortage
- **Email:** `emails/routine_no_action.txt`
- **Expected:** no planning escalation

### H — High-priority scarce stock
- **DIN:** `99000021` (Methotrexate)
- **Email:** `emails/uncertain_replacement.txt`
- **Expected:** many sites short; little transferable surplus

## Order status caveat

`risk.py` and `CsvData.orders()` currently treat **all** rows in `open_orders.csv` as relevant incoming supply. They do **not** filter on `status`.

Phase 1 therefore uses **`status=open` only**. Do not assume cancelled or fulfilled orders are ignored by risk logic.

## Reported vs applied events

| Email | What it reports | Applied in current-day CSVs? |
|---|---|---|
| `clear.txt` | O-1001 delayed ~3 weeks | Demo uses delay in agent `delay_weeks`; order row stays `open` with original expected date |
| `cancellation_notice.txt` | O-1017 cancelled | **Not applied** — order remains `open` so risk logic is not silently misled by unsupported statuses |
| `partial_shipment.txt` | Partial release for O-1006 | **Not applied** in current-day snapshot (use Phase 2 progression for applied partial receipts) |
| `routine_no_action.txt` | Billing notice | No inventory/order change (correct) |

Treat supplier emails as **inputs to the Signal Agent**. Treat CSV inventory/orders as **applied ledger state**. They only match when a scenario explicitly applies the event.

## Regenerating

```powershell
python -m scripts.synthetic.make_mock_data
```

**Warning:** this **overwrites** the active CSVs and the generated (non-protected) emails. It does not modify `followup/` or the four protected email files. Do not hand-edit generated files if you need those edits to survive regeneration—change the generator instead.

This Phase 1 dataset is the reproducible starting point for Phase 2 progression snapshots under `scenarios/progression/`.
