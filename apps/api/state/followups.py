"""Follow-up and notifications: tracked transfers and orders, and what each role should see."""

from __future__ import annotations

from datetime import datetime, time, timedelta

from apps.api.state.common import _rows
from services.store.followup_db import DEFAULT_DEADLINE_HOURS


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
    From people: escalations (services/store/escalations.py). A notification stays until someone in that role
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
