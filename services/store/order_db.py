"""The order agent on the database: approved orders to watch, their history from audit_log, drafting
requests, the evening follow-up run, and people recording what happened to an order.

  python -m services.store.order_db --now 2026-11-16T18:00 --as-of 2026-11-16
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.agents.bedrock import resolve_model
from services.agents.followup.schemas import FollowUpAction
from services.agents.model_io import LLM
from services.agents.orders.agent import OrderAgent
from services.agents.orders.replies import OrderReply, OrderReplyReading, scenario_replies
from services.agents.orders.schemas import WatchedOrder
from services.rules.order_rules import ACTOR, FINAL, NEXT_STATUS, ORDER_ROLES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------
# Database: orders to watch, their history, recording
# ---------------------------------------------------------

def _naive_utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def approved_orders(engine: Engine, plan_id: str | None = None) -> list[WatchedOrder]:
    """Purchase orders that came from an approved plan option (or only plan_id's)."""

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT o.order_id, o.plan_id, o.din, o.site_id, o.supplier_id, s.name AS supplier, o.qty, o.status,
                   o.expected_date, d.decided_at, d.actor_role
            FROM purchase_order o
            JOIN supplier s USING (supplier_id)
            JOIN decision d ON d.plan_id = o.plan_id AND d.decision = 'approved'
            WHERE o.plan_id IS NOT NULL AND (CAST(:plan_id AS text) IS NULL OR o.plan_id = :plan_id)
            ORDER BY d.decided_at, o.order_id
        """), {"plan_id": plan_id}).all()
    return [WatchedOrder(order_id=r.order_id, plan_id=r.plan_id, din=r.din, site_id=r.site_id, supplier_id=r.supplier_id,
                         supplier=r.supplier, qty=r.qty, status=r.status, expected_date=r.expected_date,
                         approved_at=_naive_utc(r.decided_at), approver_role=r.actor_role) for r in rows]


def history(engine: Engine, order_ids: list[str]) -> dict[str, list[dict]]:
    """Earlier order-agent records per order (request drafts and checks), oldest first."""

    result: dict[str, list[dict]] = {order_id: [] for order_id in order_ids}
    if not order_ids:
        return result
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT object, detail FROM audit_log
            WHERE actor = :actor AND object = ANY(CAST(:ids AS text[])) AND action <> 'Supplier date recorded'
            ORDER BY audit_id
        """), {"actor": ACTOR, "ids": order_ids}).all()
    for row in rows:
        result[row.object].append(row.detail)
    return result


def audit_writer(engine: Engine, orders: dict[str, WatchedOrder]):
    def write(record: dict, action: str) -> None:
        order_id = record.get("request_id") or record["order_id"]
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
                VALUES (:actor, :actor_type, :action, :object, CAST(:detail AS jsonb), :refs)
            """), {"actor": ACTOR, "actor_type": "agent" if record["decided_by"] == "agent" else "rule",
                   "action": action, "object": order_id, "detail": json.dumps(record, default=str),
                   "refs": [order_id, orders[order_id].plan_id]})
    return write


def _snapshot(engine: Engine, data) -> datetime:
    from services.store.followup_db import stock_snapshot_time

    return stock_snapshot_time(engine, data)


def draft_requests(engine: Engine, data, now: datetime, llm: LLM | None = None, plan_id: str | None = None) -> list[dict]:
    """Draft the supplier request for every approved order that has none yet. Returns the new drafts."""

    orders = {o.order_id: o for o in approved_orders(engine, plan_id)}
    past = history(engine, list(orders))
    agent = OrderAgent(data, _snapshot(engine, data), llm, audit_writer(engine, orders))
    return [agent.draft_request(order, _naive_utc(now)) for order_id, order in orders.items()
            if not any(r.get("kind") == "request" for r in past[order_id])]


def bridge_units(engine: Engine, orders) -> dict[str, int]:
    """Per order: units the same approved plan transfers to its hospital (a combined transfer and order),
    unless the follow-up agent replanned that transfer (it failed) or closed it (received, so in the stock)."""

    result = {}
    with engine.connect() as conn:
        for order in orders:
            result[order.order_id] = conn.execute(text("""
                SELECT COALESCE(sum(a.units), 0) FROM plan_action a
                JOIN decision d ON d.plan_id = a.plan_id AND d.option_index = a.option_index AND d.decision = 'approved'
                WHERE a.plan_id = :p AND a.type = 'transfer' AND a.to_site = :s
                  AND NOT EXISTS (SELECT 1 FROM audit_log l WHERE l.actor = 'follow-up-agent' AND l.object = a.action_id
                                  AND l.detail->>'decision' IN ('replan', 'close'))
            """), {"p": order.plan_id, "s": order.site_id}).scalar_one()
    return result


def date_writer(engine: Engine):
    """Records a supplier's code-checked delivery date on the purchase order, with an audit entry."""

    def update(order: WatchedOrder, new_date: date, reply: OrderReply, reading: OrderReplyReading, read_by: str) -> None:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE purchase_order SET expected_date = :d, promised_date = :d WHERE order_id = :o
            """), {"d": new_date, "o": order.order_id})
            conn.execute(text("""
                INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
                VALUES (:actor, :actor_type, 'Supplier date recorded', :object, CAST(:detail AS jsonb), :refs)
            """), {"actor": ACTOR, "actor_type": read_by,
                   "object": order.order_id, "refs": [order.order_id, order.plan_id],
                   "detail": json.dumps({"from": order.expected_date.isoformat(), "to": new_date.isoformat(),
                                         "reply_id": reply.reply_id, "sender": reply.sender, "kind": reading.kind})})
    return update


def run_order_followup(engine: Engine, data, now: datetime, llm: LLM | None = None, plan_id: str | None = None,
                       replies: dict[str, list[OrderReply]] | None = None) -> list[FollowUpAction]:
    """Check every approved order still being watched at `now` (or only plan_id's). Returns this run's checks.

    replies: {order_id: [OrderReply]}; default the synthetic replies (order_replies.scenario_replies).
    Each reply is read once, at the first check after it arrives."""

    from services.agents.orders.placement.orchestrator import submit_orders

    now = _naive_utc(now)
    draft_requests(engine, data, now, llm, plan_id)  # orders approved before drafting existed still get one
    submit_orders(engine, data, now, plan_id, llm)   # and any approved order the orchestrator has not placed yet
    orders = {o.order_id: o for o in approved_orders(engine, plan_id)}
    past = history(engine, list(orders))
    agent = OrderAgent(data, _snapshot(engine, data), llm, audit_writer(engine, orders), date_writer(engine))
    if replies is None:
        names = {}
        for order_id, order in orders.items():
            drug, site = agent._names(order)
            names[order_id] = {"drug": drug, "site": site}
        replies = scenario_replies(orders.values(), names)
    bridges = bridge_units(engine, orders.values())
    actions = []
    for order_id, order in orders.items():
        checks = [r for r in past[order_id] if r.get("kind") != "request"]
        if any(r["decision"] in FINAL for r in checks):
            continue
        if checks and datetime.fromisoformat(checks[-1]["at"]) >= now:
            continue  # already checked at this time
        nudges = [r for r in checks if r["decision"] == "nudge"]
        read = {entry["reply_id"] for r in checks for entry in r.get("replies") or []}
        new = [reply for reply in replies.get(order_id, []) if reply.received_at <= now and reply.reply_id not in read]
        supplier = next((r["supplier"] for r in reversed(checks) if r.get("supplier")), {})
        actions.append(agent.check(order, now, len(nudges), datetime.fromisoformat(nudges[-1]["at"]) if nudges else None,
                                   new, supplier, bridges.get(order_id, 0)))
    return actions


class OrderStatusError(Exception):
    """The status change is not allowed. Nothing was written."""


def record_order_status(engine: Engine, order_id: str, status: str, by: str, role: str,
                        expected_date: date | None = None, at: datetime | None = None, note: str | None = None) -> dict:
    """A person records what happened to an approved order: placed (optionally with the supplier's
    confirmed date), in transit, delayed, received (delivered) or cancelled. Audited; nothing is sent."""

    if not by.strip():
        raise OrderStatusError("Recording an order status needs the person's name.")
    if role not in ORDER_ROLES:
        raise OrderStatusError(f"A {role} cannot record order status; needs one of {sorted(ORDER_ROLES)}.")
    with engine.begin() as conn:
        order = conn.execute(text("""
            SELECT order_id, plan_id, status, expected_date FROM purchase_order
            WHERE order_id = :o AND plan_id IS NOT NULL FOR UPDATE
        """), {"o": order_id}).one_or_none()
        if order is None:
            raise OrderStatusError(f"Unknown platform order {order_id}.")
        if status not in NEXT_STATUS.get(order.status, set()):
            raise OrderStatusError(f"Order {order_id} is {order.status}; it cannot become {status}.")
        when = (at or datetime.now(timezone.utc)).date()
        conn.execute(text("""
            UPDATE purchase_order
            SET status = :status,
                promised_date = CASE WHEN CAST(:expected AS date) IS NOT NULL THEN CAST(:expected AS date) ELSE promised_date END,
                expected_date = COALESCE(CAST(:expected AS date), expected_date),
                delivered_at = CASE WHEN :status = 'delivered' THEN CAST(:when AS date) ELSE delivered_at END
            WHERE order_id = :o
        """), {"status": status, "expected": expected_date, "when": when, "o": order_id})
        word = {"placed": "Order placed", "in_transit": "Order in transit", "delayed": "Order delayed",
                "delivered": "Order received", "cancelled": "Order cancelled"}[status]
        detail = {"role": role, "from": order.status, "to": status, "note": note,
                  "expected_date": (expected_date or order.expected_date).isoformat()}
        audit_id = conn.execute(text("""
            INSERT INTO audit_log (actor, actor_type, action, object, detail, refs, at)
            VALUES (:actor, 'human', :action, :object, CAST(:detail AS jsonb), :refs, COALESCE(:at, now()))
            RETURNING audit_id
        """), {"actor": by, "action": word, "object": order_id, "detail": json.dumps(detail),
               "refs": [order_id, order.plan_id], "at": at}).scalar_one()
    return {"order_id": order_id, "status": status, "audit_id": audit_id, **detail}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Follow up approved purchase orders")
    parser.add_argument("--now", type=datetime.fromisoformat, default=None, help="Check time (default: now, UTC)")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Stock day to project from (default: latest)")
    parser.add_argument("--live", action="store_true", help="Use Bedrock for wording and grey-zone decisions")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ingestion.lineage import get_engine
    from services.data.db_data import PostgresData

    llm = resolve_model(None) if args.live else None
    actions = run_order_followup(get_engine(), PostgresData.from_env(as_of=args.as_of),
                                 args.now or datetime.now(timezone.utc), llm)
    for action in actions:
        print(f"{action.request_id}: {action.decision.upper()} by {action.decided_by} | {action.reason}")
        if action.message_draft:
            print(f"    draft to {action.message_to}: {action.message_draft}")
    print("Nothing was sent or placed. Every nudge, replan and escalation waits for a person.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
