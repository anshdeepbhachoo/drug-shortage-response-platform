"""Supply events, plan cards (one per plan option) and supplier emails."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from apps.api.state.common import EMAILS, EVENT_KIND, EVENT_SOURCE, EVENT_STATUS, SYNTHETIC, _rows, minutes_from
from services.store.approvals import APPROVER_ROLES, option_kind, shift_order_dates


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
    """Only files inside data/synthetic/mock/emails are read. source_ref is relative to data/synthetic
    ("mock/emails/clear.txt")."""

    path = (SYNTHETIC / source_ref).resolve()
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


def _inbox_for(day: date) -> list[str]:
    from services.pipeline.daily_run import load_inbox

    return load_inbox().get(day.isoformat(), [])
