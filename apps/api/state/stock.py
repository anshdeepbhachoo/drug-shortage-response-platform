"""Catalogue and stock: sites, drugs, platform-placed orders and stock lines with dated orders."""

from __future__ import annotations

from datetime import date

from apps.api.state.common import _rows
from services.data.db_data import PostgresData


# ---------------------------------------------------------
# Catalogue and stock
# ---------------------------------------------------------

def sites_and_drugs(conn) -> tuple[list[dict], list[dict]]:
    sites = [{"id": r["site_id"], "name": r["name"], "short": r["name"]}
             for r in _rows(conn, "SELECT site_id, name FROM site ORDER BY site_id")]
    drugs = [{"id": r["din"], "name": r["name"], "form": f"DIN {r['din']}", "unit": r["unit"] or "units",
              "criticalList": r["criticality"] == "critical", "dpdRef": f"DIN-{r['din']}"}
             for r in _rows(conn, "SELECT din, name, criticality, unit FROM tracked_drug ORDER BY din")]
    return sites, drugs


def platform_orders(conn) -> dict[str, dict]:
    """Purchase orders the platform approved, with where each stands for the stock projections:

    counted: placed (or the supplier confirmed it), so it counts as arriving in the hospital's stock line.
    failed:  cancelled, or the order agent replanned it (backordered, cancelled or too late): never counted.
    Approved but not placed yet: counted only in "with the approved plan". Received: the hospital's stock feed
    has it (the synthetic feed does not know about platform orders, so it disappears from the projection).
    """

    orders = {r["order_id"]: r for r in _rows(conn, """
        SELECT o.order_id, o.din, o.site_id, o.qty, o.status, o.expected_date, o.promised_date, s.name AS supplier
        FROM purchase_order o JOIN supplier s USING (supplier_id) WHERE o.plan_id IS NOT NULL""")}
    latest: dict[str, dict] = {}
    for r in _rows(conn, """SELECT object, detail FROM audit_log
                            WHERE actor = 'order-agent' AND action LIKE 'Order follow-up: %' ORDER BY audit_id"""):
        latest[r["object"]] = r["detail"]
    for order_id, o in orders.items():
        check = latest.get(order_id) or {}
        confirmed = bool((check.get("supplier") or {}).get("confirmed"))
        o["failed"] = o["status"] == "cancelled" or check.get("decision") == "replan"
        o["counted"] = not o["failed"] and (o["status"] in ("placed", "in_transit", "delayed") or
                                            (confirmed and o["status"] == "approved"))
    return orders


def stock_lines(conn, data: PostgresData, as_of: date, drugs: list[dict], platform: dict[str, dict] | None = None) -> list[dict]:
    refs = {(r["site_id"], r["din"]): r["source_ref"] for r in _rows(conn, """
        SELECT DISTINCT ON (site_id, din) site_id, din, source_ref FROM inventory_snapshot
        WHERE as_of <= :as_of ORDER BY site_id, din, as_of DESC""", as_of=as_of)}
    usage_refs = {(r["site_id"], r["din"]): r["source_ref"] for r in _rows(conn, """
        SELECT DISTINCT ON (site_id, din) site_id, din, source_ref FROM usage_record
        WHERE usage_date <= :as_of ORDER BY site_id, din, usage_date DESC""", as_of=as_of)}
    # The date each order was first promised, to show delays.
    first_due = {r["order_id"]: r["expected_date"] for r in _rows(conn, """
        SELECT DISTINCT ON (order_id) order_id, expected_date FROM purchase_order_snapshot
        WHERE as_of <= :as_of ORDER BY order_id, as_of""", as_of=as_of)}

    lines = []
    for drug in drugs:
        din = drug["id"]
        orders_by_site: dict[str, list[dict]] = {}
        for order in data.orders(din):
            due = date.fromisoformat(order["expected_date"])
            first = first_due.get(order["order_id"], due)
            entry = {"id": order["order_id"], "qty": int(order["qty"]), "etaDay": (due - as_of).days,
                     "supplier": order["supplier"], "status": "delayed" if due > first else "on-time"}
            if due > first:
                entry["originalEtaDay"] = (first - as_of).days
            orders_by_site.setdefault(order["site_id"], []).append(entry)
        for o in (platform or {}).values():
            if o["din"] == din and o["counted"] and o["expected_date"] >= as_of:
                late = o["status"] == "delayed" or bool(o["promised_date"] and o["promised_date"] > o["expected_date"])
                orders_by_site.setdefault(o["site_id"], []).append({
                    "id": o["order_id"], "qty": o["qty"], "etaDay": (o["expected_date"] - as_of).days,
                    "supplier": o["supplier"], "status": "delayed" if late else "on-time"})
        for row in data.stock(din):
            site = row["site_id"]
            lines.append({
                "siteId": site, "drugId": din, "onHand": row["on_hand"], "dailyUsage": row["daily_use"],
                "safetyDays": 14, "orders": orders_by_site.get(site, []),
                "inventoryRef": refs.get((site, din), ""), "usageRef": usage_refs.get((site, din), ""),
            })
    return lines
