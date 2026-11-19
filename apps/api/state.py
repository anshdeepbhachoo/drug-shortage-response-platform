"""Build the web UI's state from the database, in the shapes apps/web/src/lib/types.ts uses.

The UI was written against a scripted demo (apps/web/src/data/mock.ts). Rather
than rewrite every page, the API hands it the same shapes filled with real
data: sites, drugs, stock lines with dated orders, supply events, one plan card
per plan option, audit entries, emails, and ingestion runs.

Dates: the UI counts days and minutes from a "scenario start". Here that is
the stock day being viewed (as_of), so day 0 is that day and orders, run-out
dates and audit times line up with it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.agents.store.approvals import APPROVER_ROLES, option_kind, shift_order_dates
from services.agents.store.followup_db import DEFAULT_DEADLINE_HOURS
from services.agents.data.db_data import PostgresData

ROOT = Path(__file__).resolve().parents[2]
EMAILS = ROOT / "mock" / "emails"

ROLE_OF_APPROVER = {"supply_manager": "supply", "pharmacy_manager": "pharmacist", "pharmacist": "pharmacist",
                    "procurement": "procurement"}
EVENT_KIND = {"delay": "delay", "shortage": "shortage", "usage_spike": "usage-spike", "discontinuation": "shortage"}
EVENT_SOURCE = {"supplier_email": "supplier-email", "shortage_report": "hc-shortage-api", "site_report": "site-report",
                "stock_watch": "site-report", "manual": "site-report"}
EVENT_STATUS = {"open": "open", "monitoring": "monitoring", "mitigated": "mitigated", "closed": "mitigated"}


def _rows(conn, sql: str, **params) -> list[dict]:
    return [dict(row._mapping) for row in conn.execute(text(sql), params)]


def loaded_days(engine: Engine) -> list[date]:
    """Days whose hospital data has been ingested."""

    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text("SELECT DISTINCT as_of FROM inventory_snapshot ORDER BY as_of"))]


def scenario_days() -> list[date]:
    """Every day of the simulation, whether or not its data has arrived yet."""

    from ingestion.synthetic_loader import progression_folders

    try:
        return [date.fromisoformat(folder.name) for folder in progression_folders()]
    except FileNotFoundError:
        return []


def available_days(engine: Engine) -> list[date]:
    return sorted(set(loaded_days(engine)) | set(scenario_days()))


def scenario_start(as_of: date) -> datetime:
    return datetime.combine(as_of, time(8, 0))


def minutes_from(start: datetime, moment: datetime | None) -> int:
    if moment is None:
        return 0
    if moment.tzinfo:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return round((moment - start).total_seconds() / 60)


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


# ---------------------------------------------------------
# Events, plans, emails
# ---------------------------------------------------------

def _plans_raw(conn) -> list[dict]:
    plans = _rows(conn, """
        SELECT p.plan_id, p.event_id, p.status, p.detail, p.created_at, p.no_perfect_plan, e.din,
               d.option_index AS approved_option, d.note AS decision_note
        FROM plan p JOIN supply_event e USING (event_id)
        LEFT JOIN decision d ON d.plan_id = p.plan_id
        ORDER BY p.created_at DESC""")
    actions = _rows(conn, """
        SELECT a.plan_id, a.option_index, a.action_id, a.type, a.description, a.reasoning, a.from_site, a.to_site,
               a.units, a.supplier_id, s.name AS supplier,
               -- An approved order's date is the purchase order's (its lead time started at approval).
               COALESCE(po.expected_date, a.expected_date) AS expected_date, a.verified
        FROM plan_action a LEFT JOIN supplier s USING (supplier_id)
        LEFT JOIN purchase_order po ON po.source_ref = 'plan:' || a.action_id
        ORDER BY a.plan_id, a.option_index, a.action_id""")
    by_plan: dict[str, dict[int, list[dict]]] = {}
    for action in actions:
        by_plan.setdefault(action["plan_id"], {}).setdefault(action["option_index"], []).append(action)
    for plan in plans:
        plan["options"] = by_plan.get(plan["plan_id"], {})
    return plans


PLAN_STATE = {"pending_approval": "Plan waiting for approval", "approved": "Plan approved",
              "proposed": "No safe option: needs a person", "broken": "Plan no longer safe",
              "rejected": "Plan rejected"}


def supply_events(conn, start: datetime, plans: list[dict], names: dict[str, str]) -> list[dict]:
    current_plan = {}
    for plan in plans:  # newest first: the first non-superseded plan per event is its current one
        if plan["status"] != "superseded":
            current_plan.setdefault(plan["event_id"], plan)
    resupply = {}
    for plan in reversed(plans):  # newest last, so it wins
        detail = plan["detail"] or {}
        resupply[plan["event_id"]] = detail.get("cover_days") or {}

    events = []
    for row in _rows(conn, """
        SELECT event_id, din, site_id, kind, source_type, source_ref, delay_days, confidence, status, detail, created_at
        FROM supply_event ORDER BY created_at DESC"""):
        drug = names.get(row["din"], row["din"])
        reason = (row["detail"] or {}).get("reason", "")
        days = resupply.get(row["event_id"], {}).get(row["site_id"]) if row["site_id"] else None
        events.append({
            "id": row["event_id"], "drugId": row["din"], "siteId": row["site_id"],
            "kind": EVENT_KIND.get(row["kind"], "delay"),
            "title": f"{drug}: {row['kind'].replace('_', ' ')}" + (f" of {row['delay_days']:.0f} days" if row["delay_days"] else ""),
            "summary": reason or f"Reported by {row['source_type'].replace('_', ' ')}.",
            "source": EVENT_SOURCE.get(row["source_type"], "site-report"), "sourceRef": row["source_ref"],
            "confidence": float(row["confidence"]), "atMin": minutes_from(start, row["created_at"]),
            "status": EVENT_STATUS.get(row["status"], "open"),
            "evidence": (row["detail"] or {}).get("evidence") or [
                {"source": row["source_type"], "ref": row["source_ref"], "summary": reason}],
            "planState": PLAN_STATE.get(current_plan[row["event_id"]]["status"], "") if row["event_id"] in current_plan else "No plan yet",
            "sites": sorted(set((current_plan.get(row["event_id"], {}).get("detail") or {}).get("risk_sites") or [])
                            | ({row["site_id"]} if row["site_id"] else set())),
            **({"resupplyDay": int(days)} if days else {}),
        })
    return events


def _option_status(plan: dict, index: int, verified: bool) -> str:
    status = plan["status"]
    if status == "approved":
        return "approved" if index == plan["approved_option"] else "superseded"
    if status == "rejected":
        return "rejected"
    if status == "broken":
        return "broken" if verified else "proposed"
    if status == "pending_approval":
        return "pending-approval" if verified else "proposed"
    return "proposed"


# UI role names, as the app's role picker uses them.
UI_ROLE_OF = {"supply_manager": "supply", "pharmacist": "pharmacist", "procurement": "procurement"}
LEAD_TIME = {"substitute": "A pharmacist decides", "conserve": "Starts when chosen", "escalate": "Hands it to a person"}


def _when(day: date) -> str:
    return day.strftime("%b %d").replace(" 0", " ")


def _order_projection(plan: dict, action: dict, platform: dict[str, dict] | None) -> dict:
    """How an approved plan's order counts in "with the approved plan": not at all once it is placed (the
    hospital's stock line already counts it) or failed (cancelled or replanned), so nothing counts twice."""

    if plan["status"] != "approved" or not platform:
        return {}
    order = platform.get(f"PO-{action['action_id']}") or {}
    if order.get("failed"):
        return {"projection": "failed"}
    if order.get("counted"):
        return {"projection": "in-stock-line"}
    return {}


def plan_cards(plans: list[dict], as_of: date | None = None, platform: dict[str, dict] | None = None) -> list[dict]:
    """One UI plan card per option. Card id "<plan_id>-O<n>" maps back for approve/reject.
    as_of: the stock day being viewed; order arrival days (etaDay) count from it.

    Options that failed their checks are left out, so every option shown can be chosen.
    Only a plan where nothing passed shows its options, so the problem is still visible.
    """

    cards = []
    for plan in plans:
        passed = {i for i, acts in plan["options"].items() if all(a["verified"] for a in acts)}
        shown = 0   # options are numbered 1, 2, 3 as shown; the card id keeps the stored index for approval
        for index, actions in sorted(plan["options"].items()):
            first = actions[0]
            kind = option_kind(actions)
            verified = index in passed
            if passed and not verified:
                continue
            transfers = [{"from": a["from_site"], "to": a["to_site"], "qty": a["units"]}
                         for a in actions if a["units"] and a["from_site"]]
            today = as_of or plan["created_at"].date()
            order_lines = [a for a in actions if a["type"] == "order" and a["expected_date"]]

            if plan["status"] == "pending_approval" and (plan["detail"] or {}).get("planned_on"):
                # Not approved yet: the lead time starts at approval, as approvals.shift_order_dates does.
                order_lines = shift_order_dates(order_lines, date.fromisoformat(plan["detail"]["planned_on"]), today)
            orders = [{"qty": a["units"], "to": a["to_site"], "supplier": a["supplier"] or a["supplier_id"],
                       "etaDay": (a["expected_date"] - today).days, "expectedDate": a["expected_date"].isoformat(),
                       "premiumPct": 0, **_order_projection(plan, a, platform)}
                      for a in order_lines]
            # Hospitals this option involves: the ones at risk and any it moves stock between.
            sites = set((plan["detail"] or {}).get("risk_sites") or [])
            sites |= {t["from"] for t in transfers} | {t["to"] for t in transfers} | {o["to"] for o in orders}
            if transfers and orders:
                latest = max(a["expected_date"] for a in order_lines)
                cost = "Courier transfer ≈ $0, plus supplier price (not yet quoted)"
                lead = f"Transfer tomorrow by courier; order expected {_when(latest)} (unconfirmed)"
            elif transfers:
                cost, lead = "≈ $0 (courier transfer)", "Tomorrow by courier"
            elif orders:
                latest = max(a["expected_date"] for a in order_lines)
                cost, lead = "Supplier price (not yet quoted)", f"Expected {_when(latest)} (standard lead time, unconfirmed)"
            else:
                cost, lead = "No cost estimate", LEAD_TIME.get(first["type"], "Needs a person")
            shown += 1
            cards.append({
                "id": f"{plan['plan_id']}-O{index}", "eventId": plan["event_id"], "drugId": plan["din"],
                "solverRun": plan["plan_id"], "rank": shown,
                "title": first["description"], "transfers": transfers, "orders": orders,
                "score": round(max(0.1, (0.95 if verified else 0.4) - 0.05 * (index - 1)), 2),
                "cost": cost,
                "leadTime": lead,
                "optionType": kind,
                "chooseRoles": sorted(UI_ROLE_OF[r] for r in APPROVER_ROLES.get(kind, set()) if r in UI_ROLE_OF),
                "explanation": first["reasoning"] or "",
                "citations": [plan["event_id"], plan["plan_id"]],
                "status": _option_status(plan, index, verified),
                "sites": sorted(sites),
                "createdAt": plan["created_at"].isoformat(),
                **({"decisionNote": plan["decision_note"]} if plan["decision_note"] and index == plan["approved_option"] else {}),
            })
    return cards


def _email_file(source_ref: str) -> Path | None:
    """Only files inside mock/emails are read."""

    path = (ROOT / source_ref).resolve()
    return path if path.is_file() and EMAILS.resolve() in path.parents else None


def _parse_email(path: Path) -> tuple[str, str, str]:
    text_ = path.read_text(encoding="utf-8")
    head, _, body = text_.partition("\n\n")
    fields = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    return fields.get("From", "").strip() or path.name, fields.get("Subject", "").strip() or path.name, body.strip() or text_


def emails(conn, start: datetime, names: dict[str, str]) -> list[dict]:
    """Supplier emails the pipeline handled: accepted ones (became events) and blocked ones."""

    result, seen = [], set()
    accepted = _rows(conn, """SELECT source_ref, detail, created_at, din FROM supply_event
                              WHERE source_type = 'supplier_email' ORDER BY created_at DESC""")
    blocked = _rows(conn, """SELECT object AS source_ref, detail, at AS created_at FROM audit_log
                             WHERE action = 'Signal pipeline: needs_review' ORDER BY audit_id DESC""")
    for row, outcome in [(r, "accepted") for r in accepted] + [(r, "quarantined") for r in blocked]:
        path = _email_file(row["source_ref"] or "")
        if path is None or row["source_ref"] in seen:
            continue
        seen.add(row["source_ref"])
        sender, subject, body = _parse_email(path)
        detail = row["detail"] or {}
        assessment = detail if outcome == "accepted" else (detail.get("assessment") or {})
        reasons = detail.get("reason") if outcome == "quarantined" else None
        flags = reasons if isinstance(reasons, list) else ([reasons] if reasons else [])
        result.append({
            "id": row["source_ref"], "from": sender, "subject": subject, "atMin": minutes_from(start, row["created_at"]),
            "body": body, "outcome": outcome,
            "extraction": {
                "eventType": "Delivery delay" if outcome == "accepted" else "None found",
                "drug": assessment.get("drug_name") or names.get(assessment.get("drug_din", ""), "None identified"),
                "drugMatch": "Catalogue search by the assessment agent" if outcome == "accepted" else "Not attempted",
                "confidence": float(assessment.get("confidence") or 0),
                "excerpt": assessment.get("reason") or "",
                "flags": [f"Security screen: {f.replace('_', ' ')}" for f in flags],
                **({"newEta": f"+{assessment['delay_weeks']:g} weeks"} if assessment.get("delay_weeks") else {}),
            },
        })
    return result


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


# ---------------------------------------------------------
# Everything the UI needs, in one response
# ---------------------------------------------------------

def _inbox_for(day: date) -> list[str]:
    from services.agents.pipeline.daily_run import load_inbox

    return load_inbox().get(day.isoformat(), [])


FOLLOWUP_STATUS = {
    "wait": "On track: not due yet",
    "nudge": "Nudged: no confirmation yet",
    "escalate": "Escalated to a person",
    "replan": "Not happening: being replanned",
    "close": "Done",
}


def followups(conn, names: dict[str, str], site_names: dict[str, str]) -> list[dict]:
    """Every approved transfer, with the follow-up agent's latest check in plain words."""

    moves = _rows(conn, """
        SELECT a.action_id, a.from_site, a.to_site, a.units, e.din, d.decided_at
        FROM plan_action a
        JOIN plan p USING (plan_id)
        JOIN decision d ON d.plan_id = p.plan_id AND d.decision = 'approved' AND d.option_index = a.option_index
        JOIN supply_event e ON e.event_id = p.event_id
        WHERE a.type = 'transfer'
        ORDER BY d.decided_at DESC, a.action_id""")
    latest = {r["object"]: r for r in _rows(conn, """
        SELECT DISTINCT ON (object) object, action, detail
        FROM audit_log WHERE actor = 'follow-up-agent'
        ORDER BY object, audit_id DESC""")}
    result = []
    for m in moves:
        check = latest.get(m["action_id"])
        detail = (check or {}).get("detail") or {}
        facts = detail.get("facts") or {}
        decision = check["action"].removeprefix("Follow-up: ") if check else None
        result.append({
            "id": m["action_id"], "kind": "transfer", "drugId": m["din"], "drug": names.get(m["din"], m["din"]),
            "from": site_names.get(m["from_site"], m["from_site"]), "to": site_names.get(m["to_site"], m["to_site"]),
            "units": m["units"], "approvedAt": m["decided_at"].isoformat(),
            # When the follow-up agent expects the transfer to have arrived (same deadline it checks against).
            "expectedBy": (m["decided_at"] + timedelta(hours=DEFAULT_DEADLINE_HOURS)).isoformat(),
            "decision": decision,
            "status": FOLLOWUP_STATUS.get(decision, decision) if decision else "Not checked yet: follow-up runs each evening",
            "confirmed": facts.get("units_confirmed"), "hoursOverdue": facts.get("hours_overdue"),
            "checkedAt": detail.get("at"),
            # "agent" when the model chose; "rule" or "fallback" when code did.
            "decidedBy": detail.get("decided_by"),
            "reason": detail.get("reason", ""),
            "messageTo": detail.get("message_to"), "messageDraft": detail.get("message_draft"),
        })
    return result + order_followups(conn, names, site_names)


# Backend role -> UI role (who sees the notification).
ROLE_UI = {"supply_manager": "supply", "procurement": "procurement", "pharmacist": "pharmacist"}
AGENT_TITLE = {"escalate": "Escalated to you", "nudge": "Reminder: not placed yet"}
SUBMIT_TITLE = {"ready_to_send": "Order email ready to send", "escalated": "Order needs placing by hand",
                "held": "Order waiting for your release"}


def notifications(conn, items: list[dict]) -> list[dict]:
    """What needs a person, addressed to the role that acts on it, newest first.

    From the agents: the order and follow-up agents' latest escalation or reminder (code chose the recipient),
    and orders the orchestrator agent handed to procurement (an email to send, a hold, an escalation).
    From people: escalations (services/agents/store/escalations.py). A notification stays until someone in that role
    marks it read, or (for an order waiting on procurement) until the order is placed."""

    read = {(r["detail"] or {}).get("id") for r in _rows(conn, "SELECT detail FROM audit_log WHERE action = 'Notification read'")}
    by_id = {f["id"]: f for f in items}
    out = []

    def add(nid, role, origin, title, body, item, at):
        if role not in ROLE_UI:
            return
        out.append({"id": nid, "toRole": ROLE_UI[role], "from": origin, "title": title, "body": body,
                    "objectId": item["id"], "kind": item["kind"], "drugId": item["drugId"],
                    "about": f"{item['drug']}: {item['units']} units, {item['from']} → {item['to']}",
                    "at": at, "read": nid in read})

    for f in items:
        if f.get("decision") in AGENT_TITLE and f.get("messageTo") in ROLE_UI and f.get("orderStatus") not in ("delivered", "cancelled"):
            origin = "Order agent" if f["kind"] == "order" else "Follow-up agent"
            add(f"{f['id']}:{f['decision']}:{f.get('checkedAt')}", f["messageTo"], origin, AGENT_TITLE[f["decision"]],
                f.get("messageDraft") or f.get("reason") or "", f, f.get("checkedAt"))
        last = (f.get("submissions") or [None])[-1]
        if last and last["status"] in SUBMIT_TITLE and f.get("orderStatus") == "approved":
            add(f"{f['id']}:submission:{last['at']}", "procurement", "Order orchestrator", SUBMIT_TITLE[last["status"]],
                last.get("reason") or last.get("note") or "", f, last["at"])
    for r in _rows(conn, """SELECT audit_id, actor, object, detail, at FROM audit_log
                            WHERE actor_type = 'human' AND action LIKE 'Escalated to %' ORDER BY audit_id"""):
        item = by_id.get(r["object"])
        if item:
            d = r["detail"] or {}
            add(f"{r['object']}:escalation:{r['audit_id']}", d.get("to_role"), r["actor"], "Escalated to you",
                d.get("message", ""), item, r["at"].isoformat())
    return sorted(out, key=lambda n: n["at"] or "", reverse=True)


ORDER_STATUS_WORD = {"approved": "Approved, not placed yet", "placed": "Placed with the supplier", "in_transit": "In transit",
                     "delayed": "Delayed by the supplier", "delivered": "Received", "cancelled": "Cancelled"}


def order_followups(conn, names: dict[str, str], site_names: dict[str, str]) -> list[dict]:
    """Every approved purchase order, with the order agent's request draft and latest check in plain words."""

    orders = _rows(conn, """
        SELECT o.order_id, o.din, o.site_id, o.qty, o.status, o.expected_date, s.name AS supplier, d.decided_at
        FROM purchase_order o JOIN supplier s USING (supplier_id)
        JOIN decision d ON d.plan_id = o.plan_id AND d.decision = 'approved'
        WHERE o.plan_id IS NOT NULL
        ORDER BY d.decided_at DESC, o.order_id""")
    records: dict[str, list[dict]] = {}
    for r in _rows(conn, "SELECT object, action, detail FROM audit_log WHERE actor = 'order-agent' ORDER BY audit_id"):
        records.setdefault(r["object"], []).append(r)
    # What the order orchestrator agent did with each order: every step, who chose it and why, the sub-agent's
    # report, and the payload built for that system. Plus the orchestrator's own summary.
    orchestration = {r["object"]: r["detail"] for r in _rows(conn, """
        SELECT object, detail FROM audit_log WHERE actor = 'order-orchestrator' AND action = 'Order orchestration'
        ORDER BY audit_id""")}
    sent: dict[str, list[dict]] = {}
    for r in _rows(conn, "SELECT * FROM order_submission ORDER BY order_id, attempt"):
        sent.setdefault(r["order_id"], []).append({
            "channel": r["channel"], "target": r["target"], "mode": r["mode"], "status": r["status"],
            "externalRef": r["external_ref"], "note": r["note"], "at": r["submitted_at"].isoformat(),
            "payload": r["payload"], "chosenBy": r["chosen_by"], "reason": r["reason"], "report": r["report"]})
    result = []
    for o in orders:
        mine = records.get(o["order_id"], [])
        request = next((r["detail"] for r in mine if r["action"] == "Order request drafted"), None)
        check = next((r for r in reversed(mine) if r["action"].startswith("Order follow-up: ")), None)
        detail = (check or {}).get("detail") or {}
        decision = check["action"].removeprefix("Order follow-up: ") if check else None
        replies = [{"from": e["sender"], "receivedAt": e["received_at"], "text": e["text"],
                    "kind": (e.get("reading") or {}).get("kind"), "summary": (e.get("reading") or {}).get("summary"),
                    "readBy": e.get("read_by"), "problems": e.get("problems") or []}
                   for r in mine if r["action"].startswith("Order follow-up: ") for e in r["detail"].get("replies") or []]
        result.append({
            "id": o["order_id"], "kind": "order", "drugId": o["din"], "drug": names.get(o["din"], o["din"]),
            "from": o["supplier"], "to": site_names.get(o["site_id"], o["site_id"]), "units": o["qty"],
            "approvedAt": o["decided_at"].isoformat(),
            "expectedBy": datetime.combine(o["expected_date"], time(23, 59)).isoformat(),
            "orderStatus": o["status"], "orderStatusText": ORDER_STATUS_WORD.get(o["status"], o["status"]),
            "decision": decision,
            "status": FOLLOWUP_STATUS.get(decision, decision) if decision else "Not checked yet: follow-up runs each evening",
            "checkedAt": detail.get("at"), "decidedBy": detail.get("decided_by"), "reason": detail.get("reason", ""),
            "messageTo": detail.get("message_to"), "messageDraft": detail.get("message_draft"),
            "replies": replies,
            "submissions": sent.get(o["order_id"], []),
            "orchestration": ({k: orchestration[o["order_id"]].get(k) for k in ("decided_by", "summary", "outcome")}
                              if o["order_id"] in orchestration else None),
            "request": {"to": request["message_to"], "subject": request["subject"], "body": request["message_draft"],
                        "decidedBy": request["decided_by"]} if request else None,
        })
    return result


def build_state(engine: Engine, as_of: date | None = None) -> dict:
    days = available_days(engine)
    if not days:
        raise LookupError("No scenario days and no stock loaded. Run: python -m ingestion.synthetic_loader --progression")
    if as_of is None:
        # Open where the user left off: the day after the last one run (or the last day).
        with engine.connect() as conn:
            last = conn.execute(text("SELECT max(object) FROM audit_log WHERE action = 'Daily run'")).scalar()
        later = [d for d in days if last and d.isoformat() > last]
        as_of = later[0] if later else (days[-1] if last else days[0])
    start = scenario_start(as_of)
    data = PostgresData(engine, as_of=as_of)

    with engine.connect() as conn:
        sites, drugs = sites_and_drugs(conn)
        names = {d["id"]: d["name"] for d in drugs}
        platform = platform_orders(conn)
        stock = stock_lines(conn, data, as_of, drugs, platform)
        raw_plans = _plans_raw(conn)
        events = supply_events(conn, start, raw_plans, names)
        tracked = followups(conn, names, {site["id"]: site["name"] for site in sites})
        state = {
            "followups": tracked,
            "notifications": notifications(conn, tracked),
            "asOf": as_of.isoformat(),
            "days": [d.isoformat() for d in days],
            # Days whose data has arrived; before that the stock shown is the latest earlier day (or none).
            "loadedDays": [d.isoformat() for d in loaded_days(engine)],
            "scenarioStart": start.isoformat(),
            "sites": sites,
            "drugs": drugs,
            "stock": stock,
            "events": events,
            "plans": plan_cards(raw_plans, as_of, platform),
            "emails": emails(conn, start, names),
            "audit": audit_entries(conn, start, {site["id"]: site["name"] for site in sites}),
            "ingestion": ingestion(conn),
            "daysRun": [r["object"] for r in _rows(conn, "SELECT DISTINCT object FROM audit_log WHERE action = 'Daily run' ORDER BY object")],
            # Each day's report (the latest run of that day), for the Home page.
            "dayReports": {r["object"]: r["detail"] for r in _rows(conn, """
                SELECT DISTINCT ON (object) object, detail FROM audit_log
                WHERE action = 'Daily run' ORDER BY object, audit_id DESC""")},
            "inbox": _inbox_for(as_of),
            "sources": sources(conn, events, raw_plans, stock),
        }
    return state
