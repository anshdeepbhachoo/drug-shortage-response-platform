"""One simulated day of the platform running on its own.

Everything a real deployment would do without anyone clicking anything, in order:

1. Ingest   that day's hospital data (stock, usage, orders). Safe to re-run.
2. Inbox    the supplier emails that arrive that day (data/synthetic/progression/inbox.json):
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
6. Record   a "Daily run" audit entry summarising the day.

Approvals stay with people. Nothing is sent or executed.

Planner: "auto" uses the planning agent (Bedrock) when a model is configured and
the rules planner otherwise; "model" or "rules" force one. Reading emails always
needs the model (except hostile ones, which the security screen stops first).

  python -m services.agents.pipeline.daily_run --day 2026-11-15
  python -m services.agents.pipeline.daily_run --day 2026-11-16 --planner rules
  python -m services.agents.pipeline.daily_run --day 2026-11-16 --orchestrator classic

Orchestration: the steps run as a LangGraph graph by default (services/agents/pipeline/graph/daily.py);
--orchestrator classic (or PIPELINE_ORCHESTRATOR=classic) runs the same steps in plain Python order.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from ingestion.synthetic_loader import PROGRESSION_DAYS, load_folder
from services.agents.agents.signal_agents import process_email, process_event
from services.agents.data.db_data import PostgresData
from services.agents.store.followup_db import approved_transfers, run_followup
from services.agents.agents.order_agent import approved_orders, resolve_model, run_order_followup
from services.agents.store.results_store import open_event_for, save_pipeline_result
from services.agents.rules.rules_planner import rules_planner
from services.agents.agents.schemas import SupplyEvent
from services.agents.rules.security import inspect_supplier_email
from services.agents.pipeline.shortage_signals import run_shortage_signals

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
SYNTHETIC = ROOT / "data" / "synthetic"
EMAILS = SYNTHETIC / "mock" / "emails"
INBOX = SYNTHETIC / "progression" / "inbox.json"

URGENT_DAYS = 7          # the Stock page's red zone
# How early the watch acts, by how critical the drug is (days of stock left with
# no delivery arriving in time). Critical drugs get a plan two weeks ahead.
WATCH_DAYS = {"critical": 14, "high": 10, "medium": URGENT_DAYS, "low": URGENT_DAYS}
RESTORE_DAYS = 14        # stock-watch plans aim to restore two weeks of cover
SPIKE_RATIO = 1.25       # usage up 25% on the previous day counts as a spike
MAX_PLANS_PER_DAY = 3    # stock-watch alerts planned per day; the rest wait
PERSON_WATCH_DAYS = 14   # a person asking for a plan looks wider: the amber zone too
FOLLOWUP_HOUR = 18


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

    from services.agents.store.approvals import _options, option_kind, recheck_combined, recheck_order, recheck_transfer

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
    today = getattr(data, "as_of", None) or date.today()
    for actions in candidates:
        kind = option_kind(actions) if actions else None
        if kind == "transfer" and recheck_transfer(data, alert["din"], actions, plan.detail):
            continue
        if kind == "order" and recheck_order(data, alert["din"], actions, today):
            continue
        if kind == "transfer_order" and recheck_combined(data, alert["din"], actions, plan.detail, today):
            continue
        return None  # this option still holds
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


@dataclass
class DayContext:
    """Everything one simulated day's steps share. Built once per run, never stored."""

    write_engine: Engine
    read_engine: Engine
    day: date
    llm: object = None
    planner: str = "auto"
    max_plans: int = MAX_PLANS_PER_DAY
    inbox: dict[str, list[str]] | None = None
    days_root: Path = PROGRESSION_DAYS
    has_model: bool = field(init=False)
    use_rules: bool = field(init=False)
    # The LangGraph agentic pipeline (services/agents/pipeline/graph/pipeline.py). None: the classic code path.
    pipeline: object = field(default=None, init=False)

    def __post_init__(self):
        self.has_model = model_available(self.llm)
        self.use_rules = self.planner == "rules" or (self.planner == "auto" and not self.has_model)
        if self.inbox is None:
            self.inbox = load_inbox()

    @property
    def code_planner(self):
        return rules_planner if self.use_rules else None

    @property
    def data(self) -> PostgresData:
        return PostgresData(self.read_engine, as_of=self.day)


# ---------------------------------------------------------
# The day's steps. run_day calls them in order; the LangGraph
# pipeline (services/agents/pipeline/graph/daily.py) calls the same functions
# as graph nodes, so both orchestrators do exactly the same work.
# ---------------------------------------------------------

def step_ingest(ctx: DayContext) -> dict:
    """1. Load that day's hospital data (stock, usage, orders). Safe to re-run."""

    folder = Path(ctx.days_root) / ctx.day.isoformat()
    return {"ingested": load_folder(ctx.write_engine, folder)["rows_ok"] if folder.is_dir() else 0}


def step_inbox(ctx: DayContext) -> dict:
    """2. Supplier emails that arrive today: screen, assessment agent, plan, save (joining the drug's alert)."""

    data = ctx.data
    emails = []
    for name in ctx.inbox.get(ctx.day.isoformat(), []):
        path = EMAILS / Path(name).name
        source_ref = path.relative_to(SYNTHETIC).as_posix()  # "mock/emails/<name>", as stored before the move
        if not path.is_file():
            emails.append({"email": name, "status": "missing"})
            continue
        if open_event_for(ctx.write_engine, source_ref):
            emails.append({"email": name, "status": "already open"})
            continue
        body = path.read_text(encoding="utf-8")
        if not ctx.has_model and not inspect_supplier_email(body).suspicious:
            emails.append({"email": name, "status": "waiting: reading emails needs the model"})
            continue
        if ctx.pipeline is not None:
            # Agentic pipeline: intake graph (screen, assessment agent), then the drug's alert graph.
            notice = ctx.pipeline.read_notice(body, ctx.day)
            if notice["outcome"] == "plan":
                din = notice["event"]["drug_din"]
                existing = living_event(ctx.write_engine, din)
                trigger = {"event": notice["event"], "save": {"source_ref": source_ref}, "agent_planner": True}
                result = ctx.pipeline.signal(din, trigger, ctx.day)["result"]
            else:
                result = notice["result"]
                din = (result.get("assessment") or {}).get("drug_din")
                existing = living_event(ctx.write_engine, din) if din else None
                save_pipeline_result(ctx.write_engine, result, source_ref=source_ref, event_id=existing)
            emails.append({"email": name, "status": result["status"] + (" (updated the drug's existing alert)" if existing else "")})
            continue
        result = process_email(body, data=data, llm=ctx.llm, today=ctx.day)
        din = (result.get("assessment") or {}).get("drug_din")
        existing = living_event(ctx.write_engine, din) if din else None
        save_pipeline_result(ctx.write_engine, result, source_ref=source_ref, event_id=existing)
        emails.append({"email": name, "status": result["status"] + (" (updated the drug's existing alert)" if existing else "")})
    return {"emails": emails}


def step_detect(ctx: DayContext) -> dict:
    """3a. Stock watch (code only): which drugs need a new or redone plan today."""

    data = ctx.data
    previous = PostgresData(ctx.read_engine, as_of=ctx.day - timedelta(days=1))
    to_plan, unchanged = [], 0
    for alert in find_at_risk(data, previous, ctx.day):
        existing = living_event(ctx.write_engine, alert["din"])
        if existing is None:
            to_plan.append({"alert": alert, "existing": None, "why": "new"})
            continue
        reason = plan_needs_redo(ctx.write_engine, data, existing, alert)
        if reason:
            to_plan.append({"alert": alert, "existing": existing, "why": reason})
        else:
            unchanged += 1
    return {"to_plan": to_plan, "unchanged": unchanged}


def step_plan_alerts(ctx: DayContext, to_plan: list[dict], unchanged: int) -> dict:
    """3b. Plan each alert (planning agent or rules planner, then code checks). Critical drugs always
    the same day; others share the daily cap."""

    data = ctx.data
    planned, others_used, waiting = [], 0, 0
    for item in to_plan:
        alert, existing, why = item["alert"], item["existing"], item["why"]
        if alert["criticality"] != "critical":
            if others_used >= ctx.max_plans:
                waiting += 1
                continue
            others_used += 1
        save = {"source_ref": f"stock_watch:{ctx.day.isoformat()}:{alert['din']}", "actor": "stock-watch",
                "source_type": "stock_watch", "kind": alert["kind"],
                "site_id": alert["sites"][0]["site_id"] if len(alert["sites"]) == 1 else None}
        if ctx.pipeline is not None:
            trigger = {"event": event_from_watch(alert).model_dump(), "save": save, "restore": True}
            result = ctx.pipeline.signal(alert["din"], trigger, ctx.day)["result"]
        else:
            result = process_event(event_from_watch(alert), data=data, llm=ctx.llm, today=ctx.day,
                                   planner=ctx.code_planner, restore=True)
            save_pipeline_result(ctx.write_engine, result, event_id=existing, **save)
        planned.append({"drug": alert["name"], "criticality": alert["criticality"],
                        "sites": [s["site_id"] for s in alert["sites"]], "kind": alert["kind"],
                        "why": why, "status": result["status"]})
    return {"stock_watch": {"alerts": planned, "unchanged": unchanged, "waiting": waiting}}


def step_reports(ctx: DayContext) -> dict:
    """4. Health Canada shortage reports for tracked drugs."""

    reports = run_shortage_signals(ctx.write_engine, ctx.data, llm=ctx.llm, today=ctx.day, planner=ctx.code_planner,
                                   pipeline=ctx.pipeline)
    reports.pop("saved", None)
    return {"shortage_reports": reports}


def step_followup(ctx: DayContext) -> dict:
    """5. Follow up on approved transfers and approved orders in the evening."""

    now = datetime.combine(ctx.day, time(FOLLOWUP_HOUR))
    order_model = resolve_model(ctx.llm, ctx.use_rules)
    if ctx.pipeline is None:
        actions = run_followup(ctx.write_engine, ctx.data, now)
        checks = [{"transfer": a.request_id, "decision": a.decision} for a in actions]
        checks += [{"transfer": a.request_id, "decision": a.decision, "kind": "order"}
                   for a in run_order_followup(ctx.write_engine, ctx.data, now, order_model)]
        return {"followup": checks}

    # Agentic pipeline: resume each drug's alert paused at follow-up (transfers) or order follow-up.
    # Alerts approved before the pipeline existed have no paused graph, so they are followed up directly.
    requests = approved_transfers(ctx.write_engine)
    orders = approved_orders(ctx.write_engine)
    position = {r.request_id: i for i, r in enumerate(requests)} | {o.order_id: len(requests) + i for i, o in enumerate(orders)}
    checks: list[dict] = []
    for din in dict.fromkeys([r.din for r in requests] + [o.din for o in orders]):
        resumed = ctx.pipeline.tick(din, ctx.day, now)
        if resumed is not None:
            checks += resumed
            continue
        for plan_id in dict.fromkeys(r.plan_id for r in requests if r.din == din):
            checks += [{"transfer": a.request_id, "decision": a.decision}
                       for a in run_followup(ctx.write_engine, ctx.data, now, plan_id=plan_id)]
        for plan_id in dict.fromkeys(o.plan_id for o in orders if o.din == din):
            checks += [{"transfer": a.request_id, "decision": a.decision, "kind": "order"}
                       for a in run_order_followup(ctx.write_engine, ctx.data, now, order_model, plan_id=plan_id)]
    return {"followup": sorted(checks, key=lambda c: position.get(c["transfer"], len(position)))}


def step_record(ctx: DayContext, summary: dict) -> dict:
    """6. Count what waits for people and write the day's audit entry."""

    with ctx.read_engine.connect() as conn:
        waiting = conn.execute(text("SELECT count(*) FROM plan WHERE status = 'pending_approval'")).scalar_one()
    summary = {**summary, "waiting_for_approval": waiting}
    _record(ctx.write_engine, ctx.day, summary)
    return summary


def uses_graph(orchestrator: str | None = None) -> bool:
    """Whether to run the LangGraph orchestration (default) or the classic code path."""

    return (orchestrator or os.getenv("PIPELINE_ORCHESTRATOR", "graph")) == "graph"


def summary_of(ctx: DayContext, parts: dict) -> dict:
    """The day's summary, in the order the steps ran (the same shape whichever orchestrator ran them)."""

    return {
        "day": ctx.day.isoformat(),
        "planner": "rules" if ctx.use_rules else "planning agent",
        "ingested": parts["ingested"],
        "emails": parts["emails"],
        "stock_watch": parts["stock_watch"],
        "shortage_reports": parts["shortage_reports"],
        "followup": parts["followup"],
    }


def run_day(
    write_engine: Engine,
    read_engine: Engine,
    day: date,
    llm=None,
    planner: str = "auto",
    max_plans: int = MAX_PLANS_PER_DAY,
    inbox: dict[str, list[str]] | None = None,
    days_root: Path = PROGRESSION_DAYS,
    orchestrator: str | None = None,
) -> dict:
    """Run one simulated day. Returns a summary of what happened (also written to audit_log).

    orchestrator: "graph" runs the steps as a LangGraph graph (the default), "classic" runs them in
    plain Python order. Both call the same step functions. Default from PIPELINE_ORCHESTRATOR.
    """

    ctx = DayContext(write_engine, read_engine, day, llm, planner, max_plans, inbox, days_root)
    if uses_graph(orchestrator):
        from services.agents.pipeline.graph.daily import run_daily_graph
        from services.agents.pipeline.graph.pipeline import AgentPipeline

        ctx.pipeline = AgentPipeline(write_engine, read_engine, llm, ctx.use_rules)
        return run_daily_graph(ctx)

    parts = {**step_ingest(ctx), **step_inbox(ctx)}
    detected = step_detect(ctx)
    parts |= step_plan_alerts(ctx, detected["to_plan"], detected["unchanged"])
    parts |= step_reports(ctx)
    parts |= step_followup(ctx)
    return step_record(ctx, summary_of(ctx, parts))


def plan_for_drug(
    write_engine: Engine,
    read_engine: Engine,
    din: str,
    as_of: date,
    by: str,
    site_id: str | None = None,
    llm=None,
    planner: str = "auto",
    orchestrator: str | None = None,
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

    event = event_from_watch(alert, raised_by=f"Raised by {by}")
    save = {"source_ref": f"manual:{as_of.isoformat()}:{din}", "actor": "rules-planner" if use_rules else "planning-agent",
            "source_type": "manual", "kind": alert["kind"],
            "site_id": site_id or (alert["sites"][0]["site_id"] if len(alert["sites"]) == 1 else None)}
    if uses_graph(orchestrator):
        from services.agents.pipeline.graph.pipeline import AgentPipeline

        final = AgentPipeline(write_engine, read_engine, llm, use_rules).signal(
            din, {"event": event.model_dump(), "save": save, "restore": True, "join": False}, as_of)
        result, saved = final["result"], {"event_id": final.get("alert_id"), "plan_id": final.get("plan_id")}
    else:
        result = process_event(event, data=data, llm=llm, today=as_of, planner=rules_planner if use_rules else None,
                               restore=True)
        saved = save_pipeline_result(write_engine, result, **save)
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
    parser.add_argument("--orchestrator", choices=["graph", "classic"], default=None,
                        help="graph: LangGraph (default, or PIPELINE_ORCHESTRATOR); classic: plain Python order")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from sqlalchemy import create_engine

    from ingestion.lineage import get_engine

    write = get_engine()
    read = create_engine(os.environ.get("DATABASE_URL_READONLY") or os.environ["DATABASE_URL"])
    print(json.dumps(run_day(write, read, args.day, planner=args.planner, max_plans=args.max_plans,
                                orchestrator=args.orchestrator), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
