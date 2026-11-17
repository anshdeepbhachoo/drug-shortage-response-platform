"""Human approval of plans: the step between "the agent proposed" and "act on it".

A plan holds alternative options. Approving means choosing one of them. All
rules are enforced here in code (and the database), never by a model:

- only plans waiting for approval can be decided, once (one decision per plan);
- only options that passed the code checks can be approved;
- the person's role must be allowed to approve that kind of action;
- approving marks the plan's supply event as mitigated (it has a response);
- a transfer is re-checked against *current* stock first. If a donor would now
  fall below its minimum (e.g. its usage went up since the plan was made), the
  plan is marked broken and the approval is refused.

The decision, plan status and an audit entry are written in one transaction.
Approving executes nothing: it records that a person authorised the option.

  python -m services.agents.approvals list
  python -m services.agents.approvals approve P-769e347397 --by "Priya Nair" --role pharmacy_manager --note "Confirmed with Northside"
  python -m services.agents.approvals reject  P-769e347397 --by "Priya Nair" --role pharmacist --note "Clinical team prefers to wait"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import ProgrammingError

from services.agents.checks import check_transfer
from services.agents.schemas import Move, PlanOption

# Who may choose each kind of option. The supply manager can choose any option
# except a substitution, which changes what patients receive and needs a pharmacist.
# Choosing "escalate" records who took the problem on.
APPROVER_ROLES: dict[str, set[str]] = {
    "transfer": {"pharmacy_manager", "supply_manager"},
    "order": {"procurement", "supply_manager"},
    "substitute": {"pharmacist"},                                       # clinical decision
    "conserve": {"pharmacist", "pharmacy_manager", "supply_manager"},
    "escalate": {"pharmacist", "pharmacy_manager", "supply_manager"},
}
ALL_ROLES = set().union(*APPROVER_ROLES.values())
DEFAULT_FLOOR_DAYS = 14


class ApprovalError(Exception):
    """The decision is not allowed. Nothing was written."""


# ---------------------------------------------------------
# Reading plans
# ---------------------------------------------------------

def _options(conn: Connection, plan_id: str) -> dict[int, list[dict]]:
    rows = conn.execute(text("""
        SELECT action_id, option_index, type, description, from_site, to_site, units, verified, problems
        FROM plan_action WHERE plan_id = :plan_id
        ORDER BY option_index, action_id
    """), {"plan_id": plan_id}).all()
    options: dict[int, list[dict]] = defaultdict(list)
    for row in rows:
        options[row.option_index].append(dict(row._mapping))
    return dict(options)


def pending_plans(engine: Engine) -> list[dict]:
    """Plans waiting for a decision, with each option summarised."""

    with engine.connect() as conn:
        plans = conn.execute(text("""
            SELECT p.plan_id, p.status, p.created_at, p.no_perfect_plan, e.event_id, e.din,
                   e.source_type, e.source_ref, t.name AS drug_name
            FROM plan p
            JOIN supply_event e USING (event_id)
            LEFT JOIN tracked_drug t ON t.din = e.din
            WHERE p.status IN ('pending_approval', 'proposed')
            ORDER BY p.created_at
        """)).all()
        result = []
        for plan in plans:
            options = _options(conn, plan.plan_id)
            result.append({
                **dict(plan._mapping),
                "options": [
                    {
                        "option": index,
                        "type": actions[0]["type"],
                        "verified": all(a["verified"] for a in actions),
                        "description": actions[0]["description"],
                        "moves": [f"{a['from_site']}->{a['to_site']} {a['units']}" for a in actions if a["units"]],
                    }
                    for index, actions in options.items()
                ],
            })
    return result


# ---------------------------------------------------------
# Re-check a transfer against current stock
# ---------------------------------------------------------

def recheck_transfer(data, din: str, actions: list[dict], detail: dict | None) -> list[str]:
    """Same checks the plan passed when it was made, but with today's stock."""

    stock = {row["site_id"]: {"on_hand": row["on_hand"], "daily_use": row["daily_use"]} for row in data.stock(din)}
    option = PlanOption(
        type="transfer",
        description="approval re-check",
        moves=[Move(from_site=a["from_site"], to_site=a["to_site"], units=a["units"]) for a in actions],
        reasoning="approval re-check",
    )
    detail = detail or {}
    shortfall = detail.get("shortfall")
    if not shortfall:  # plans saved before plan.detail existed: check donor floors only
        shortfall = defaultdict(int)
        for action in actions:
            shortfall[action["to_site"]] += action["units"]
        shortfall = dict(shortfall)
    return check_transfer(option, stock, shortfall, floor_days=DEFAULT_FLOOR_DAYS,
                          reserve_days=detail.get("cover_days") or {})


# ---------------------------------------------------------
# Decisions
# ---------------------------------------------------------

def _lock_plan(conn: Connection, plan_id: str):
    plan = conn.execute(text("""
        SELECT p.plan_id, p.status, p.detail, e.event_id, e.din
        FROM plan p JOIN supply_event e USING (event_id)
        WHERE p.plan_id = :plan_id
        FOR UPDATE OF p
    """), {"plan_id": plan_id}).one_or_none()
    if plan is None:
        raise ApprovalError(f"Unknown plan {plan_id}.")
    return plan


def _audit(conn: Connection, actor: str, actor_type: str, action: str, plan_id: str, detail: dict, refs: list[str],
           at=None) -> int:
    return conn.execute(text("""
        INSERT INTO audit_log (actor, actor_type, action, object, detail, refs, at)
        VALUES (:actor, :actor_type, :action, :object, CAST(:detail AS jsonb), :refs, COALESCE(:at, now()))
        RETURNING audit_id
    """), {"actor": actor, "actor_type": actor_type, "action": action, "object": plan_id,
           "detail": json.dumps(detail, default=str), "refs": refs, "at": at}).scalar_one()


def approve_plan(
    engine: Engine,
    plan_id: str,
    by: str,
    role: str,
    data,
    option: int | None = None,
    note: str | None = None,
    at: "datetime | None" = None,
) -> dict:
    """Approve one option of a plan. data is the stock source used for the re-check.

    at: the decision time to record (default: now). Simulations pass their own
    clock so follow-up deadlines line up with the scenario day; the audit entry
    still records the real time it was written.

    Returns {"approved": True, ...} or {"approved": False, "problems": [...]} when
    the re-check found the plan is no longer safe (the plan is then marked broken).
    Raises ApprovalError, writing nothing, for any other reason it can't be approved.
    """

    if not by.strip():
        raise ApprovalError("An approval needs the approver's name.")

    with engine.begin() as conn:
        plan = _lock_plan(conn, plan_id)
        if plan.status != "pending_approval":
            raise ApprovalError(f"Plan {plan_id} is {plan.status}; only plans pending approval can be approved.")

        options = _options(conn, plan_id)
        if option is None:
            option = next((i for i, acts in options.items() if all(a["verified"] for a in acts)), None)
            if option is None:
                raise ApprovalError(f"Plan {plan_id} has no option that passed the checks.")
        if option not in options:
            raise ApprovalError(f"Plan {plan_id} has no option {option}; options are {sorted(options)}.")

        actions = options[option]
        kind = actions[0]["type"]
        if not all(a["verified"] for a in actions):
            raise ApprovalError(f"Option {option} ({kind}) did not pass the checks and cannot be approved.")
        allowed = APPROVER_ROLES.get(kind, set())
        if role not in allowed:
            raise ApprovalError(f"A {role} cannot approve a {kind}; needs one of {sorted(allowed)}.")

        refs = [plan_id, plan.event_id] + [a["action_id"] for a in actions]

        if kind == "transfer":
            problems = recheck_transfer(data, plan.din, actions, plan.detail)
            if problems:
                conn.execute(text("UPDATE plan SET status = 'broken' WHERE plan_id = :p"), {"p": plan_id})
                audit_id = _audit(conn, "approvals", "rule", "Approval refused: plan no longer safe", plan_id,
                                  {"attempted_by": by, "role": role, "option": option, "problems": problems}, refs, at)
                return {"approved": False, "plan_id": plan_id, "status": "broken", "option": option,
                        "problems": problems, "audit_id": audit_id}

        conn.execute(text("""
            INSERT INTO decision (plan_id, actor, actor_role, decision, note, option_index, decided_at)
            VALUES (:plan_id, :actor, :role, 'approved', :note, :option, COALESCE(:at, now()))
        """), {"plan_id": plan_id, "actor": by, "role": role, "note": note, "option": option, "at": at})
        conn.execute(text("UPDATE plan SET status = 'approved' WHERE plan_id = :p"), {"p": plan_id})
        # The alert now has an approved response; follow-up tracks it from here.
        conn.execute(text("UPDATE supply_event SET status = 'mitigated' WHERE event_id = :e AND status = 'open'"),
                     {"e": plan.event_id})
        audit_id = _audit(conn, by, "human", "Approved", plan_id,
                          {"role": role, "option": option, "type": kind, "note": note,
                           "actions": [{k: a[k] for k in ("action_id", "from_site", "to_site", "units")} for a in actions]},
                          refs, at)

    return {"approved": True, "plan_id": plan_id, "status": "approved", "option": option,
            "type": kind, "action_ids": [a["action_id"] for a in actions], "audit_id": audit_id}


def reject_plan(engine: Engine, plan_id: str, by: str, role: str, note: str, at: "datetime | None" = None) -> dict:
    """Reject a plan (any of its options). A reason is required. at: as in approve_plan."""

    if not by.strip():
        raise ApprovalError("A rejection needs the approver's name.")
    if not (note or "").strip():
        raise ApprovalError("A rejection needs a reason (--note).")
    if role not in ALL_ROLES:
        raise ApprovalError(f"{role} cannot decide plans; roles are {sorted(ALL_ROLES)}.")

    with engine.begin() as conn:
        plan = _lock_plan(conn, plan_id)
        if plan.status not in ("pending_approval", "proposed"):
            raise ApprovalError(f"Plan {plan_id} is {plan.status}; it can no longer be rejected.")
        conn.execute(text("""
            INSERT INTO decision (plan_id, actor, actor_role, decision, note, decided_at)
            VALUES (:plan_id, :actor, :role, 'rejected', :note, COALESCE(:at, now()))
        """), {"plan_id": plan_id, "actor": by, "role": role, "note": note, "at": at})
        conn.execute(text("UPDATE plan SET status = 'rejected' WHERE plan_id = :p"), {"p": plan_id})
        audit_id = _audit(conn, by, "human", "Rejected", plan_id, {"role": role, "note": note}, [plan_id, plan.event_id], at)

    return {"approved": False, "plan_id": plan_id, "status": "rejected", "audit_id": audit_id}


# ---------------------------------------------------------
# Command line
# ---------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Approve or reject agent plans")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Plans waiting for a decision")

    approve = sub.add_parser("approve", help="Approve one option of a plan")
    approve.add_argument("plan_id")
    approve.add_argument("--by", required=True, help="Approver's name")
    approve.add_argument("--role", required=True, choices=sorted(ALL_ROLES))
    approve.add_argument("--option", type=int, help="Option number (default: first that passed the checks)")
    approve.add_argument("--note")
    approve.add_argument("--as-of", type=date.fromisoformat, help="Re-check against stock from this day")

    reject = sub.add_parser("reject", help="Reject a plan")
    reject.add_argument("plan_id")
    reject.add_argument("--by", required=True)
    reject.add_argument("--role", required=True, choices=sorted(ALL_ROLES))
    reject.add_argument("--note", required=True, help="Reason")

    args = parser.parse_args(argv)

    from ingestion.lineage import get_engine
    from services.agents.db_data import PostgresData

    engine = get_engine()
    try:
        if args.command == "list":
            result = pending_plans(engine)
        elif args.command == "approve":
            result = approve_plan(engine, args.plan_id, args.by, args.role,
                                  PostgresData(engine, as_of=args.as_of), args.option, args.note)
        else:
            result = reject_plan(engine, args.plan_id, args.by, args.role, args.note)
    except ApprovalError as error:
        print(f"Not allowed: {error}", file=sys.stderr)
        return 2
    except ProgrammingError as error:
        if "does not exist" in str(error.orig):
            print("The database is missing tables or columns this needs. "
                  "Run: .venv/bin/alembic upgrade head", file=sys.stderr)
            return 3
        raise

    print(json.dumps(result, indent=2, default=str))
    return 0 if args.command == "list" or result.get("status") != "broken" else 1


if __name__ == "__main__":
    raise SystemExit(main())
