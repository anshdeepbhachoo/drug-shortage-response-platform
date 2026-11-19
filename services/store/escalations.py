"""People escalating approved work to the role that can act on it, and reading their notifications.

After approval, an order belongs to procurement: they own the supplier (placing it, chasing it, answering
it). So whoever approved an order (the supply manager for a combined transfer and order, a pharmacist or
procurement for an order) escalates order problems to procurement. Procurement escalates back to the supply
manager, who owns the plan, when the supplier can't deliver and the gap needs another answer. A transfer
belongs to the supply manager. Nobody escalates to themselves, and the auditor only reads.

Code decides the target, not the person: ESCALATE_TO. Every escalation and every "read" is in audit_log;
apps/api/state/followups.py turns them, with what the agents escalated, into each role's notifications.
"""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

# (what it is, who escalates) -> who receives it
ESCALATE_TO = {
    ("order", "supply_manager"): "procurement",
    ("order", "pharmacist"): "procurement",
    ("order", "procurement"): "supply_manager",
    ("transfer", "pharmacist"): "supply_manager",
    ("transfer", "procurement"): "supply_manager",
    ("transfer", "supply_manager"): "pharmacist",
}
ROLE_WORD = {"procurement": "procurement", "supply_manager": "the supply manager", "pharmacist": "the site pharmacist"}


class EscalationError(Exception):
    """The escalation is not allowed. Nothing was written."""


def _item(conn, object_id: str) -> tuple[str, str | None]:
    """('order' | 'transfer', plan_id) for an approved order or transfer."""

    row = conn.execute(text("SELECT plan_id FROM purchase_order WHERE order_id = :o"), {"o": object_id}).first()
    if row:
        return "order", row[0]
    row = conn.execute(text("SELECT plan_id FROM plan_action WHERE action_id = :o AND type = 'transfer'"),
                       {"o": object_id}).first()
    if row:
        return "transfer", row[0]
    raise EscalationError(f"{object_id} is not an approved order or transfer.")


def target_for(kind: str, role: str) -> str:
    target = ESCALATE_TO.get((kind, role))
    if target is None:
        raise EscalationError(f"A {role} cannot escalate a {kind}." if role != "auditor" else "The auditor only reads.")
    return target


def escalate(engine: Engine, object_id: str, by: str, role: str, message: str, at: datetime | None = None) -> dict:
    """A person escalates an approved order or transfer. Returns who it went to."""

    if not by.strip():
        raise EscalationError("An escalation needs the person's name.")
    if not message.strip():
        raise EscalationError("Say what the problem is.")
    with engine.begin() as conn:
        kind, plan_id = _item(conn, object_id)
        target = target_for(kind, role)
        conn.execute(text("""
            INSERT INTO audit_log (actor, actor_type, action, object, detail, refs, at)
            VALUES (:actor, 'human', :action, :object, CAST(:detail AS jsonb), :refs, COALESCE(:at, now()))
        """), {"actor": by, "action": f"Escalated to {ROLE_WORD[target]}", "object": object_id,
               "detail": json.dumps({"to_role": target, "from_role": role, "kind": kind, "message": message.strip()}),
               "refs": [r for r in (object_id, plan_id) if r], "at": at})
    return {"object_id": object_id, "kind": kind, "to_role": target}


def mark_read(engine: Engine, notification_id: str, by: str, role: str, at: datetime | None = None) -> None:
    if role == "auditor":
        raise EscalationError("The auditor only reads.")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO audit_log (actor, actor_type, action, object, detail, refs, at)
            VALUES (:actor, 'human', 'Notification read', :object, CAST(:detail AS jsonb), :refs, COALESCE(:at, now()))
        """), {"actor": by, "object": notification_id.split(":")[0],
               "detail": json.dumps({"id": notification_id, "role": role}), "refs": [notification_id.split(":")[0]],
               "at": at})
