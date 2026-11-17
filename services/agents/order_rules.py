"""Deterministic rules for ordering from suppliers. No model is used here.

Three jobs:
1. Decide WHAT to order: how many days of stock a site has (counting
   deliveries already on the way), whether it needs a new order, how many
   units, and from which supplier.
2. CHECK an order before anyone can approve it: supplier sells the drug,
   pack sizes and minimums, no overbuying, price matches the catalog, it
   arrives before the site runs out, and who may approve that much spend.
3. TRACK an order after it is placed: facts (late? acknowledged? site cover),
   hard rules, the choices the decision agent may pick from, the fallback when
   no model is available, recipients and message templates.

All thresholds are constants at the top.
"""

from __future__ import annotations

import csv
import math
from datetime import date, datetime, timedelta
from pathlib import Path

from services.agents.order_schemas import (
    PurchaseOrder,
    SupplierOffer,
    SupplierReading,
)
from services.agents.schemas import OrderLine, PlanOption

# ---------------------------------------------------------
# Thresholds
# ---------------------------------------------------------

SAFETY_DAYS = 14          # same floor the transfer checks use
TARGET_DAYS = 30          # a routine order tops a site up to this many days after it lands
MAX_DAYS_ON_ORDER = 90    # never order more than this many days of use for one site
MIN_ON_TIME_RATE = 0.85   # suppliers below this are used only if nobody better can deliver
PROCUREMENT_LIMIT = 10_000.0  # orders above this (CAD) need the supply manager
ORDER_APPROVER_ROLES = {"procurement", "supply_manager"}

ACK_HOURS = 24            # a supplier should acknowledge an order within this
MAX_CHASES = 2            # reminders before a person takes over
CHASE_COOLDOWN_HOURS = 12
ESCALATE_COVER_DAYS = 3   # site this close to running out and the order won't make it -> person
MIN_READING_CONFIDENCE = 0.7

INACTIVE_ORDER_STATUSES = {"cancelled", "delivered", "draft", "rejected"}


# ---------------------------------------------------------
# Supplier catalog
# ---------------------------------------------------------

class SupplierCatalog:
    """Which supplier sells which drug, at what price, lead time and pack size."""

    def __init__(self, offers: list[SupplierOffer]):
        self._offers = offers
        self._names = {offer.supplier_id: offer.supplier_name for offer in offers}

    @classmethod
    def from_csv(cls, folder) -> "SupplierCatalog | None":
        """mock/suppliers.csv + mock/supplier_catalog.csv. None if the folder has no catalog."""

        folder = Path(folder)
        suppliers_path, catalog_path = folder / "suppliers.csv", folder / "supplier_catalog.csv"
        if not (suppliers_path.exists() and catalog_path.exists()):
            return None
        with suppliers_path.open(newline="", encoding="utf-8-sig") as handle:
            suppliers = {row["supplier_id"]: row for row in csv.DictReader(handle)}
        with catalog_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        offers = []
        for row in rows:
            supplier = suppliers.get(row["supplier_id"])
            if supplier is None:
                raise ValueError(f"Catalog row for unknown supplier {row['supplier_id']}")
            offers.append(SupplierOffer(
                supplier_id=row["supplier_id"],
                supplier_name=supplier["name"],
                din=row["din"],
                unit_cost=float(row["unit_cost"]),
                pack_size=int(row["pack_size"]),
                min_order_qty=int(row["min_order_qty"]),
                lead_time_days=int(row["lead_time_days"]),
                availability=row["availability"],
                on_time_rate=float(supplier["on_time_rate"]),
                channel=supplier.get("channel") or "email",
                contact=supplier.get("contact") or None,
                active=(supplier.get("active", "true").strip().lower() != "false"),
            ))
        return cls(offers)

    def offers(self, din: str) -> list[SupplierOffer]:
        return [offer for offer in self._offers if offer.din == din]

    def offer(self, supplier_id: str, din: str) -> SupplierOffer | None:
        return next((o for o in self._offers if o.supplier_id == supplier_id and o.din == din), None)

    def dins(self) -> list[str]:
        return sorted({offer.din for offer in self._offers})

    def supplier_name(self, supplier_id: str) -> str:
        return self._names.get(supplier_id, supplier_id)


def catalog_for(data) -> SupplierCatalog | None:
    """The catalog that goes with a data source: database tables for PostgresData,
    the CSVs next to the stock files for CsvData. None when there is no catalog,
    in which case no order options are proposed."""

    engine = getattr(data, "engine", None)
    if engine is not None:
        from services.agents.order_db import load_catalog_from_db

        return load_catalog_from_db(engine)
    folder = getattr(data, "folder", None)
    return SupplierCatalog.from_csv(folder) if folder is not None else None


# ---------------------------------------------------------
# Stock projection
# ---------------------------------------------------------

def incoming_by_site(order_rows: list[dict], today: date, exclude: set[str] = frozenset()) -> dict[str, list[tuple[int, int]]]:
    """Open orders as (days from today, units) per site. Overdue orders count as arriving today."""

    arrivals: dict[str, list[tuple[int, int]]] = {}
    for row in order_rows:
        if row.get("order_id") in exclude or (row.get("status") or "").lower() in INACTIVE_ORDER_STATUSES:
            continue
        if not row.get("expected_date"):
            continue
        days = max(0, (date.fromisoformat(str(row["expected_date"])) - today).days)
        arrivals.setdefault(row["site_id"], []).append((days, int(float(row["qty"]))))
    return arrivals


def cover_days(on_hand: float, daily_use: float, arrivals: list[tuple[int, int]] = ()) -> float:
    """Days until the site runs out, counting deliveries that land before it does."""

    if daily_use <= 0:
        return math.inf
    stock, day = float(on_hand), 0.0
    for when, units in sorted(arrivals):
        if stock - daily_use * (when - day) <= 0:
            break  # runs out before this delivery lands
        stock -= daily_use * (when - day)
        day = when
        stock += units
    return round(day + stock / daily_use, 1)


def stock_on_day(on_hand: float, daily_use: float, arrivals: list[tuple[int, int]], target_day: int) -> float:
    """Units on hand on target_day (after that day's deliveries). Never negative."""

    stock, day = float(on_hand), 0
    for when, units in sorted(arrivals):
        if when > target_day:
            break
        stock = max(0.0, stock - daily_use * (when - day)) + units
        day = when
    return max(0.0, stock - daily_use * (target_day - day))


def round_to_pack(units: float, offer: SupplierOffer) -> int:
    """Whole packs, at least the supplier's minimum."""

    packs = math.ceil(max(units, 1) / offer.pack_size)
    return max(offer.min_order_qty, packs * offer.pack_size)


# ---------------------------------------------------------
# Choosing a supplier
# ---------------------------------------------------------

def rank_offers(
    catalog: SupplierCatalog,
    din: str,
    needed_within_days: float,
    avoid: set[str] = frozenset(),
) -> list[SupplierOffer]:
    """Suppliers that can sell this drug, best first:
    1. delivers before the site runs out,
    2. reliable (on-time rate >= MIN_ON_TIME_RATE),
    3. in stock (not just limited),
    4. cheapest, then fastest.
    Backordered, inactive and avoided suppliers are left out."""

    eligible = [
        offer for offer in catalog.offers(din)
        if offer.active and offer.availability != "backorder" and offer.supplier_id not in avoid
    ]
    return sorted(eligible, key=lambda o: (
        o.lead_time_days > needed_within_days,
        o.on_time_rate < MIN_ON_TIME_RATE,
        o.availability != "in_stock",
        o.unit_cost,
        o.lead_time_days,
    ))


def describe_offer(offer: SupplierOffer, qty: int, today: date) -> dict:
    return {
        "supplier_id": offer.supplier_id,
        "supplier": offer.supplier_name,
        "unit_cost": offer.unit_cost,
        "total_cost": round(qty * offer.unit_cost, 2),
        "lead_time_days": offer.lead_time_days,
        "arrives": (today + timedelta(days=offer.lead_time_days)).isoformat(),
        "on_time_rate": offer.on_time_rate,
        "availability": offer.availability,
    }


# ---------------------------------------------------------
# 1a. Routine reorders
# ---------------------------------------------------------

def reorder_proposals(
    data,
    catalog: SupplierCatalog,
    today: date,
    pending: list[PurchaseOrder] = (),
    dins: list[str] | None = None,
) -> list[dict]:
    """Every site that will fall under SAFETY_DAYS before a new order could land.

    Counts deliveries already on the way (data.orders) and orders we already
    drafted or placed (pending), so the same need is never ordered twice.
    Returns one proposal per drug and site, with the chosen supplier, the
    alternatives and the checks it passed.
    """

    proposals = []
    for din in dins or catalog.dins():
        drug = data.drug(din)
        if drug is None:
            continue
        rows = data.stock(din)
        order_rows = data.orders(din)
        arrivals = incoming_by_site(order_rows, today)
        known = {row.get("order_id") for row in order_rows}
        for order in pending:
            if order.order_id in known:
                continue  # already counted from the data source (placed orders in the database)
            if order.din == din and order.status not in ("delivered", "cancelled"):
                lands = order.expected_date or (today + timedelta(days=_lead(catalog, order)))
                arrivals.setdefault(order.site_id, []).append((max(0, (lands - today).days), order.qty))

        for row in rows:
            daily = float(row["daily_use"])
            if daily <= 0:
                continue
            site_arrivals = arrivals.get(row["site_id"], [])
            cover = cover_days(row["on_hand"], daily, site_arrivals)
            ranked = rank_offers(catalog, din, cover)
            if not ranked:
                continue
            best = ranked[0]
            lead = best.lead_time_days
            if cover >= lead + SAFETY_DAYS:
                continue

            on_arrival = stock_on_day(row["on_hand"], daily, site_arrivals, lead)
            later = sum(units for when, units in site_arrivals if lead < when <= lead + TARGET_DAYS)
            needed = math.ceil(daily * TARGET_DAYS) - on_arrival - later
            if needed <= 0:
                continue
            qty = round_to_pack(needed, best)
            runs_out = today + timedelta(days=math.floor(cover)) if math.isfinite(cover) else None
            urgent = lead >= cover
            proposal = {
                "kind": "routine",
                "din": din,
                "drug_name": drug.get("name"),
                "site_id": row["site_id"],
                "site": row.get("site"),
                "qty": qty,
                **describe_offer(best, qty, today),
                "cover_days": cover,
                "needed_by": runs_out.isoformat() if runs_out else None,
                "urgent": urgent,
                "alternatives": [describe_offer(o, round_to_pack(needed, o), today) for o in ranked[1:3]],
                "reason": (
                    f"{row.get('site') or row['site_id']} has {cover:g} days of {drug.get('name')} "
                    f"counting deliveries on the way; a new order takes {lead} days, so it would "
                    f"drop under the {SAFETY_DAYS}-day safety stock. {qty} units tops it up to about "
                    f"{TARGET_DAYS} days after delivery."
                    + (" URGENT: it runs out before any supplier can deliver; a transfer is needed too." if urgent else "")
                ),
            }
            proposal["problems"] = check_order_line(
                OrderLine(supplier_id=best.supplier_id, site_id=row["site_id"], units=qty, unit_cost=best.unit_cost),
                din, catalog, row, runs_out_in_days=None,
            )
            proposals.append(proposal)
    return sorted(proposals, key=lambda p: (not p["urgent"], p["cover_days"]))


def _lead(catalog: SupplierCatalog, order: PurchaseOrder) -> int:
    offer = catalog.offer(order.supplier_id, order.din)
    return offer.lead_time_days if offer else 7


# ---------------------------------------------------------
# 1b. Order option for a shortage plan
# ---------------------------------------------------------

def shortage_order_option(
    din: str,
    drug_name: str | None,
    stock_rows: list[dict],
    shortfall: dict[str, int],
    order_rows: list[dict],
    catalog: SupplierCatalog,
    today: date,
) -> tuple[PlanOption | None, list[str]]:
    """An emergency order from a different supplier for every short site.

    The supplier whose order is late (any supplier with an open order for this
    drug at a short site) is avoided. Each site gets its shortfall, rounded to
    whole packs, from the best supplier that can deliver before it runs out.
    Returns (option, problems); problems is empty only if every line passes
    check_order_line.
    """

    by_site = {row["site_id"]: row for row in stock_rows}
    avoid = {row.get("supplier_id") or _supplier_key(row.get("supplier", ""))
             for row in order_rows if row["site_id"] in shortfall}
    lines, problems, notes = [], [], []

    for site_id, units in sorted(shortfall.items()):
        row = by_site[site_id]
        daily = float(row["daily_use"])
        runs_out = cover_days(row["on_hand"], daily)
        ranked = rank_offers(catalog, din, runs_out, avoid)
        if not ranked:
            problems.append(f"No other supplier sells {drug_name or din} for {site_id}.")
            continue
        best = ranked[0]
        qty = round_to_pack(units, best)
        line = OrderLine(supplier_id=best.supplier_id, site_id=site_id, units=qty, unit_cost=best.unit_cost)
        lines.append(line)
        problems.extend(check_order_line(line, din, catalog, row, runs_out_in_days=runs_out))
        notes.append(
            f"{site_id}: {qty} from {best.supplier_name} at ${best.unit_cost:,.2f}, "
            f"{best.lead_time_days} days (runs out in {runs_out:g}); "
            f"{len(ranked) - 1} other supplier(s) considered"
        )

    if not lines:
        return None, problems

    total = sum(line.units * line.unit_cost for line in lines)
    suppliers = sorted({catalog.supplier_name(line.supplier_id) for line in lines})
    option = PlanOption(
        type="order",
        description=(
            f"Emergency order from {', '.join(suppliers)}: "
            + ", ".join(f"{line.units} to {line.site_id}" for line in lines)
            + f" (about ${total:,.0f})"
        ),
        orders=lines,
        reasoning="Calculated in code from the supplier catalog. " + "; ".join(notes)
        + f". Avoided supplier(s) with the late order: {', '.join(sorted(a for a in avoid if a)) or 'none'}.",
    )
    return option, problems


def _supplier_key(name: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")


# ---------------------------------------------------------
# 2. Checks before approval
# ---------------------------------------------------------

def check_order_line(
    line: OrderLine,
    din: str,
    catalog: SupplierCatalog,
    stock_row: dict | None,
    runs_out_in_days: float | None,
) -> list[str]:
    """Hard checks on one order line. Empty list = passes."""

    problems = []
    offer = catalog.offer(line.supplier_id, din)
    if offer is None:
        return [f"{line.supplier_id} does not sell DIN {din}."]
    if not offer.active:
        problems.append(f"{offer.supplier_name} is not an active supplier.")
    if offer.availability == "backorder":
        problems.append(f"{offer.supplier_name} has DIN {din} on backorder.")
    if line.units < offer.min_order_qty:
        problems.append(f"{line.units} is under {offer.supplier_name}'s minimum of {offer.min_order_qty}.")
    if line.units % offer.pack_size:
        problems.append(f"{line.units} is not a whole number of packs of {offer.pack_size}.")
    if line.unit_cost is not None and abs(line.unit_cost - offer.unit_cost) > 0.01:
        problems.append(f"Price {line.unit_cost} does not match the catalog price {offer.unit_cost}.")
    if stock_row is None:
        problems.append(f"Site {line.site_id} has no stock record for DIN {din}.")
    else:
        daily = float(stock_row["daily_use"])
        cap = math.ceil(daily * MAX_DAYS_ON_ORDER)
        if daily > 0 and line.units > max(cap, offer.min_order_qty):
            problems.append(f"{line.units} units is more than {MAX_DAYS_ON_ORDER} days of use at {line.site_id} ({cap}).")
    if runs_out_in_days is not None and offer.lead_time_days > runs_out_in_days:
        problems.append(
            f"{offer.supplier_name} delivers in {offer.lead_time_days} days but {line.site_id} "
            f"runs out in {runs_out_in_days:g}."
        )
    return problems


def check_order_option(option: PlanOption, din: str, catalog: SupplierCatalog, stock_rows: list[dict]) -> list[str]:
    """Re-run the line checks for a whole order option (used again at approval time)."""

    if option.type != "order" or not option.orders:
        return ["Not an order option, or it has no order lines."]
    by_site = {row["site_id"]: row for row in stock_rows}
    problems = []
    for line in option.orders:
        row = by_site.get(line.site_id)
        runs_out = cover_days(row["on_hand"], float(row["daily_use"])) if row else None
        problems.extend(check_order_line(line, din, catalog, row, runs_out))
    return problems


def required_role(total_cost: float) -> str:
    return "procurement" if total_cost <= PROCUREMENT_LIMIT else "supply_manager"


def approval_problems(total_cost: float, role: str) -> list[str]:
    """Who may approve an order of this size."""

    if role not in ORDER_APPROVER_ROLES:
        return [f"A {role} cannot approve orders; needs one of {sorted(ORDER_APPROVER_ROLES)}."]
    if role == "procurement" and total_cost > PROCUREMENT_LIMIT:
        return [f"${total_cost:,.2f} is over procurement's limit of ${PROCUREMENT_LIMIT:,.0f}; "
                "the supply manager must approve it."]
    return []


# ---------------------------------------------------------
# 3. Tracking after the order is placed
# ---------------------------------------------------------

def check_reading(reading: SupplierReading, order: PurchaseOrder) -> tuple[SupplierReading, list[str]]:
    """Code checks on the reader's output. Problems -> a person reads the reply."""

    problems = []
    if reading.confidence < MIN_READING_CONFIDENCE:
        problems.append(f"Reader confidence {reading.confidence} is under {MIN_READING_CONFIDENCE}.")
    if reading.qty is not None and reading.qty > order.qty:
        problems.append(f"Reply mentions {reading.qty} units but the order is for {order.qty}.")
    if reading.kind == "partial" and not reading.qty:
        problems.append("A partial confirmation must say how many units.")
    if reading.kind == "delayed" and reading.date is None:
        problems.append("A delay must give a new date.")
    placed = order.placed_at.date() if order.placed_at else None
    if reading.date is not None and placed and reading.date < placed:
        problems.append(f"Date {reading.date} is before the order was placed ({placed}).")
    if reading.kind == "partial" and reading.qty and reading.qty >= order.qty:
        reading = reading.model_copy(update={"kind": "confirmed"})
    return reading, problems


def compute_facts(order: PurchaseOrder, stock_rows: list[dict], order_rows: list[dict],
                  snapshot: datetime, now: datetime) -> dict:
    """Everything the decision needs, calculated in code."""

    row = next((r for r in stock_rows if r["site_id"] == order.site_id), None)
    today = snapshot.date()
    elapsed = max(0.0, (now - snapshot).total_seconds() / 86400)
    others = incoming_by_site(order_rows, today, exclude={order.order_id}).get(order.site_id, [])
    if row is None:
        cover_now = None
    else:
        cover = cover_days(row["on_hand"], float(row["daily_use"]), others)
        cover_now = round(max(0.0, cover - elapsed), 1) if math.isfinite(cover) else None
    runs_out = (now.date() + timedelta(days=math.floor(cover_now))) if cover_now is not None else None
    hours_placed = round((now - order.placed_at).total_seconds() / 3600, 1) if order.placed_at else None
    eta = order.expected_date
    return {
        "status": order.status,
        "qty": order.qty,
        "confirmed_qty": order.confirmed_qty,
        "outstanding": order.qty - order.confirmed_qty if order.confirmed_qty else order.qty,
        "hours_since_placed": hours_placed,
        "acknowledged": order.status in ("in_transit", "delayed") or order.confirmed_qty > 0,
        "ack_overdue_hours": max(0.0, round(hours_placed - ACK_HOURS, 1))
        if hours_placed is not None and order.status == "placed" else 0.0,
        "expected_date": eta.isoformat() if eta else None,
        "days_past_eta": max(0, (now.date() - eta).days) if eta and order.status in ("in_transit", "delayed") else 0,
        "site_cover_days": cover_now,
        "site_runs_out": runs_out.isoformat() if runs_out else None,
        "eta_after_runout_days": (eta - runs_out).days if eta and runs_out else None,
        "chases_sent": order.chases,
        "max_chases": MAX_CHASES,
        "hours_since_chase": round((now - order.last_chased_at).total_seconds() / 3600, 1)
        if order.last_chased_at else None,
    }


def _arrives_too_late(facts: dict, eta: date | None = None) -> bool:
    if facts["site_runs_out"] is None:
        return False
    eta = eta or (date.fromisoformat(facts["expected_date"]) if facts["expected_date"] else None)
    return eta is None or eta > date.fromisoformat(facts["site_runs_out"])


def hard_rule(order: PurchaseOrder, facts: dict, reading: SupplierReading | None, flags: list[str]):
    """Cases with exactly one right answer. Returns (decision, reason) or None."""

    if order.status == "approved":
        return "place", "Approved by a person; send it to the supplier."
    if flags:
        return "escalate", "A supplier reply could not be used automatically: " + "; ".join(flags)
    if reading is not None:
        if reading.kind == "delivered":
            return "close", f"Supplier reports delivery of {reading.qty or order.qty} units."
        if reading.kind == "rejected":
            return "switch_supplier", f"Supplier cannot supply: {reading.summary}"
        if reading.kind == "question":
            return "escalate", f"Supplier asked a question a person must answer: {reading.summary}"
        if reading.kind in ("confirmed", "shipped"):
            return "accept", f"Supplier {reading.kind} the order: {reading.summary}"
        if reading.kind == "delayed" and not _arrives_too_late(facts, reading.date):
            return "record_delay", f"New date {reading.date} still lands before the site runs out."
    cover = facts["site_cover_days"]
    new_date = reading.date if reading is not None and reading.kind == "delayed" else None
    if cover is not None and cover < ESCALATE_COVER_DAYS:
        if _arrives_too_late(facts, new_date):
            due = new_date.isoformat() if new_date else (facts["expected_date"] or "no date yet")
            return "escalate", (f"{order.site_id} has {cover} days left and this order (due {due}) "
                                "will not arrive in time.")
        if new_date is None and facts["days_past_eta"] >= 1:
            # Its date has passed without delivery, so the date can no longer be trusted.
            return "escalate", (f"{order.site_id} has {cover} days left and this order was due "
                                f"{facts['expected_date']} but has not arrived.")
    overdue = facts["ack_overdue_hours"] > 0 or facts["days_past_eta"] >= 1
    if reading is None and overdue and order.chases >= MAX_CHASES:
        return "escalate", f"No answer after {order.chases} reminders."
    return None


def needs_attention(order: PurchaseOrder, facts: dict, reading: SupplierReading | None) -> bool:
    if reading is not None and reading.kind != "unrelated":
        return True
    overdue = facts["ack_overdue_hours"] > 0 or facts["days_past_eta"] >= 1
    if not overdue:
        return False
    since = facts["hours_since_chase"]
    return since is None or since >= CHASE_COOLDOWN_HOURS


def allowed_decisions(order: PurchaseOrder, facts: dict, reading: SupplierReading | None) -> set[str]:
    """Grey zone: the choices the decision agent may pick from."""

    if reading is not None and reading.kind == "partial":
        return {"accept", "switch_supplier"}
    if reading is not None and reading.kind == "delayed":
        return {"record_delay", "switch_supplier", "escalate"}
    if facts["days_past_eta"] >= 1:
        return {"chase", "escalate"}
    return {"wait", "chase"}


def fallback_decision(order: PurchaseOrder, facts: dict, reading: SupplierReading | None,
                      allowed: set[str], has_alternative: bool) -> tuple[str, str]:
    """What we do when no model is available or the model failed."""

    if reading is not None and reading.kind == "partial":
        if has_alternative and "switch_supplier" in allowed:
            return "switch_supplier", (f"Supplier confirmed only {reading.qty} of {order.qty}; "
                                       "order the rest from another supplier.")
        return "accept", f"Supplier confirmed {reading.qty} of {order.qty}; no other supplier available."
    if reading is not None and reading.kind == "delayed":
        if has_alternative:
            return "switch_supplier", f"New date {reading.date} is after the site runs out; another supplier can deliver sooner."
        return "record_delay", f"New date {reading.date} is after the site runs out and no supplier is faster; raise a shortage event."
    if "chase" in allowed:
        if facts["days_past_eta"] >= 1:
            return "chase", f"Due {facts['expected_date']} and not delivered yet; send a reminder."
        return "chase", "No answer from the supplier yet; send a reminder."
    return "wait", "Nothing to do yet."


def recipient_for(decision: str, order: PurchaseOrder, catalog: SupplierCatalog) -> str | None:
    """Code, not the model, decides who a message goes to."""

    if decision in ("chase", "place"):
        offer = catalog.offer(order.supplier_id, order.din)
        return (offer.contact if offer and offer.contact else None) or catalog.supplier_name(order.supplier_id)
    if decision == "escalate":
        return order.approved_role or "procurement"
    if decision == "switch_supplier":
        return "procurement"
    return None


def purchase_order_message(order: PurchaseOrder, catalog: SupplierCatalog) -> str:
    """The order document itself. Always a template: no model writes an order."""

    return (
        f"PURCHASE ORDER {order.order_id}\n"
        f"To: {catalog.supplier_name(order.supplier_id)}\n"
        f"Drug: {order.drug_name or ''} (DIN {order.din})\n"
        f"Quantity: {order.qty} at ${order.unit_cost:,.2f} = ${order.total_cost:,.2f}\n"
        f"Deliver to: site {order.site_id}\n"
        f"Requested delivery: {order.expected_date.isoformat() if order.expected_date else 'as soon as possible'}\n"
        f"Approved by: {order.approved_by} ({order.approved_role})\n"
        "Please confirm quantity and delivery date by reply."
    )


def template_message(decision: str, order: PurchaseOrder, facts: dict, catalog: SupplierCatalog) -> str | None:
    supplier = catalog.supplier_name(order.supplier_id)
    if decision == "chase":
        if facts["days_past_eta"]:
            return (f"Order {order.order_id} ({order.qty} x {order.drug_name or order.din}) was due "
                    f"{facts['expected_date']} and has not arrived. Please send a delivery update today.")
        return (f"We have not had a confirmation for order {order.order_id} ({order.qty} x "
                f"{order.drug_name or order.din}, placed {facts['hours_since_placed']} hours ago). "
                "Please confirm quantity and delivery date.")
    if decision == "escalate":
        return (f"Order {order.order_id} from {supplier} needs a person: {order.qty} x "
                f"{order.drug_name or order.din} for site {order.site_id}. Site cover "
                f"{facts['site_cover_days']} days, expected {order.expected_date or 'unknown'}, "
                f"{order.chases} reminder(s) sent.")
    if decision == "switch_supplier":
        return (f"Replacement needed for order {order.order_id} ({supplier}). A new draft order is "
                "waiting for your approval.")
    return None


def replacement_order(order: PurchaseOrder, catalog: SupplierCatalog, units: int, now: datetime,
                      runs_out_in_days: float | None, new_id: str,
                      avoid: set[str] = frozenset()) -> PurchaseOrder | None:
    """Draft an order for `units` from the next-best supplier. Needs approval again."""

    ranked = rank_offers(catalog, order.din, runs_out_in_days if runs_out_in_days is not None else math.inf,
                         avoid | {order.supplier_id})
    if not ranked:
        return None
    best = ranked[0]
    qty = round_to_pack(units, best)
    return PurchaseOrder(
        order_id=new_id,
        kind=order.kind,
        din=order.din,
        drug_name=order.drug_name,
        site_id=order.site_id,
        supplier_id=best.supplier_id,
        qty=qty,
        unit_cost=best.unit_cost,
        status="pending_approval",
        created_at=now,
        needed_by=order.needed_by,
        plan_id=order.plan_id,
        event_id=order.event_id,
        replaces=order.order_id,
        reason=f"Replaces {order.order_id} from {catalog.supplier_name(order.supplier_id)}: {units} units still needed.",
    )


def has_faster_alternative(order: PurchaseOrder, catalog: SupplierCatalog, runs_out_in_days: float | None,
                           avoid: set[str] = frozenset()) -> bool:
    ranked = rank_offers(catalog, order.din, runs_out_in_days if runs_out_in_days is not None else math.inf,
                         avoid | {order.supplier_id})
    if not ranked:
        return False
    if runs_out_in_days is None:
        return True
    return ranked[0].lead_time_days <= runs_out_in_days
