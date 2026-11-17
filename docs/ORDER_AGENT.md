# Order agent

Owner: Aryan. Code: `services/agents/order_*.py`. Tests: `tests/test_orders.py` (offline), `tests/test_orders_db.py` (Postgres).

The order agent closes the supply-chain loop. The signal pipeline spots a shortage and plans a response, people approve it, and the follow-up agent watches transfers between hospitals. The order agent buys stock from suppliers: it decides what to order, gets a person's approval, places the order and follows it up until it arrives.

```
             ┌──────────── nightly reorder scan (routine) ────────────┐
             │                                                        ▼
supplier email / shortage report ─► signal pipeline ─► plan with options:     draft order
                                                       transfer | ORDER       (pending_approval)
                                                             │                     │
                                     person approves option ─┘     person approves ┘  (role + spend limit,
                                                             ▼                     ▼   re-checked first)
                                                         order agent: place ─► simulated supplier channel
                                                             │
                     supplier replies ─► security screen ─► reader agent ─► code checks
                                                             │
          accept · record_delay · switch_supplier · chase · escalate · close
                                   │             │
             delay leaves a site short           replacement order ─► back to approval
             └─► supply event ─► signal pipeline (re-plan transfers)
```

## Where orders come from

| Source | When | Status when created | Approved by |
|---|---|---|---|
| **Routine reorder** (`draft_routine_orders`, `order_db scan`) | A site will fall under 14 days of stock before a new delivery could land, counting orders already on the way | `pending_approval` | procurement up to $10,000, supply manager above |
| **Shortage plan** (`agent._order_option` → `approvals.approve_plan` → `order_db.sync_plan_orders`) | The signal pipeline found a shortage; code adds an "emergency order from another supplier" option next to the planner's transfer options | `approved` (the plan decision is the approval) | same roles and limit, checked in `approvals.py` |
| **Replacement** (`switch_supplier`) | A supplier refused, could only ship part, or slipped past the day the site runs out | `pending_approval` | same |

## Who decides what

| Step | Decided by | Where |
|---|---|---|
| Days of stock per site (counting deliveries that land before it runs out) | code | `order_rules.cover_days` |
| Whether to reorder, how many units, which supplier | code | `reorder_proposals`, `shortage_order_option`, `rank_offers` |
| Checks before approval: sells the drug, not backordered, minimum order, whole packs, no more than 90 days of use, price matches catalog, lands before the site runs out (shortage orders) | code | `check_order_line` |
| Who may approve how much | code | `approval_problems` (`PROCUREMENT_LIMIT`) |
| Approve / reject | **a person** | `OrderAgent.approve/reject`, `order_db approve/reject`, `approvals.approve_plan` |
| The purchase order text | code template | `purchase_order_message`: no model ever writes an order, a quantity or a price |
| Reading a supplier's reply | reader agent (Bedrock), after the security screen | `OrderAgent.read_reply` |
| Checking the reading (qty ≤ ordered, dates after placing, confidence ≥ 0.7) | code | `check_reading` |
| Clear cases (confirmed, delivered, refused, question, delay that still lands in time, site under 3 days and order can't make it, no answer after 2 reminders) | code (hard rules) | `hard_rule` |
| Grey zones (partial shipment, delay past run-out, overdue with no reply) | decision agent picks from the choices code allows; fallback rules without a model | `allowed_decisions`, `fallback_decision` |
| Who a message goes to | code | `recipient_for` |

Supplier choice (`rank_offers`), best first: lands before the site runs out → on-time rate ≥ 85% → in stock rather than limited → cheapest → fastest. Backordered and inactive suppliers are never chosen. For a shortage, the supplier whose order is late is avoided.

## Order lifecycle

`pending_approval → approved → placed → in_transit → delivered`, with `delayed` and `cancelled`. These are the `purchase_order.status` values the database already had.

| Decision | What changes |
|---|---|
| `place` | Order text sent through the simulated channel; `expected_date` = today + supplier lead time |
| `accept` | `in_transit`, `confirmed_qty`, new date if given. A partial that is accepted is flagged for a person |
| `record_delay` | `delayed`, new `expected_date`. If it now lands after the site runs out, a **supply event** is raised for the signal pipeline |
| `switch_supplier` | Replacement order drafted with the next supplier (`replaces` = old order), waiting for approval. A refused or delayed order is cancelled; after a partial, the confirmed part stays |
| `chase` | Reminder sent through the simulated channel (max 2, 12 h apart). An order is chased when not acknowledged within 24 h, or not delivered 1 day after its date |
| `escalate` | Message drafted for the approver role; the agent stops checking until the supplier writes again |
| `close` | `delivered`, `received_qty`. A short delivery is flagged |

A supplier's new date is recorded whatever the decision, so an order that is escalated still carries the right date.

## Sending: simulated only

`SimulatedSupplierChannel` stands in for EDI, API and email. `send()` records the message in `outbox`. `replies()` plays back scripted supplier replies from `mock/orders/supplier_replies.json`, timed relative to when each order was placed (`{date+N}` and `{part}` placeholders are filled in). A real connector would replace this one class. Every action records `sent_via: "simulated"` when something went through it.

## Thresholds (`order_rules.py`)

| Constant | Value | Meaning |
|---|---|---|
| `SAFETY_DAYS` | 14 | Reorder before a site would fall under this (same floor as transfers) |
| `TARGET_DAYS` | 30 | A routine order tops a site up to this many days after it lands |
| `MAX_DAYS_ON_ORDER` | 90 | Overbuy cap per site |
| `MIN_ON_TIME_RATE` | 0.85 | Less reliable suppliers only if nobody better can deliver in time |
| `PROCUREMENT_LIMIT` | $10,000 | Above this, the supply manager approves |
| `ACK_HOURS` | 24 | Supplier should confirm within this |
| `MAX_CHASES` / `CHASE_COOLDOWN_HOURS` | 2 / 12 | Reminders before a person takes over |
| `ESCALATE_COVER_DAYS` | 3 | Site this close to empty and the order can't make it → person |
| `MIN_READING_CONFIDENCE` | 0.7 | Below this, a person reads the reply |

## Data

Synthetic, labelled as such:
- `mock/suppliers.csv`: the 5 suppliers already named on open orders, with channel, on-time rate and contact.
- `mock/supplier_catalog.csv`: 50 offers (2 to 3 suppliers per drug): unit cost (CAD), pack size, minimum order (2 packs), lead time, availability. Some are deliberately limited or backordered (methotrexate at Cascade, propofol at Harbour).
- `mock/orders/supplier_replies.json`: scripted replies for the demo: confirmations, a partial, a delay, a refusal, a supplier that goes quiet, and a hostile reply.

Database (migration `c4d5e6f7a8b9_orders`): `supplier.on_time_rate/contact`, new `supplier_catalog` table, `plan_action.supplier_id` (an order option line: `to_site` = receiving site), and on `purchase_order`: `kind` (`routine`/`shortage`; NULL for orders loaded from the hospital feed, which the agent only reads), `approved_by/role`, `placed_at`, `needed_by`, `confirmed_qty`, `received_qty`, `action_id`, `replaces`, `reason`.

Without a catalog (for example the `tests/fixtures/baseline_three_site` folder) no order option is added, so the signal pipeline behaves exactly as before.

## Running

```bash
# Offline demo: 23 routine drafts, one shortage order, approvals, then 4 days of follow-up
.venv/bin/python -m services.agents.order_demo
.venv/bin/python -m services.agents.order_demo --trace     # every step
.venv/bin/python -m services.agents.order_demo --live      # Bedrock reads replies and decides grey zones

# Database
.venv/bin/alembic upgrade head
.venv/bin/python -m services.agents.order_db load-catalog
.venv/bin/python -m services.agents.order_db scan --as-of 2026-11-15
.venv/bin/python -m services.agents.order_db list
.venv/bin/python -m services.agents.order_db approve PO-xxxxxxxx --by "Marc Tremblay" --role procurement
.venv/bin/python -m services.agents.order_db run --as-of 2026-11-15 --now 2026-11-15T10:00 --replies mock/orders/supplier_replies.json
.venv/bin/python -m ingestion.workflow orders              # scheduled: scan, then run (rules only)
```

Logs: `data/orders/audit.jsonl` and `data/orders/orders.log` (demo); `audit_log` with actor `order-agent` (database). Each record keeps `at` (the agent's clock, simulated in the demo) and `logged_at` (real time).

## Website

The **Orders** page (`apps/web/src/views/Orders.tsx`, charts in `components/OrderCharts.tsx`) shows:

- four cards: waiting for approval, needs a person, on the way, done (click one to filter the list);
- **Where every order is**: orders and money at each stage, from waiting for approval to delivered (click a bar to list those orders);
- **Money by supplier**: spend per supplier, approved or sent versus still waiting;
- **Will each order arrive before the hospital runs out?**: per open order, the days until it arrives (dashed when not approved yet, using the supplier's lead time) against the day the hospital runs out (red mark), over 7- and 14-day bands;
- the list of orders, most urgent first, with a detail panel to approve or reject;
- what the agent did, check by check, with a bar of who made the calls (people, fixed rules, the AI).

The Home dashboard has a **Supplier orders** tile. Orders is in the menu for the supply manager, procurement (their start page) and the auditor.

**Live from the database** through the platform API (`apps/api/main.py`, FastAPI):

```bash
docker compose up -d                                          # the database
.venv/bin/python -m services.agents.order_db load-catalog     # once: suppliers and their prices
.venv/bin/uvicorn apps.api.main:app --port 8000               # the platform API
cd apps/web && npm run dev                                    # the website; /api is forwarded to the API
```

- The order agent runs as **step 6 of every simulated day** (`daily_run.run_orders_for_day`, 20:00 on the day's clock), when you click **Run day** at the top of the site: it turns approved plan order options into purchase orders, sends approved orders through the simulated channel, reads that day's scripted supplier replies (`mock/orders/supplier_replies.json`), follows every open order up, then drafts new reorders. It is skipped until a supplier catalog is loaded. The day report includes `orders`.
- Approve and reject call `OrderAgent.approve/reject` through `order_db`, the same as the command line: role and spend limit (procurement up to $10,000, the supply manager any amount), the re-check, and an `audit_log` entry timed on the simulated day. Rejecting needs a note.
- Endpoints: `GET /api/orders?as_of=YYYY-MM-DD`, `POST /api/orders/<id>/approve|reject` (`{by, role, note, as_of}`; 403 wrong role, 404 unknown order, 409 not allowed, 422 reject without a note). Tests: `tests/test_orders_api.py`.
- Prototype only: the API trusts the name and role the page sends. There is no login.

**Saved run** when the API isn't running: the page says so and reads `apps/web/src/data/orders.json`. Decisions made then stay in that browser. Refresh the file after changing the agent or the mock data:

```bash
.venv/bin/python -m services.agents.order_demo --export          # fixed rules only
.venv/bin/python -m services.agents.order_demo --live --export   # the AI reads replies (needs Bedrock)
```

Both use `services/agents/order_view.py`, so the page looks the same either way.

## Known limits

- Sending is simulated. There is no EDI, API or email connector.
- One order is one drug, one site, one supplier. Orders are not combined per supplier.
- Prices, lead times and reliability are synthetic and fixed. No price quotes or contracts.
- Delivered stock is not added to inventory snapshots; the inventory feed is the source of truth for on-hand stock.
- The orders API has no login: anyone who can reach it on your machine can approve as any name. Approval also doesn't lock the row, so two people approving the same order at the same moment isn't guarded against across processes (one API process handles one write at a time).
