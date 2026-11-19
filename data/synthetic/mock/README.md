# Synthetic mock data (active current-day set)

Teammate handoff guide: [`docs/SYNTHETIC_DATA.md`](../../../docs/SYNTHETIC_DATA.md)

All content here is **synthetic**. DINs starting with `9900` are **not** real Health Canada registrations.

## What `make_mock_data.py` does

```powershell
python -m scripts.synthetic.make_mock_data
```

**Overwrites (by design, for reproducibility):**
- `sites.csv`, `drugs.csv`, `inventory.csv`, `usage.csv`, `open_orders.csv`, `substitutions.csv`
- Generated emails such as `delay_epinephrine.txt`, `routine_no_action.txt`, etc.

**Never overwrite / never touch:**
- Protected emails: `clear.txt`, `ambiguous.txt`, `hostile.txt`, `sneaky.txt`
- Entire `followup/` directory

Do **not** put permanent manual edits in the generated CSVs or generated emails. Change `make_mock_data.py` and regenerate instead.

Current-day data here is the reproducible starting point for demos and for the 10-day progression under `data/synthetic/progression/` on current `main`.

## Reported vs applied supplier events

Some emails describe supplier events that are **not** applied as order/inventory mutations in the current-day CSVs.

Example: `emails/cancellation_notice.txt` says order `O-1017` is cancelled, but `open_orders.csv` still lists `O-1017` with `status=open`.

Why: `risk.py` / `CsvData.orders()` currently ignore order status, so a cancelled row would still be treated as incoming supply if left in the file with another status—or would still affect tools if kept as open. Phase 1 keeps statuses as `open` and treats some emails as **Signal Agent inputs** (reported events), not as already-applied ledger updates.

See `SCENARIOS.md` for scenario intent.
