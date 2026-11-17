"""What the website's Orders page shows, built from the order agent.

Used by both the offline export (order_demo --export -> apps/web/src/data/orders.json)
and the platform API (apps/api/main.py, GET /api/orders, reading Postgres), so
the page looks the same whichever it reads.
"""

from __future__ import annotations

import math
from datetime import datetime

from services.agents.order_agent import OrderAgent
from services.agents.order_rules import PROCUREMENT_LIMIT, cover_days, incoming_by_site

# Audit actions that are events on an order (not agent checks).
EVENT_ACTIONS = {
    "Order drafted": "drafted",
    "Order created from approved plan": "created",
    "Order approved": "approved",
    "Order rejected": "rejected",
}


def order_rows(agent: OrderAgent) -> tuple[list[dict], dict[str, str]]:
    """Every order the agent manages, plus site names, in the page's shape."""

    today = agent.snapshot.date()
    stock: dict[str, list[dict]] = {}
    sites: dict[str, str] = {}
    rows = []
    for o in agent.orders.values():
        if o.din not in stock:
            stock[o.din] = agent.data.stock(o.din)
            sites.update({r["site_id"]: r.get("site") or r["site_id"] for r in stock[o.din]})
        row = next((r for r in stock[o.din] if r["site_id"] == o.site_id), None)
        days = None
        if row is not None:
            arrivals = incoming_by_site(agent.data.orders(o.din), today, exclude={o.order_id}).get(o.site_id, [])
            left = cover_days(row["on_hand"], row["daily_use"], arrivals)
            days = None if math.isinf(left) else round(left, 1)
        rows.append({
            "id": o.order_id, "kind": o.kind, "drug": o.drug_name or o.din, "din": o.din,
            "site": o.site_id, "siteName": sites.get(o.site_id, o.site_id),
            "supplier": o.supplier_id, "supplierName": agent.catalog.supplier_name(o.supplier_id),
            "qty": o.qty, "confirmedQty": o.confirmed_qty, "receivedQty": o.received_qty,
            "unitCost": o.unit_cost, "totalCost": round(o.total_cost, 2), "status": o.status,
            "neededBy": str(o.needed_by) if o.needed_by else None,
            "expected": str(o.expected_date) if o.expected_date else None,
            "approvedBy": o.approved_by, "approvedRole": o.approved_role,
            "replaces": o.replaces, "planId": o.plan_id, "reason": o.reason,
            "urgent": "URGENT" in (o.reason or ""), "daysLeft": days,
            "chases": o.chases, "escalated": o.escalated_at is not None,
            # For orders not sent yet: how long the supplier takes, so the page can show when it would arrive.
            "leadDays": offer.lead_time_days if (offer := agent.catalog.offer(o.supplier_id, o.din)) else None,
        })
    return rows, sites


def event_from_record(record: dict) -> dict | None:
    """One audit record (as OrderAgent writes it) -> one timeline entry, or None to skip it."""

    if record.get("kind") == "check":
        if record.get("decision") == "wait":
            return None
        return {
            "at": record["at"], "order": record["order_id"], "who": record["decided_by"],
            "action": record["decision"], "text": record["reason"],
            "statusBefore": record.get("status_before"), "statusAfter": record.get("status_after"),
            "to": record.get("message_to"), "message": record.get("message_draft"),
            "sentVia": record.get("sent_via"), "flags": record.get("flags") or [],
            "newOrder": (record.get("new_order") or {}).get("order_id"),
            "event": (record.get("raised_event") or {}).get("reason"),
            "needsHuman": bool(record.get("needs_human")),
        }
    action = EVENT_ACTIONS.get(record.get("action", ""))
    if record.get("kind") != "event" or action is None:
        return None
    if action in ("approved", "rejected"):
        role = (record.get("role") or "").replace("_", " ")
        text = record.get("note") or (f"Approved, ${record.get('total_cost', 0):,.2f}." if action == "approved" else "")
        return {"at": record["at"], "order": record["object"], "who": "human", "action": action,
                "by": f"{record.get('by')} ({role})", "text": text}
    order = record.get("order") or {}
    text = order.get("reason") or "Created from the approved plan option."
    return {"at": record["at"], "order": record["object"], "who": "rule", "action": action, "text": text}


def page(agent: OrderAgent, records: list[dict], *, mode: str, source: str,
         last_check: datetime | None, outbox: int | None = None) -> dict:
    orders, sites = order_rows(agent)
    events = [e for e in (event_from_record(r) for r in records) if e is not None]
    events.sort(key=lambda e: e["at"])
    if outbox is None:
        outbox = sum(1 for e in events if e.get("sentVia"))
    return {
        "source": source, "mode": mode, "asOf": str(agent.snapshot.date()),
        "lastCheck": last_check.isoformat() if last_check else None,
        "procurementLimit": PROCUREMENT_LIMIT, "sites": sites, "orders": orders,
        "events": events, "outbox": outbox,
    }


def order_records(engine, order_ids: list[str]) -> list[dict]:
    """The agent's audit records for these orders, oldest first."""

    import json

    from sqlalchemy import text

    if not order_ids:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT detail FROM audit_log
            WHERE object = ANY(CAST(:ids AS text[])) AND detail ->> 'kind' IS NOT NULL
            ORDER BY audit_id
        """), {"ids": order_ids}).scalars().all()
    return [row if isinstance(row, dict) else json.loads(row) for row in rows]


def orders_page(engine, data) -> dict:
    """The Orders page from the database (used by the platform API, apps/api/main.py)."""

    from services.agents.order_db import build_agent, last_check_time, load_catalog_from_db

    if load_catalog_from_db(engine) is None:
        return {"source": "database", "mode": "database", "asOf": str(getattr(data, "as_of", "") or ""),
                "lastCheck": None, "procurementLimit": PROCUREMENT_LIMIT, "sites": {}, "orders": [],
                "events": [], "outbox": 0, "catalog": False}
    agent, _ = build_agent(engine, data)
    result = page(agent, order_records(engine, list(agent.orders)), mode="database",
                  source="Postgres via the platform API", last_check=last_check_time(engine))
    result["catalog"] = True
    return result
