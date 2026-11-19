"""Simulated-clock demo for the follow-up agent.

    python -m services.agents.followup.demo            # offline: rules + scripted replies
    python -m services.agents.followup.demo --live     # Bedrock reader, decider and planner
    python -m services.agents.followup.demo --json     # machine-readable output
    python -m services.agents.followup.demo --trace    # also print every step

Every run writes (both gitignored, under data/):
    data/followup/audit.jsonl   one JSON line per decision: facts, reading, allowed
                                choices, decision, who decided, trace, drafts
    data/followup/followup.log  timestamped log of every step, warnings and tool calls

The clock steps through four evenings starting at the inventory snapshot.
"""

import argparse
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from services.data.data import CsvData
from services.agents.followup.agent import (
    FollowUpAgent,
    bedrock_planner,
    load_replies,
    load_requests,
)

ROOT = Path(__file__).resolve().parents[3]
MOCK = ROOT / "data" / "synthetic" / "mock"
FOLLOWUP = MOCK / "followup"

LOG_DIR = ROOT / "data" / "followup"
AUDIT_PATH = LOG_DIR / "audit.jsonl"
LOG_PATH = LOG_DIR / "followup.log"

ICONS = {"wait": "..", "nudge": ">>", "replan": "<>", "escalate": "!!", "close": "ok"}


def setup_logging():
    """Send all log lines to a file so the console stays readable. Fresh files each run."""

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    AUDIT_PATH.unlink(missing_ok=True)
    handler = logging.FileHandler(LOG_PATH, mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    for existing in list(root.handlers):  # live mode may have added a console handler
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def build_agent(live: bool, audit_path=None):
    load_dotenv()
    snapshot = datetime.fromisoformat(os.getenv("MOCK_TODAY", "2026-11-15"))
    requests = load_requests(FOLLOWUP / "transfer_requests.csv")

    llm = planner = None
    if live:
        from services.agents.bedrock import run_agent

        setup_logging()  # make sure the file log is still in place after importing the Bedrock module

        llm = lambda system, task, registry: run_agent(system, task, registry)
        planner = bedrock_planner

    agent = FollowUpAgent(
        CsvData(MOCK), requests, snapshot, llm=llm, planner=planner, audit_path=audit_path
    )
    return agent, snapshot


def run(live: bool = False, days: int = 4, audit_path=None):
    agent, snapshot = build_agent(live, audit_path)
    replies = load_replies(FOLLOWUP / "replies.json")
    timeline = []

    for day in range(days):
        now = snapshot + timedelta(days=day, hours=18)
        timeline.append((now, agent.tick(now, replies)))

    return agent, timeline


def print_timeline(timeline, show_trace=False):
    for now, actions in timeline:
        print(f"\n=== {now:%a %b %d, %H:%M} ===")
        if not actions:
            print("  (no open transfers)")
        for action in actions:
            facts = action.facts
            print(
                f"  [{ICONS[action.decision]}] {action.request_id} {action.decision.upper():8} "
                f"by {action.decided_by:8} | overdue {facts['hours_overdue']:>5}h | "
                f"outstanding {facts['units_outstanding']:>3} | "
                f"cover {facts['receiving_cover_days']} d"
            )
            print(f"       {action.reason}")
            if show_trace:
                for line in action.trace:
                    print(f"         - {line}")
            for flag in action.flags:
                print(f"       FLAG: {flag}")
            if action.message_draft:
                print(f"       draft to {action.message_to}: {action.message_draft}")
            if action.replan:
                for option in action.replan["verified_options"]:
                    print(f"       replan option ({action.replan['source']}): {option['description']}")
    print("\nNothing was sent or executed. Every nudge, replan and escalation waits for a human.")
    print(f"Audit log: {AUDIT_PATH.relative_to(ROOT)}   Step log: {LOG_PATH.relative_to(ROOT)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Bedrock models.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--trace", action="store_true", help="Print every step.")
    parser.add_argument("--days", type=int, default=4)
    args = parser.parse_args()

    setup_logging()
    _, timeline = run(live=args.live, days=args.days, audit_path=AUDIT_PATH)

    if args.json:
        print(json.dumps(
            [
                {"at": now.isoformat(), "actions": [a.model_dump(mode="json") for a in actions]}
                for now, actions in timeline
            ],
            indent=2,
        ))
    else:
        print_timeline(timeline, show_trace=args.trace)


if __name__ == "__main__":
    main()
