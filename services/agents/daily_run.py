"""One simulated day of the platform running on its own.

Everything a real deployment would do without anyone clicking anything, in order:

1. Ingest   that day's hospital data (stock, usage, orders). Safe to re-run.
2. Inbox    the supplier emails that arrive that day (scenarios/progression/inbox.json):
            security screen -> assessment agent -> risk -> planning -> checks -> saved.
            An email about a drug that already has an alert joins it, and its plan
            replaces the alert's waiting plan (emails refine plans, they don't fork them).
3. Watch    scan every drug at every site. A site under its drug's threshold
            (WATCH_DAYS: 14 days for critical drugs, 10 high, 7 others) with no
            delivery arriving before it runs out needs a plan. One living alert
            per drug: a new problem gets an alert and a plan; an existing alert is
            re-planned only when its plan no longer holds (new hospitals at risk,
            or it fails a re-check against today's stock). Critical drugs are
            always planned the same day; others share MAX_PLANS_PER_DAY.
4. Reports  Health Canada shortage reports for tracked drugs (shortage_signals.py).
5. Follow   check approved transfers at FOLLOWUP_HOUR (followup_db.py).
6. Orders   at ORDERS_HOUR the order agent (order_db.py) turns approved plan
            order options into purchase orders, places approved orders through the
            simulated supplier channel, reads that day's scripted supplier replies
            and follows every open order up; then the reorder scan drafts orders for
            sites heading under safety stock, for a person to approve. Skipped when
            no supplier catalog is loaded (order_db load-catalog).
7. Record   a "Daily run" audit entry summarising the day.

Approvals stay with people. Nothing is sent or executed.

Planner: "auto" uses the planning agent (Bedrock) when a model is configured and
the rules planner otherwise; "model" or "rules" force one. Reading emails always
needs the model (except hostile ones, which the security screen stops first).

  python -m services.agents.daily_run --day 2026-11-15
  python -m services.agents.daily_run --day 2026-11-16 --planner rules
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingestion.synthetic_loader import PROGRESSION_DAYS, load_folder
from services.agents.agent import process_email, process_event
from services.agents.db_data import PostgresData
from services.agents.followup_db import run_followup
from services.agents.results_store import open_event_for, save_pipeline_result
from services.agents.rules_planner import rules_planner
from services.agents.schemas import SupplyEvent
from services.agents.security import inspect_supplier_email
from services.agents.shortage_signals import run_shortage_signals

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
EMAILS = ROOT / "mock" / "emails"
INBOX = ROOT / "scenarios" / "progression" / "inbox.json"

URGENT_DAYS = 7          # the Stock page's red zone
# How early the watch acts, by how critical the drug is (days of stock left with
# no delivery arriving in time). Critical drugs get a plan two weeks ahead.
WATCH_DAYS = {"critical": 14, "high": 10, "medium": URGENT_DAYS, "low": URGENT_DAYS}
RESTORE_DAYS = 14        # stock-watch plans aim to restore two weeks of cover
SPIKE_RATIO = 1.25       # usage up 25% on the previous day counts as a spike
MAX_PLANS_PER_DAY = 3    # stock-watch alerts planned per day; the rest wait
PERSON_WATCH_DAYS = 14   # a person asking for a plan looks wider: the amber zone too
FOLLOWUP_HOUR = 18
ORDERS_HOUR = 20
SUPPLIER_REPLIES = ROOT / "mock" / "orders" / "supplier_replies.json"


def load_inbox(path: Path = INBOX) -> dict[str, list[str]]:
    if not path.is_file():
        return {}
    return {day: files for day, files in json.loads(path.read_text()).items() if not day.startswith("_")}


def model_available(llm) -> bool:
    return llm is not None or bool(os.getenv("BEDROCK_MODEL_ID"))


# ---------------------------------------------------------
# Stock watch (code only)
# ---------------------------------------------------------

def find_at_risk(data, previous, today: date, urgent_days: float | None = None, drugs=None) -> list[dict]:
    """Drugs with at least one site under its threshold and no delivery before it runs out.

    urgent_days: one threshold for every drug; default is by criticality (WATCH_DAYS).
    data / previous: today's and yesterday's data sources (PostgresData or CsvData).
    drugs: (din, name, criticality) rows; read from tracked_drug when not given.
    """

    if drugs is None:
        with data.engine.connect() as conn:
            drugs = conn.execute(text("SELECT din, name, criticality FROM tracked_drug ORDER BY din")).all()

    found = []
    for din, name, criticality in drugs:
        next_due: dict[str, int] = {}
        for order in data.orders(din):
            days = (date.fromisoformat(order["expected_date"]) - today).days
            next_due[order["site_id"]] = min(days, next_due.get(order["site_id"], days))
        before = {row["site_id"]: row["daily_use"] for row in previous.stock(din)} if previous else {}

        limit = urgent_days if urgent_days is not None else WATCH_DAYS.get(criticality, URGENT_DAYS)
        sites, spike = [], False
        for row in data.stock(din):
            if row["daily_use"] <= 0:
                continue
            days_left = row["on_hand"] / row["daily_use"]
            if days_left < limit and next_due.get(row["site_id"], 10**6) > days_left:
                sites.append({"site_id": row["site_id"], "site": row["site"], "days_left": round(days_left, 1),
                              "next_delivery_days": next_due.get(row["site_id"])})
                if before.get(row["site_id"]) and row["daily_use"] >= before[row["site_id"]] * SPIKE_RATIO:
                    spike = True
        if sites:
            found.append({"din": din, "name": name, "criticality": criticality, "sites": sites,
                          "kind": "usage_spike" if spike else "shortage",
                          "worst_days": min(s["days_left"] for s in sites)})

    # Most urgent first; critical drugs ahead of others at the same urgency.
    return sorted(found, key=lambda f: (f["worst_days"], f["criticality"] != "critical"))


def event_from_watch(alert: dict, raised_by: str = "Stock watch") -> SupplyEvent:
    parts = []
    for s in alert["sites"]:
        due = f"next delivery in {s['next_delivery_days']} days" if s["next_delivery_days"] is not None else "no delivery due"
        parts.append(f"{s['site']} has {s['days_left']} days left ({due})")
    cause = "Usage jumped since yesterday. " if alert["kind"] == "usage_spike" else ""
    return SupplyEvent(
        drug_din=alert["din"],
        drug_name=alert["name"],
        delay_weeks=RESTORE_DAYS / 7,
        confidence=1.0,
        next_action="plan",
        reason=(f"{raised_by}: {cause}{'; '.join(parts) or 'no site is under ' + str(PERSON_WATCH_DAYS) + ' days yet, planning ahead'}. "
                f"Planning to restore {RESTORE_DAYS} days of cover."),
    )


def living_event(engine: Engine, din: str) -> str | None:
    """The drug's current alert: still open, or handled (approved) but not closed. One per drug."""

    with engine.connect() as conn:
        return conn.execute(text("""
            SELECT event_id FROM supply_event
            WHERE din = :din AND status IN ('open', 'mitigated')
            ORDER BY created_at DESC LIMIT 1
        """), {"din": din}).scalar_one_or_none()


def plan_needs_redo(engine: Engine, data, event_id: str, alert: dict) -> str | None:
    """Why the alert's current plan no longer holds with today's stock, or None if it still does."""

    from services.agents.approvals import _options, recheck_transfer

    with engine.connect() as conn:
        plan = conn.execute(text("""
            SELECT p.plan_id, p.status, p.detail, d.option_index
            FROM plan p LEFT JOIN decision d ON d.plan_id = p.plan_id AND d.decision = 'approved'
            WHERE p.event_id = :e AND p.status NOT IN ('superseded', 'rejected')
            ORDER BY p.created_at DESC LIMIT 1
        """), {"e": event_id}).one_or_none()
        if plan is None:
            return "no current plan"
        options = _options(conn, plan.plan_id)

    planned_sites = set((plan.detail or {}).get("risk_sites") or [])
    new_sites = {s["site_id"] for s in alert["sites"]} - planned_sites
    if new_sites:
        return f"new hospitals at risk: {', '.join(sorted(new_sites))}"
    if plan.status == "broken":
        return "plan no longer safe"
    if plan.status == "approved":
        # People already acted on it; the follow-up agent owns it from here (nudge, escalate, replan).
        # Re-checking donors against later stock would replace a plan that is already under way.
        return None
    if plan.status == "pending_approval":
        candidates = [acts for acts in options.values() if all(a["verified"] for a in acts)]
    else:
        return None  # nothing could be verified; replanning on the same facts gives the same answer
    for actions in candidates:
        if not actions or actions[0]["type"] != "transfer":
            return None
        if not recheck_transfer(data, alert["din"], actions, plan.detail):
            return None
    return "plan no longer safe with today's stock"


def drug_has_open_event(engine: Engine, din: str) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM supply_event WHERE din = :din AND status = 'open' LIMIT 1"),
                            {"din": din}).first() is not None


# ---------------------------------------------------------
# The day
# ---------------------------------------------------------

def _record(engine: Engine, day: date, summary: dict) -> None:
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
            VALUES ('daily-run', 'system', 'Daily run', :day, CAST(:detail AS jsonb), :refs)
        """), {"day": day.isoformat(), "detail": json.dumps(summary, default=str), "refs": [day.isoformat()]})


def run_orders_for_day(engine: Engine, data, day: date, llm=None) -> dict:
    """The order agent's evening run: follow up and place first, then draft new reorders."""

    from services.agents.order_agent import SimulatedSupplierChannel
    from services.agents.order_db import load_catalog_from_db, run_orders, scan

    if load_catalog_from_db(engine) is None:
        return {"skipped": "no supplier catalog loaded"}
    when = datetime.combine(day, time(ORDERS_HOUR))
    channel = SimulatedSupplierChannel.from_file(SUPPLIER_REPLIES) if SUPPLIER_REPLIES.is_file() else None
    actions, raised = run_orders(engine, data, when, channel, llm)
    drafted = scan(engine, data, when)
    decisions: dict[str, int] = {}
    for action in actions:
        if action.decision != "wait":
            decisions[action.decision] = decisions.get(action.decision, 0) + 1
    return {"checked": len(actions), "decisions": decisions, "raised_events": len(raised),
            "drafted": len(drafted), "drafted_cost": round(sum(o.total_cost for o in drafted), 2)}


def run_day(
    write_engine: Engine,
    read_engine: Engine,
    day: date,
    llm=None,
    planner: str = "auto",
    max_plans: int = MAX_PLANS_PER_DAY,
    inbox: dict[str, list[str]] | None = None,
    days_root: Path = PROGRESSION_DAYS,
) -> dict:
    """Run one simulated day. Returns a summary of what happened (also written to audit_log)."""

    has_model = model_available(llm)
    use_rules = planner == "rules" or (planner == "auto" and not has_model)
    code_planner = rules_planner if use_rules else None
    inbox = load_inbox() if inbox is None else inbox
    summary: dict = {"day": day.isoformat(), "planner": "rules" if use_rules else "planning agent"}

    # 1. Ingest
    folder = Path(days_root) / day.isoformat()
    summary["ingested"] = load_folder(write_engine, folder)["rows_ok"] if folder.is_dir() else 0

    data = PostgresData(read_engine, as_of=day)

    # 2. Inbox
    emails = []
    for name in inbox.get(day.isoformat(), []):
        path = EMAILS / Path(name).name
        source_ref = str(path.relative_to(ROOT))
        if not path.is_file():
            emails.append({"email": name, "status": "missing"})
            continue
        if open_event_for(write_engine, source_ref):
            emails.append({"email": name, "status": "already open"})
            continue
        body = path.read_text(encoding="utf-8")
        if not has_model and not inspect_supplier_email(body).suspicious:
            emails.append({"email": name, "status": "waiting: reading emails needs the model"})
            continue
        result = process_email(body, data=data, llm=llm, today=day)
        din = (result.get("assessment") or {}).get("drug_din")
        existing = living_event(write_engine, din) if din else None
        save_pipeline_result(write_engine, result, source_ref=source_ref, event_id=existing)
        emails.append({"email": name, "status": result["status"] + (" (updated the drug's existing alert)" if existing else "")})
    summary["emails"] = emails

    # 3. Stock watch
    previous = PostgresData(read_engine, as_of=day - timedelta(days=1))
    to_plan, unchanged = [], 0
    for alert in find_at_risk(data, previous, day):
        existing = living_event(write_engine, alert["din"])
        if existing is None:
            to_plan.append((alert, None, "new"))
            continue
        reason = plan_needs_redo(write_engine, data, existing, alert)
        if reason:
            to_plan.append((alert, existing, reason))
        else:
            unchanged += 1

    # Critical drugs are always planned the same day; others share the daily cap.
    planned, others_used, waiting = [], 0, 0
    for alert, existing, why in to_plan:
        if alert["criticality"] != "critical":
            if others_used >= max_plans:
                waiting += 1
                continue
            others_used += 1
        result = process_event(event_from_watch(alert), data=data, llm=llm, today=day, planner=code_planner,
                               restore=True)
        save_pipeline_result(
            write_engine, result, source_ref=f"stock_watch:{day.isoformat()}:{alert['din']}", actor="stock-watch",
            source_type="stock_watch", kind=alert["kind"],
            site_id=alert["sites"][0]["site_id"] if len(alert["sites"]) == 1 else None,
            event_id=existing,
        )
        planned.append({"drug": alert["name"], "criticality": alert["criticality"],
                        "sites": [s["site_id"] for s in alert["sites"]], "kind": alert["kind"],
                        "why": why, "status": result["status"]})
    summary["stock_watch"] = {"alerts": planned, "unchanged": unchanged, "waiting": waiting}

    # 4. Health Canada shortage reports
    reports = run_shortage_signals(write_engine, data, llm=llm, today=day, planner=code_planner)
    reports.pop("saved", None)
    summary["shortage_reports"] = reports

    # 5. Follow-up on approved transfers
    actions = run_followup(write_engine, data, datetime.combine(day, time(FOLLOWUP_HOUR)))
    summary["followup"] = [{"transfer": a.request_id, "decision": a.decision} for a in actions]

    # 6. Supplier orders
    summary["orders"] = run_orders_for_day(write_engine, data, day, llm if has_model else None)

    with read_engine.connect() as conn:
        summary["waiting_for_approval"] = conn.execute(
            text("SELECT count(*) FROM plan WHERE status = 'pending_approval'")).scalar_one()

    _record(write_engine, day, summary)
    return summary


def plan_for_drug(
    write_engine: Engine,
    read_engine: Engine,
    din: str,
    as_of: date,
    by: str,
    site_id: str | None = None,
    llm=None,
    planner: str = "auto",
) -> dict:
    """A person (the supply manager) asks for a plan for one drug now, without waiting
    for an email or the stock watch. Same risk calculation, planner and checks.

    Returns {"status": "already_open", "event_id": ...} if the drug already has an
    open alert, so the caller can open that plan instead of duplicating it.
    """

    with read_engine.connect() as conn:
        drug = conn.execute(text("SELECT din, name, criticality FROM tracked_drug WHERE din = :din"),
                            {"din": din}).one_or_none()
    if drug is None:
        raise LookupError(f"Unknown drug {din}")
    existing = living_event(write_engine, din)
    if existing:
        return {"status": "already_open", "event_id": existing}

    use_rules = planner == "rules" or (planner == "auto" and not model_available(llm))
    data = PostgresData(read_engine, as_of=as_of)
    previous = PostgresData(read_engine, as_of=as_of - timedelta(days=1))
    found = find_at_risk(data, previous, as_of, urgent_days=PERSON_WATCH_DAYS, drugs=[tuple(drug)])
    alert = found[0] if found else {"din": din, "name": drug.name, "sites": [], "kind": "shortage"}
    if site_id and any(s["site_id"] == site_id for s in alert["sites"]):
        alert = {**alert, "sites": [s for s in alert["sites"] if s["site_id"] == site_id]}

    with write_engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
            VALUES (:by, 'human', 'Requested a plan', :din, CAST(:detail AS jsonb), :refs)
        """), {"by": by, "din": din, "refs": [din],
               "detail": json.dumps({"drug": drug.name, "site": site_id, "day": as_of.isoformat(), "role": "supply_manager"})})

    result = process_event(event_from_watch(alert, raised_by=f"Raised by {by}"), data=data, llm=llm, today=as_of,
                           planner=rules_planner if use_rules else None, restore=True)
    saved = save_pipeline_result(
        write_engine, result, source_ref=f"manual:{as_of.isoformat()}:{din}",
        actor="rules-planner" if use_rules else "planning-agent", source_type="manual", kind=alert["kind"],
        site_id=site_id or (alert["sites"][0]["site_id"] if len(alert["sites"]) == 1 else None),
    )
    return {"status": result["status"], "event_id": saved["event_id"], "plan_id": saved["plan_id"],
            "planner": "rules" if use_rules else "planning agent", "shortfall": result.get("shortfall", {})}


def days_run(engine: Engine) -> list[str]:
    with engine.connect() as conn:
        return [row[0] for row in conn.execute(text(
            "SELECT DISTINCT object FROM audit_log WHERE action = 'Daily run' ORDER BY object"))]


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run one simulated day of the platform")
    parser.add_argument("--day", type=date.fromisoformat, required=True)
    parser.add_argument("--planner", choices=["auto", "model", "rules"], default="auto")
    parser.add_argument("--max-plans", type=int, default=MAX_PLANS_PER_DAY)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import create_engine

    from ingestion.lineage import get_engine

    write = get_engine()
    read = create_engine(os.environ.get("DATABASE_URL_READONLY") or os.environ["DATABASE_URL"])
    print(json.dumps(run_day(write, read, args.day, planner=args.planner, max_plans=args.max_plans), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
