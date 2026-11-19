"""Order follow-up rules (code: every number lives here): facts about an approved order, the hard rules,
the decisions the order agent may choose from, the fallback, who receives what, and the message
templates and checks. The order agent (services/agents/orders/agent.py) only chooses within these."""

from __future__ import annotations

import math
import re
from datetime import datetime, time, timedelta

from services.agents.orders.replies import CANNOT_SUPPLY
from services.agents.orders.schemas import RequestDraft, WatchedOrder
from services.rules.followup_rules import ESCALATE_COVER_DAYS, project_stock

ACTOR = "order-agent"

# Thresholds (demo defaults; agree real values with procurement)
PLACE_WITHIN_HOURS = 24     # an approved order should be placed with the supplier within this
TIGHT_MARGIN_DAYS = 2       # stock outlasting the order's arrival by less than this is tight
MAX_NUDGES = 2
NUDGE_COOLDOWN_HOURS = 12
FINAL = {"close", "escalate", "replan"}        # after these the agent stops watching the order

PLACED = {"placed", "in_transit", "delayed"}
ORDER_ROLES = {"procurement", "supply_manager"}  # who may record what happened to an order
# The status a person may record next, from each status.
NEXT_STATUS = {"approved": {"placed", "cancelled"}, "placed": {"in_transit", "delivered", "delayed", "cancelled"},
               "in_transit": {"delivered", "delayed", "cancelled"}, "delayed": {"in_transit", "delivered", "cancelled"}}


# ---------------------------------------------------------
# Facts and rules (code: every number lives here)
# ---------------------------------------------------------

def order_facts(order: WatchedOrder, stock_rows, snapshot: datetime, now: datetime, nudges_sent: int,
                last_nudged_at: datetime | None, supplier: dict | None = None, bridge_units: int = 0) -> dict:
    """The numbers a decision is based on. The order's own units are not counted until it arrives.

    supplier: what the supplier's replies said so far, from code-checked readings:
              {"confirmed": bool, "reply": latest kind or None, "units_confirmed": int or None}
    bridge_units: units the same plan's approved transfer brings to this hospital (a combined transfer and
              order). Counted as arriving: the follow-up agent watches that transfer.
    """

    supplier = supplier or {}
    confirmed = bool(supplier.get("confirmed"))

    site = dict(project_stock(stock_rows, snapshot, now)[order.site_id])
    site["on_hand"] += bridge_units
    cover = round(site["on_hand"] / site["daily_use"], 2) if site["daily_use"] > 0 else math.inf
    # Due by the end of its expected day: an order is late only once that day is over.
    due = datetime.combine(order.expected_date + timedelta(days=1), time())
    days_to_arrival = round((due - now).total_seconds() / 86400, 2)
    hours_since_approval = round((now - order.approved_at).total_seconds() / 3600, 1)
    return {
        "now": now.isoformat(),
        "order_status": order.status,
        "qty": order.qty,
        # A supplier that confirmed the order has it, whatever procurement has recorded so far.
        "placed": order.status in PLACED or confirmed,
        "supplier_confirmed": confirmed,
        "supplier_reply": supplier.get("reply"),
        "units_confirmed": supplier.get("units_confirmed"),
        "delivered": order.status == "delivered",
        "cancelled": order.status == "cancelled",
        "hours_since_approval": hours_since_approval,
        "unplaced_too_long": order.status == "approved" and not confirmed and hours_since_approval > PLACE_WITHIN_HOURS,
        "expected_date": order.expected_date.isoformat(),
        "days_to_arrival": days_to_arrival,
        "overdue": days_to_arrival < 0 and order.status != "delivered",
        "site": order.site_id,
        "bridge_units": bridge_units,
        "site_cover_days": cover,
        # How long the hospital's stock outlasts the order's arrival. Negative: it runs out first.
        "margin_days": round(cover - max(0.0, days_to_arrival), 2) if cover != math.inf else math.inf,
        "nudges_sent": nudges_sent,
        "hours_since_last_nudge": round((now - last_nudged_at).total_seconds() / 3600, 1) if last_nudged_at else None,
    }


def _replan_or_escalate(facts: dict, why: str) -> tuple[str, str]:
    if facts["site_cover_days"] < ESCALATE_COVER_DAYS:
        return "escalate", f"{why} {facts['site']} has {facts['site_cover_days']} days of stock, too soon to replan. A person must act."
    return "replan", f"{why} Find another way to cover the gap."


def hard_rule(facts: dict, flags: list[str] = ()) -> tuple[str, str] | None:
    """(decision, reason) when the outcome is not a judgement call, else None.

    flags: problems with the supplier's replies (security screen, unreadable, failed checks, a question)."""

    if facts["delivered"]:
        return "close", "The order was received."
    if facts["cancelled"]:
        return "replan", "The order was cancelled; the gap needs another response."
    if flags:
        return "escalate", f"A supplier reply needs a person: {'; '.join(flags)}"
    reply = facts.get("supplier_reply")
    if reply in CANNOT_SUPPLY:
        return _replan_or_escalate(facts, f"The supplier says the order is {reply}.")
    if reply == "partial":
        missing = facts["qty"] - (facts["units_confirmed"] or 0)
        # A replan would order the whole gap again; a person decides how to cover the missing units.
        return "escalate", (f"The supplier confirmed {facts['units_confirmed']} of {facts['qty']} units. "
                            f"A person decides how to cover the other {missing}.")
    if facts["margin_days"] < 0:
        if facts["site_cover_days"] < ESCALATE_COVER_DAYS:
            return "escalate", (f"{facts['site']} runs out in {facts['site_cover_days']} days, before the order arrives "
                                f"in {facts['days_to_arrival']}, and too soon to replan. A person must act.")
        return "replan", (f"{facts['site']} runs out in {facts['site_cover_days']} days, before the order arrives in "
                          f"{facts['days_to_arrival']}. Find another way to cover the gap.")
    return None


def needs_attention(facts: dict) -> bool:
    return facts["unplaced_too_long"] or facts["overdue"] or facts["margin_days"] < TIGHT_MARGIN_DAYS


def allowed_decisions(facts: dict) -> set[str]:
    """Decisions the model may choose from in a grey zone."""

    nudge_ok = facts["nudges_sent"] < MAX_NUDGES and not (
        facts["hours_since_last_nudge"] is not None and facts["hours_since_last_nudge"] < NUDGE_COOLDOWN_HOURS)
    allowed = set()
    if facts["unplaced_too_long"] or facts["overdue"]:
        if nudge_ok:
            allowed.add("nudge")
        if facts["overdue"]:
            allowed.add("replan")
    if facts["margin_days"] < TIGHT_MARGIN_DAYS or facts["nudges_sent"] >= MAX_NUDGES:
        allowed.add("escalate")
    if not facts["overdue"] and not facts["unplaced_too_long"]:
        allowed.add("wait")  # only a tight margin: watching is still safe
    return allowed or {"escalate"}


def fallback_decision(facts: dict, allowed: set[str]) -> tuple[str, str]:
    """Rule-based choice when no model is available or its answer failed the checks."""

    if facts["unplaced_too_long"] and "nudge" in allowed:
        return "nudge", f"Approved {facts['hours_since_approval']} hours ago and not placed yet; remind procurement."
    if facts["overdue"] and "nudge" in allowed:
        return "nudge", f"Expected {facts['expected_date']} and not received; ask the supplier for a date."
    if "wait" in allowed:
        return "wait", f"{facts['site']} lasts {facts['margin_days']} days past the expected arrival; keep watching."
    if facts["overdue"] and "replan" in allowed and facts["margin_days"] >= TIGHT_MARGIN_DAYS:
        return "replan", "Reminders have not produced a delivery; find another way to cover the gap."
    return "escalate", "Reminders have run out or the margin is tight; a person must decide."


# Who acts on an order problem. The approver handed the order to procurement, who own the supplier: placing it,
# answering the supplier, a reply that needs reading. A problem with the stock itself (the hospital runs out
# before the order arrives, the supplier can't send it all) needs a new plan: that is the supply manager's.
SUPPLY_PROBLEMS = {"partial", "backordered", "cancelled"}


def escalation_role(facts: dict) -> str:
    stock = facts.get("supplier_reply") in SUPPLY_PROBLEMS or facts["margin_days"] < 0 or facts["cancelled"]
    return "supply_manager" if stock else "procurement"


def recipient_for(decision: str, order: WatchedOrder, facts: dict) -> str | None:
    """Code, not the model, decides who a message goes to."""

    if decision == "nudge":
        return order.supplier if facts["placed"] else "procurement"   # procurement places orders, whoever approved
    if decision == "escalate":
        return escalation_role(facts)
    return None


def template_message(decision: str, order: WatchedOrder, facts: dict, drug: str, site: str) -> str | None:
    if decision == "nudge" and not facts["placed"]:
        return (f"Reminder: the order of {order.qty} units of {drug} for {site} from {order.supplier} was approved "
                f"{facts['hours_since_approval']:.0f} hours ago and is not marked as placed. Please place it, or record why not.")
    if decision == "nudge":
        return (f"Hello {order.supplier}, our order of {order.qty} units of {drug} for {site} was expected on "
                f"{order.expected_date:%b %d}. Please confirm a delivery date.")
    if decision == "escalate":
        return (f"Escalation: the order of {order.qty} units of {drug} for {site} ({order.supplier}, status "
                f"{order.status}, expected {order.expected_date:%b %d}). {site} has about {facts['site_cover_days']} "
                f"days of stock. A person must decide.")
    return None


def template_request(order: WatchedOrder, drug: str, site: str) -> RequestDraft:
    return RequestDraft(
        subject=f"Purchase order {order.order_id}: {order.qty} units of {drug}",
        body=(f"Hello {order.supplier},\n\nPlease supply {order.qty} units of {drug} (DIN {order.din}), delivered to "
              f"{site} by {order.expected_date:%B %d}. Reference {order.order_id}.\n\nPlease confirm availability, price "
              f"and the delivery date."),
    )


def check_request(draft: RequestDraft, order: WatchedOrder) -> list[str]:
    """The drafted request must state the approved quantity and the order reference."""

    text_ = f"{draft.subject}\n{draft.body}"
    problems = []
    numbers = {int(n.replace(",", "")) for n in re.findall(r"(?<![\w-])\d[\d,]*(?![\w-])", text_)}
    if order.qty not in numbers:
        problems.append(f"The request must state the approved quantity, {order.qty} units.")
    if order.order_id not in text_:
        problems.append(f"The request must quote the order reference {order.order_id}.")
    return problems
