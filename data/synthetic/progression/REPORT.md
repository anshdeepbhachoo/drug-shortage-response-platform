# Progression report — Cyclophosphamide 500 mg (`99000001`)

Synthetic timeline generated from Phase 1 `data/synthetic/mock/` without modifying it.

- Start date: **2026-11-15**
- Days: **10**
- Focus DIN: **99000001**

## Risk horizon note

Day 1 cover days use the agent-style per-site calculation (`days until each site's own open order` + a hypothetical 21-day delay).

From Day 2 onward, **Cover days** in this report are a **shared disruption horizon**: days until delayed order O-1001 arrives. This does **not** mean every hospital shares O-1001 as its own delivery. It is a demonstration lens for the focus-DIN network story.

## Scripted events vs live agents

Ledger events such as `transfer_proposed`, `transfer_rejected`, and `followup_check` are **scripted simulation markers**. They are not outputs from Bedrock Signal/Planner/Follow-up agents. The daily CSV folders can be loaded by those agents via `CsvData` for live demos.

Same-day `usage_change` events apply **before** that day's consumption.

## Day-by-day (sites A–D)

| Day | Date | Site | Stock | Daily use | Days supply | Cover days (see note) | At risk? | Event notes |
|---:|---|---|---:|---:|---:|---:|:---:|---|
| 1 | 2026-11-15 | A | 60 | 10.0 | 6.0 | 24 | yes | baseline |
| 1 | 2026-11-15 | B | 400 | 8.0 | 50.0 | 21 | no |  |
| 1 | 2026-11-15 | C | 150 | 6.0 | 25.0 | 21 | no |  |
| 1 | 2026-11-15 | D | 200 | 7.0 | 28.6 | 21 | no |  |
| 2 | 2026-11-16 | A | 50 | 10.0 | 5.0 | 21 | yes | order_delay, supplier_disruption |
| 2 | 2026-11-16 | B | 392 | 8.0 | 49.0 | 21 | no |  |
| 2 | 2026-11-16 | C | 144 | 6.0 | 24.0 | 21 | no |  |
| 2 | 2026-11-16 | D | 193 | 7.0 | 27.6 | 21 | no |  |
| 3 | 2026-11-17 | A | 40 | 10.0 | 4.0 | 20 | yes | — |
| 3 | 2026-11-17 | B | 384 | 8.0 | 48.0 | 20 | no |  |
| 3 | 2026-11-17 | C | 138 | 6.0 | 23.0 | 20 | no |  |
| 3 | 2026-11-17 | D | 186 | 7.0 | 26.6 | 20 | no |  |
| 4 | 2026-11-18 | A | 30 | 10.0 | 3.0 | 19 | yes | transfer_proposed |
| 4 | 2026-11-18 | B | 376 | 8.0 | 47.0 | 19 | no |  |
| 4 | 2026-11-18 | C | 132 | 6.0 | 22.0 | 19 | no |  |
| 4 | 2026-11-18 | D | 179 | 7.0 | 25.6 | 19 | no |  |
| 5 | 2026-11-19 | A | 120 | 10.0 | 12.0 | 18 | yes | transfer_received |
| 5 | 2026-11-19 | B | 268 | 8.0 | 33.5 | 18 | no |  |
| 5 | 2026-11-19 | C | 126 | 6.0 | 21.0 | 18 | no |  |
| 5 | 2026-11-19 | D | 172 | 7.0 | 24.6 | 18 | no |  |
| 6 | 2026-11-20 | A | 110 | 10.0 | 11.0 | 17 | yes | usage_change, demand_spike |
| 6 | 2026-11-20 | B | 260 | 8.0 | 32.5 | 17 | no |  |
| 6 | 2026-11-20 | C | 114 | 12.0 | 9.5 | 17 | yes |  |
| 6 | 2026-11-20 | D | 165 | 7.0 | 23.6 | 17 | no |  |
| 7 | 2026-11-21 | A | 100 | 10.0 | 10.0 | 16 | yes | transfer_proposed, transfer_rejected |
| 7 | 2026-11-21 | B | 252 | 8.0 | 31.5 | 16 | no |  |
| 7 | 2026-11-21 | C | 102 | 12.0 | 8.5 | 16 | yes |  |
| 7 | 2026-11-21 | D | 158 | 7.0 | 22.6 | 16 | no |  |
| 8 | 2026-11-22 | A | 170 | 10.0 | 17.0 | 15 | no | delivery_received |
| 8 | 2026-11-22 | B | 244 | 8.0 | 30.5 | 15 | no |  |
| 8 | 2026-11-22 | C | 90 | 12.0 | 7.5 | 15 | yes |  |
| 8 | 2026-11-22 | D | 151 | 7.0 | 21.6 | 15 | no |  |
| 9 | 2026-11-23 | A | 160 | 10.0 | 16.0 | 14 | no | followup_check |
| 9 | 2026-11-23 | B | 236 | 8.0 | 29.5 | 14 | no |  |
| 9 | 2026-11-23 | C | 78 | 12.0 | 6.5 | 14 | yes |  |
| 9 | 2026-11-23 | D | 144 | 7.0 | 20.6 | 14 | no |  |
| 10 | 2026-11-24 | A | 150 | 10.0 | 15.0 | 13 | no | usage_change, stabilizing |
| 10 | 2026-11-24 | B | 228 | 8.0 | 28.5 | 13 | no |  |
| 10 | 2026-11-24 | C | 72 | 6.0 | 12.0 | 13 | yes |  |
| 10 | 2026-11-24 | D | 137 | 7.0 | 19.6 | 13 | no |  |

## Story arc

1. **Day 1:** Baseline Phase 1 stocks; A already thin if a 3-week delay is assumed.
2. **Day 2:** Supplier delay applied to O-1001 (`expected_date` → 2026-12-07); consumption continues.
3. **Days 3–4:** Stock declines; transfer B→A (150) proposed (not yet moved).
4. **Day 5:** Partial confirmation — only 100 units received B→A.
5. **Day 6:** Harbour (C) demand spike (usage rises before that day's consume).
6. **Day 7:** Scripted replan proposes D→A; scripted rejection (no stock movement).
7. **Day 8:** Partial supplier delivery (+80 to A against O-1001).
8. **Days 9–10:** Scripted follow-up checkpoint; Harbour usage eases before consume; A recovering.

## How to inspect

```powershell
python -m scripts.synthetic.generate_progression
python -c "from services.agents.data.data import CsvData; print(CsvData('data/synthetic/progression/days/2026-11-19').stock('99000001'))"
```

Ledger: `ledger.json`. Risk series: `daily_risk.json`.

