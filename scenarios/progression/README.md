# Multi-day synthetic progression

Full teammate guide: [`docs/SYNTHETIC_DATA.md`](../../docs/SYNTHETIC_DATA.md)

Deterministic 10-day timeline built from Phase 1 `mock/` **without modifying** `mock/`.

## Generate

```powershell
python generate_progression.py
```

Optional:

```powershell
python generate_progression.py --source mock --out scenarios/progression
```

`--out` cleanup only deletes `out/days` when that folder is empty or contains only `YYYY-MM-DD` snapshot directories. Unsafe paths (filesystem root, home directory, or a `days/` tree with unrelated files) are rejected.

Outputs:
- `days/YYYY-MM-DD/` — CsvData-compatible snapshots
- `ledger.json` — applied / scripted events
- `daily_risk.json` — focus-DIN risk series
- `REPORT.md` — human-readable arc for `99000001`

## Inspect a day with the real loader

```powershell
python -c "from services.agents.data.data import CsvData; from pprint import pprint; pprint(CsvData('scenarios/progression/days/2026-11-19').stock('99000001'))"
```

## Design notes

- Day 1 equals the Phase 1 morning baseline.
- Same-day usage changes apply **before** that day's consumption.
- Transfer **proposals / rejections / follow-up checkpoints** are **scripted ledger events**, not live Bedrock agent decisions. Daily CSVs can still be fed to the real agents.
- Only `transfer_received` and `delivery_received` move inventory.
- Deliveries must match the order's site and DIN.
- Inventory never goes negative; consumption is capped at on-hand.
- From Day 2, the report's cover-days column uses a **shared disruption horizon** until delayed O-1001 arrives (not each hospital's own delivery schedule). Day 1 uses agent-style per-site cover for the classic demo comparison.
