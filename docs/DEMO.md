# 40-second demo: one drug through the five steps

The deck's five steps on one drug: **epinephrine 1 mg/mL at Central General**, over two simulated days.
It's the drug in the scenario that goes through every step, including an order placed in a supplier's system
and a supplier reply the next day.

> Why not cyclophosphamide 500 mg? Its only supplier, Meridian, is the one reporting the 3-week delay, so a new
> order would arrive in 26 days and Central General runs out in 6. Code correctly offers only a transfer, so
> there's no order to place or follow up. It still shows steps 1–4 well if you need a second example.

## Before recording

1. Stop the API (Ctrl+C), then reset the database and the scenario files:
   ```bash
   .venv/bin/alembic downgrade base && .venv/bin/alembic upgrade head
   git checkout -- data/synthetic/progression
   ```
2. Start the API with Bedrock configured in `.env`, and the web app:
   ```bash
   .venv/bin/uvicorn apps.api.main:app --port 8000
   ```
3. Open http://localhost:5173 and set **Viewing as** to **Dana Okafor (supply manager)**.
4. A day run with the model takes a while (every alert gets a planning agent). Either run Nov 15 before you
   start recording, or cut the wait in editing.

## The script (Dana Okafor throughout)

| Time | Deck step | Click | Say |
|---|---|---|---|
| 0:00 | **1 Detect** | **Home → Run Nov 15** | "Every morning the platform reads stock at all 6 hospitals. Stock watch flags epinephrine: Central General has 2 days left." |
| 0:08 | **2 Understand** | Home's day report (or **Alerts**) | "The assessment agent read today's supplier emails. One tried to give the AI orders; the security screen held it for a person." |
| 0:15 | **3 Plan + 4 Decide** | **Approvals → Epinephrine → Approve "Transfer + order"** | "The planning agent proposed transfer, transfer + order, and a substitute. Code checked every number. Dana picks one." |
| 0:25 | **5 Act** | **Shortage Alerts → Epinephrine** order card | "On approval, the order orchestrator agent placed both orders in SAP. The transfer leaves Northside today." |
| 0:30 | **5 Follow up** | **Run Nov 16 → Home, "For you"** | "That evening the order agent read Meridian's reply: only half can ship. It escalated to Dana. The follow-up agent flagged that the transfer hasn't arrived." |
| 0:36 | All five | **History → Response status** | "One row, every step, and who did each one: agent, code or person." |

## What you should see

Checked with the rules fallback (no model) and the full API flow:

- **Nov 15:** an epinephrine alert at Central General (2.0 days) and Rural Community (12.5 days). Three options:
  - transfer
  - **transfer + order**: 50 from Northside, plus 180 + 6 ordered from Meridian Pharma
  - substitute
- **Approve transfer + order:** both orders are **Placed** via *Hospital ERP: SAP S/4HANA*, each with a reference number.
- **Nov 16** (the morning before Run Nov 16, Meridian's reply arrives; the evening check reads it):
  - The order agent reads Meridian's reply as partial (90 of 180) and escalates to the supply manager.
  - The follow-up agent escalates the Northside transfer: not received, and Central General has about 6 hours left.
  - Both show under **For you** on Dana's Home.
- With the model on, Meridian's Nov 16 epinephrine email also joins the alert. That re-checks the plan (the deck's
  replan loop) and can put a new epinephrine plan in **Approvals**. That's expected; you don't need to show it.

## Where the scripted parts live

- Supplier reply to the epinephrine order: `data/synthetic/mock/orders/order_replies.json`, story `mixed` (the default).
- Supplier systems: `data/synthetic/mock/orders/supplier_channels.json`. Meridian goes through SAP.
  `ORDER_SUBMISSION=simulate` (the code default when unset) builds and checks the order but does not submit a live supplier order. Health Canada and optional Bedrock calls are separate.
