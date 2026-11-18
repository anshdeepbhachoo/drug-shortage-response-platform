"""Order options: buy more from a supplier, worked out and checked in code.

A transfer moves stock that is already in the network; an order brings in new
stock. Everything about an order is a calculation, so no model is involved:

- supplier:  one that has supplied this drug before (data.suppliers), preferring
             one that has supplied that hospital;
- quantity:  each at-risk hospital's shortfall (what it needs to last its cover days);
- arrival:   today plus the standard lead time, plus the reported supplier delay
             when the alert is a supplier delay (the drug's suppliers are the problem).

The option is only offered when every at-risk hospital still has stock on the
day its order arrives. check_order re-checks all of it, and approving an order
records an approved purchase order: nothing is sent to a supplier from here.

ORDER_LEAD_DAYS is an assumption (the synthetic data has no supplier lead
times); the supplier's confirmed date replaces it once replies are read.
"""

from __future__ import annotations

import math
import os
from datetime import date, timedelta

from services.agents.schemas import OrderLine, PlanOption, PlanSet

ORDER_LEAD_DAYS = int(os.environ.get("ORDER_LEAD_DAYS", "5"))
# An order may not leave a hospital with more than this many days of supply (no stockpiling).
MAX_ORDER_DAYS = 60


def arrival_days(delay_days: int, restore: bool, lead_days: int = ORDER_LEAD_DAYS) -> int:
    """Days until a new order arrives. A supplier delay applies to new orders too."""

    return lead_days + (0 if restore else max(0, delay_days))


def _days_of_supply(row: dict) -> float:
    return row["on_hand"] / row["daily_use"] if row["daily_use"] > 0 else math.inf


def _pick_supplier(suppliers: list[dict], site_id: str) -> dict | None:
    """The supplier that has supplied this hospital, else the drug's most used supplier."""

    if not suppliers:
        return None
    for_site = [s for s in suppliers if s["site_id"] == site_id]
    pool = for_site or suppliers
    counts: dict[str, int] = {}
    for s in pool:
        counts[s["supplier_id"]] = counts.get(s["supplier_id"], 0) + 1
    best = sorted(counts, key=lambda sid: (-counts[sid], sid))[0]
    return next(s for s in pool if s["supplier_id"] == best)


def order_option(*, stock_rows: list[dict], shortfall: dict, suppliers: list[dict], today: date,
                 delay_days: int, restore: bool, lead_days: int = ORDER_LEAD_DAYS) -> PlanOption | None:
    """An order covering every at-risk hospital, or None when no order would arrive in time."""

    if not shortfall or not suppliers:
        return None
    stock = {row["site_id"]: row for row in stock_rows}
    days = arrival_days(delay_days, restore, lead_days)
    expected = today + timedelta(days=days)

    lines = []
    for site_id in sorted(shortfall):
        row = stock.get(site_id)
        supplier = _pick_supplier(suppliers, site_id)
        if row is None or supplier is None or _days_of_supply(row) < days:
            return None  # this hospital runs out before any order could arrive
        lines.append(OrderLine(site_id=site_id, supplier_id=supplier["supplier_id"], supplier=supplier["supplier"],
                               units=shortfall[site_id], expected_date=expected.isoformat()))

    names = {row["site_id"]: row.get("site") or row["site_id"] for row in stock_rows}
    by_supplier: dict[str, list[OrderLine]] = {}
    for line in lines:
        by_supplier.setdefault(line.supplier, []).append(line)
    parts = [f"{', '.join(f'{l.units} for {names[l.site_id]}' for l in ls)} from {name}" for name, ls in by_supplier.items()]
    delay_note = "" if restore or not delay_days else f", plus the reported {delay_days}-day supplier delay"
    # No date in the description: the lead time starts at approval, so the date shown moves with the day.
    return PlanOption(
        type="order",
        description=f"Order {'; '.join(parts)}",
        order_lines=lines,
        reasoning=(f"Order rules: each hospital's shortfall, from a supplier that has supplied this drug "
                   f"before, expected {days} days after approval (standard {lead_days}-day lead time{delay_note}). "
                   f"Every hospital still has stock when it arrives. The date is unconfirmed until the supplier "
                   f"confirms it."),
    )


def with_order_option(plan: PlanSet, option: PlanOption | None) -> PlanSet:
    """The plan with the code's order option placed after its transfers (planner order options are dropped:
    orders come from code only)."""

    options = [o for o in plan.options if o.type != "order"]
    if option is not None:
        at = sum(1 for o in options if o.type == "transfer")
        options = options[:at] + [option] + options[at:]
    return PlanSet(options=options[:4], no_perfect_plan=plan.no_perfect_plan)


def check_order(option: PlanOption, stock: dict, shortfall: dict, suppliers: list[dict], today: date,
                min_arrival_days: int) -> list[str]:
    """Empty only when the order passes every check.

    stock:            {"A": {"on_hand": 100, "daily_use": 2.5}}
    shortfall:        {"A": 30}
    suppliers:        data.suppliers(din): who has supplied this drug
    min_arrival_days: the earliest an order can arrive (arrival_days); an earlier date is optimistic
    """

    if option.type != "order":
        return ["Only order options can be verified by this function."]
    if not option.order_lines:
        return ["Order option contains no order lines."]

    problems = []
    known = {s["supplier_id"] for s in suppliers}
    seen = set()
    for line in option.order_lines:
        site_id = line.site_id
        if site_id in seen:
            problems.append(f"{site_id} has more than one order line.")
            continue
        seen.add(site_id)
        if site_id not in stock:
            problems.append(f"Unknown site: {site_id}")
            continue
        if site_id not in shortfall:
            problems.append(f"{site_id} is not listed as requiring stock.")
            continue
        if line.supplier_id not in known:
            problems.append(f"{line.supplier} has never supplied this drug.")

        row = stock[site_id]
        need = shortfall[site_id]
        if line.units < need:
            problems.append(f"{site_id} orders {line.units} units but needs {need}.")
        cap = max(need, math.ceil(row["daily_use"] * MAX_ORDER_DAYS) - row["on_hand"])
        if line.units > cap:
            problems.append(f"{site_id} would order {line.units} units, more than {MAX_ORDER_DAYS} days of supply.")

        try:
            arrives_in = (date.fromisoformat(line.expected_date) - today).days
        except ValueError:
            problems.append(f"{site_id} has no valid expected date.")
            continue
        if arrives_in < min_arrival_days:
            problems.append(f"{site_id}'s order is expected in {arrives_in} days; the earliest possible is {min_arrival_days}.")
        if _days_of_supply(row) < arrives_in:
            problems.append(f"{site_id} runs out in {_days_of_supply(row):.1f} days, before its order arrives in {arrives_in}.")

    for site_id in shortfall:
        if site_id not in seen:
            problems.append(f"{site_id} needs {shortfall[site_id]} units but has no order line.")
    return problems
