"""Run the follow-up agent on the database instead of CSV/JSON files.

Aryan's FollowUpAgent (followup.py) is used unchanged: same rules, model checks,
fallbacks and replanning. This module only feeds it and records what it does:

- Transfers to watch: every transfer action of an *approved* option
  (decision.option_index -> plan_action), instead of transfer_requests.csv.
  The deadline is the approval time plus DEFAULT_DEADLINE_HOURS.
- Stock: any data source with CsvData's methods, normally PostgresData.
- Record: each check is written to audit_log (actor "follow-up-agent"),
  instead of data/followup/audit.jsonl.
- State across runs (nudges sent, units confirmed, closed/escalated/replanned)
  is rebuilt from those audit_log entries, so every run continues where the
  last one stopped. A closed, escalated or replanned transfer is not checked again.

Nothing is sent or executed; drafts and escalations wait for a person.

  python -m services.agents.store.followup_db --now 2026-11-16T18:00 --as-of 2026-11-15
  python -m services.agents.store.followup_db --now 2026-11-16T18:00 --as-of 2026-11-15 --replies replies.json --live
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, time, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.agents.agents.followup import FollowUpAgent, load_replies
from services.agents.agents.followup_schemas import FollowUpAction, SiteReply, TransferRequest

logger = logging.getLogger(__name__)

ACTOR = "follow-up-agent"
DEFAULT_DEADLINE_HOURS = 24
# Decisions after which the agent stops watching a transfer (as FollowUpAgent.check sets them).
FINAL_STATUS = {"replan": "replanned", "escalate": "escalated", "close": "closed"}


def _naive_utc(value: datetime) -> datetime:
    """FollowUpAgent works in naive datetimes; store and compare them as UTC."""

    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


# ---------------------------------------------------------
# Reading: approved transfers and their saved follow-up history
# ---------------------------------------------------------

def approved_transfers(engine: Engine, deadline_hours: float = DEFAULT_DEADLINE_HOURS,
                       plan_id: str | None = None) -> list[TransferRequest]:
    """One TransferRequest per move of every approved transfer option (or only plan_id's)."""

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT a.action_id, a.plan_id, e.din, a.from_site, a.to_site, a.units,
                   d.actor_role, d.decided_at
            FROM decision d
            JOIN plan p ON p.plan_id = d.plan_id
            JOIN supply_event e ON e.event_id = p.event_id
            JOIN plan_action a ON a.plan_id = d.plan_id AND a.option_index = d.option_index
            WHERE d.decision = 'approved' AND p.status = 'approved' AND a.type = 'transfer'
              AND (CAST(:plan_id AS text) IS NULL OR d.plan_id = :plan_id)
            ORDER BY d.decided_at, a.action_id
        """), {"plan_id": plan_id}).all()

    return [
        TransferRequest(
            request_id=row.action_id,
            plan_id=row.plan_id,
            din=row.din,
            from_site=row.from_site,
            to_site=row.to_site,
            units=row.units,
            approval_status="approved",
            approver_role=row.actor_role,  # escalations go back to the role that approved it
            deadline=_naive_utc(row.decided_at) + timedelta(hours=deadline_hours),
        )
        for row in rows
    ]


def saved_history(engine: Engine, request_ids: list[str]) -> dict[str, list[dict]]:
    """Earlier follow-up records per transfer, oldest first."""

    history: dict[str, list[dict]] = {request_id: [] for request_id in request_ids}
    if not request_ids:
        return history
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT object, detail FROM audit_log
            WHERE actor = :actor AND object = ANY(CAST(:ids AS text[]))
            ORDER BY audit_id
        """), {"actor": ACTOR, "ids": request_ids}).all()
    for row in rows:
        history[row.object].append(row.detail)
    return history


def restore_state(agent: FollowUpAgent, history: dict[str, list[dict]]) -> None:
    """Put the agent back where the last run left it."""

    for request_id, records in history.items():
        if not records:
            continue
        request = agent.requests[request_id]
        agent.history[request_id] = [FollowUpAction.model_validate(record) for record in records]
        nudges = [record for record in records if record["decision"] == "nudge"]
        agent.nudges[request_id] = len(nudges)
        if nudges:
            request.last_nudged_at = datetime.fromisoformat(nudges[-1]["at"])
        request.units_confirmed = max(record["facts"]["units_confirmed"] for record in records)
        final = [record for record in records if record["decision"] in FINAL_STATUS]
        if final:
            request.status = FINAL_STATUS[final[-1]["decision"]]

    times = [datetime.fromisoformat(record["at"]) for records in history.values() for record in records]
    if times:
        agent.last_check = max(times)


# ---------------------------------------------------------
# Writing: every follow-up check goes to audit_log
# ---------------------------------------------------------

def audit_writer(engine: Engine, requests: dict[str, TransferRequest]):
    def write(record: dict) -> None:
        request = requests[record["request_id"]]
        with engine.begin() as conn:
            conn.execute(text("""
                INSERT INTO audit_log (actor, actor_type, action, object, detail, refs)
                VALUES (:actor, :actor_type, :action, :object, CAST(:detail AS jsonb), :refs)
            """), {
                "actor": ACTOR,
                # The model chose ("agent"), or a rule did (hard rule or fallback).
                "actor_type": "agent" if record["decided_by"] == "agent" else "rule",
                "action": f"Follow-up: {record['decision']}",
                "object": record["request_id"],
                "detail": json.dumps(record, default=str),
                "refs": [record["request_id"], request.plan_id],
            })
    return write


# ---------------------------------------------------------
# Running
# ---------------------------------------------------------

def stock_snapshot_time(engine: Engine, data) -> datetime:
    """The moment the stock figures describe: the as_of day, else the latest inventory snapshot."""

    as_of = getattr(data, "as_of", None)
    if as_of is None:
        with engine.connect() as conn:
            as_of = conn.execute(text("SELECT max(as_of) FROM inventory_snapshot")).scalar_one()
    if as_of is None:
        raise RuntimeError("No inventory snapshots loaded; run ingestion.synthetic_loader first.")
    return datetime.combine(as_of, time())


def build_agent(engine: Engine, data, llm=None, planner=None, deadline_hours: float = DEFAULT_DEADLINE_HOURS,
                plan_id: str | None = None) -> FollowUpAgent:
    requests = approved_transfers(engine, deadline_hours, plan_id)
    by_id = {request.request_id: request for request in requests}
    agent = FollowUpAgent(
        data,
        requests,
        stock_snapshot_time(engine, data),
        llm=llm,
        planner=planner,
        audit_writer=audit_writer(engine, by_id),
    )
    restore_state(agent, saved_history(engine, list(by_id)))
    return agent


def run_followup(
    engine: Engine,
    data,
    now: datetime,
    replies: list[SiteReply] = (),
    llm=None,
    planner=None,
    deadline_hours: float = DEFAULT_DEADLINE_HOURS,
    plan_id: str | None = None,
) -> list[FollowUpAction]:
    """Check every open approved transfer at `now` (or only plan_id's). Returns this run's actions."""

    agent = build_agent(engine, data, llm, planner, deadline_hours, plan_id)
    return agent.tick(_naive_utc(now), list(replies))


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Follow up approved transfers from the database")
    parser.add_argument("--now", type=datetime.fromisoformat, default=None,
                        help="Check time, e.g. 2026-11-16T18:00 (default: now, UTC)")
    parser.add_argument("--as-of", type=lambda v: datetime.fromisoformat(v).date(),
                        help="Stock snapshot day to project from (default: latest loaded)")
    parser.add_argument("--replies", help="JSON file of site replies (same format as mock/followup/replies.json)")
    parser.add_argument("--deadline-hours", type=float, default=DEFAULT_DEADLINE_HOURS)
    parser.add_argument("--live", action="store_true", help="Use Bedrock for reading replies, decisions and replans")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ingestion.lineage import get_engine
    from services.agents.data.db_data import PostgresData

    llm = planner = None
    if args.live:
        from services.agents.agents.signal_agents import run_agent
        from services.agents.agents.followup import bedrock_planner

        llm = lambda system, task, registry: run_agent(system, task, registry)
        planner = bedrock_planner

    engine = get_engine()
    data = PostgresData.from_env(as_of=args.as_of)
    now = args.now or datetime.now(timezone.utc)
    replies = load_replies(args.replies) if args.replies else []

    actions = run_followup(engine, data, now, replies, llm, planner, args.deadline_hours)
    if not actions:
        print("No open approved transfers to check.")
    for action in actions:
        print(f"{action.request_id}: {action.decision.upper()} by {action.decided_by} | {action.reason}")
        if action.message_draft:
            print(f"    draft to {action.message_to}: {action.message_draft}")
    print("Nothing was sent or executed. Every nudge, replan and escalation waits for a person.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
