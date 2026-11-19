"""History and data sources: audit entries in plain words, ingestion runs and where each number came from."""

from __future__ import annotations

from datetime import datetime

from apps.api.state.common import ROLE_OF_APPROVER, _rows, minutes_from


# ---------------------------------------------------------
# Audit and data sources
# ---------------------------------------------------------

def _actor(row: dict) -> tuple[str, str]:
    detail = row["detail"] or {}
    if row["actor_type"] == "human":
        return ROLE_OF_APPROVER.get(detail.get("role", ""), "supply"), row["actor"]
    if row["actor_type"] == "agent":
        return "agent", row["actor"]
    if row["actor_type"] == "rule":
        # The order orchestrator logs its steps as a rule row; when its agent made the call, say so.
        if "agent" in (detail.get("chosen_by"), detail.get("decided_by")):
            return "agent", row["actor"]
        return "solver", row["actor"]
    return "system", row["actor"]


ORDER_STEPS = ("Order placed via", "Order email ready", "Order rejected by", "Order not submitted", "Order orchestration")


def _supplier(supplier_id: str | None) -> str:
    return " ".join(w.capitalize() for w in (supplier_id or "a supplier").split("-"))


def _detail_text(row: dict, site_names: dict[str, str]) -> str:
    detail = row["detail"] or {}
    action = row["action"]
    if action in ("Approved", "Rejected"):
        # Described, not numbered: the number people see counts only the options that passed their checks.
        what = detail.get("type", "plan").replace("_", " + ").capitalize()
        moves = [a for a in detail.get("actions", []) if a.get("units")]
        if moves:
            name = lambda site: site_names.get(site, site)
            what += ": " + ", ".join(
                f"transfer {a['units']} from {name(a['from_site'])} to {name(a['to_site'])}" if a.get("from_site")
                else f"order {a['units']} from {_supplier(a.get('supplier_id'))} for {name(a['to_site'])}" for a in moves)
        note = detail.get("note")
        return what + (f'. Note: "{note}"' if note else "")
    if action.startswith(ORDER_STEPS):
        text_ = detail.get("summary") or detail.get("note") or ""
        ref = detail.get("external_ref")
        if ref and ref not in text_:
            text_ += f" Reference {ref}."
        if detail.get("mode") == "simulate" and not action.startswith("Order orchestration"):
            text_ += " (Simulated: built and checked, nothing was sent.)"
        return text_.strip()
    if action == "Order escalated to procurement" or action.startswith("Escalated to"):
        return detail.get("message", "")
    if action == "Order request drafted":
        return (f"Email to {detail.get('message_to', 'the supplier')} drafted: \"{detail.get('subject', '')}\". "
                "Used only if the order has to go by email; nothing is sent from here.")
    if action == "Order held for release":
        return detail.get("note") or f"{detail.get('qty')} units is over the auto-submit limit; a person must release it."
    if action == "Notification read":
        return "Marked a notification as read."
    if action.startswith("Signal pipeline"):
        reason = detail.get("reason")
        return "; ".join(reason) if isinstance(reason, list) else (reason or "")
    if action == "Requested a plan":
        return f"{detail.get('drug', row['object'])}" + (f" at site {detail['site']}" if detail.get("site") else "") + f" (stock as of {detail.get('day')})"
    if action.startswith("Follow-up"):
        return detail.get("reason", "")
    if action == "Daily run":
        watch = detail.get("stock_watch", {})
        parts = [f"{len(detail.get('emails', []))} email(s)", f"{len(watch.get('alerts', []))} stock-watch alert(s)",
                 f"{len(detail.get('followup', []))} follow-up check(s)", f"{detail.get('waiting_for_approval', 0)} waiting for approval"]
        return f"Day {detail.get('day')}: " + ", ".join(parts)
    if "problems" in detail:
        return "; ".join(detail["problems"])
    for key in ("summary", "reason", "message", "note"):    # anything else the agents log: their own words
        if isinstance(detail.get(key), str) and detail[key]:
            return detail[key]
    return ""


def audit_entries(conn, start: datetime, site_names: dict[str, str] | None = None, limit: int = 200) -> list[dict]:
    rows = _rows(conn, """SELECT audit_id, at, actor, actor_type, action, object, detail, refs
                          FROM audit_log ORDER BY audit_id DESC LIMIT :limit""", limit=limit)
    entries = []
    for row in rows:
        actor, name = _actor(row)
        entries.append({"id": f"AU-{row['audit_id']:04d}", "atMin": minutes_from(start, row["at"]), "actor": actor,
                        "actorName": name, "action": row["action"], "object": row["object"] or "",
                        "detail": _detail_text(row, site_names or {}), "refs": list(row["refs"] or [])})
    return entries


def ingestion(conn) -> dict:
    runs = _rows(conn, """SELECT run_id, source, state, started_at, rows_in, rows_ok, rows_quarantined, error
                          FROM ingestion_run ORDER BY run_id DESC LIMIT 12""")
    quarantine = _rows(conn, """SELECT quarantine_id, snapshot_id, error_code, error_detail
                                FROM quarantine ORDER BY quarantine_id DESC LIMIT 12""")
    counts = _rows(conn, """SELECT
        (SELECT count(*) FROM drug_product) AS dpd,
        (SELECT count(*) FROM shortage_report) AS shortages,
        (SELECT count(*) FROM tracked_drug) AS drugs,
        (SELECT count(DISTINCT as_of) FROM inventory_snapshot) AS days,
        (SELECT count(*) FROM supply_event WHERE source_type = 'supplier_email') AS emails,
        (SELECT count(*) FROM substitution) AS subs""")[0]
    return {
        "runs": [{"id": r["run_id"], "source": r["source"], "state": r["state"],
                  "started": r["started_at"].strftime("%b %d %H:%M"), "rowsIn": r["rows_in"], "rowsOk": r["rows_ok"],
                  "quarantined": r["rows_quarantined"], **({"error": r["error"]} if r["error"] and r["state"] == "failed" else {})}
                 for r in runs],
        "quarantine": [{"id": q["quarantine_id"], "snapshot": q["snapshot_id"], "code": q["error_code"],
                        "detail": q["error_detail"] or ""} for q in quarantine],
        "catalog": [
            {"name": "Health Canada Drug Product Database", "status": f"{counts['dpd']} products", "origin": "Real",
             "note": "Loaded by ingestion.dpd_ingest; raw responses kept"},
            {"name": "Health Canada shortage reports", "status": f"{counts['shortages']} reports", "origin": "Real",
             "note": "Loaded by ingestion.shortage_ingest (nightly workflow)"},
            {"name": "Hospital stock, usage and orders", "status": f"{counts['days']} days loaded", "origin": "Synthetic",
             "note": f"{counts['drugs']} drugs; loaded by ingestion.synthetic_loader"},
            {"name": "Supplier emails", "status": f"{counts['emails']} processed", "origin": "Synthetic",
             "note": "Test set including hostile cases"},
            {"name": "Substitution table", "status": f"{counts['subs']} rows", "origin": "Synthetic",
             "note": "Hand-built, every row sourced"},
        ],
    }


def sources(conn, events: list[dict], plans: list[dict], stock: list[dict]) -> dict:
    """Source records for the IDs the UI shows as chips."""

    records = {}
    snapshot_ids = {int(ref.split(":")[1]) for line in stock for ref in (line["inventoryRef"], line["usageRef"])
                    if ref.startswith("snapshot:")}
    if snapshot_ids:
        for row in _rows(conn, """SELECT s.snapshot_id, s.source, s.endpoint, s.payload_sha256, s.fetched_at, r.run_id
                                  FROM raw_snapshot s JOIN ingestion_run r USING (run_id)
                                  WHERE s.snapshot_id = ANY(CAST(:ids AS bigint[]))""", ids=sorted(snapshot_ids)):
            records[f"snapshot:{row['snapshot_id']}"] = {
                "id": f"snapshot:{row['snapshot_id']}", "kind": "Raw snapshot",
                "label": f"{row['endpoint']} from {row['source']}",
                "detail": f"Stored by ingestion run {row['run_id']} on {row['fetched_at']:%b %d %H:%M}.",
                "origin": "synthetic" if row["source"] == "synthetic_csv" else "real (cached)",
                "table": "raw_snapshot", "sha256": row["payload_sha256"]}
    for event in events:
        records[event["id"]] = {"id": event["id"], "kind": "Supply event", "label": event["title"],
                                "detail": f"From {event['sourceRef']}. {event['summary']}", "origin": "platform",
                                "table": "supply_event"}
    for plan in plans:
        records.setdefault(plan["plan_id"], {"id": plan["plan_id"], "kind": "Plan", "label": f"Plan for {plan['event_id']}",
                                             "detail": f"Status {plan['status']}; {len(plan['options'])} option(s).",
                                             "origin": "platform", "table": "plan"})
    return records
