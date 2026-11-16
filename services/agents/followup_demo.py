"""Simulated-clock demo for the follow-up agent.

    python -m services.agents.followup_demo            # offline: rules + scripted replies
    python -m services.agents.followup_demo --live     # Bedrock reader, decider and planner
    python -m services.agents.followup_demo --json     # machine-readable output

The clock steps through four evenings starting at the inventory snapshot.
"""

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from services.agents.data import CsvData
from services.agents.followup import (
    FollowUpAgent,
    bedrock_planner,
    load_replies,
    load_requests,
)

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "mock"
FOLLOWUP = MOCK / "followup"

ICONS = {"wait": "..", "nudge": ">>", "replan": "<>", "escalate": "!!", "close": "ok"}


def build_agent(live: bool):
    load_dotenv()
    snapshot = datetime.fromisoformat(os.getenv("MOCK_TODAY", "2026-11-15"))
    requests = load_requests(FOLLOWUP / "transfer_requests.csv")

    llm = planner = None
    if live:
        from services.agents.agent import run_agent

        llm = lambda system, task, registry: run_agent(system, task, registry)
        planner = bedrock_planner

    agent = FollowUpAgent(CsvData(MOCK), requests, snapshot, llm=llm, planner=planner)
    return agent, snapshot


def run(live: bool = False, days: int = 4):
    agent, snapshot = build_agent(live)
    replies = load_replies(FOLLOWUP / "replies.json")
    timeline = []

    for day in range(days):
        now = snapshot + timedelta(days=day, hours=18)
        timeline.append((now, agent.tick(now, replies)))

    return agent, timeline


def print_timeline(timeline):
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
            for flag in action.flags:
                print(f"       FLAG: {flag}")
            if action.message_draft:
                print(f"       draft to {action.message_to}: {action.message_draft}")
            if action.replan:
                for option in action.replan["verified_options"]:
                    print(f"       replan option ({action.replan['source']}): {option['description']}")
    print("\nNothing was sent or executed. Every nudge, replan and escalation waits for a human.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use Bedrock models.")
    parser.add_argument("--json", action="store_true", help="Print JSON.")
    parser.add_argument("--days", type=int, default=4)
    args = parser.parse_args()

    _, timeline = run(live=args.live, days=args.days)

    if args.json:
        print(json.dumps(
            [
                {"at": now.isoformat(), "actions": [a.model_dump(mode="json") for a in actions]}
                for now, actions in timeline
            ],
            indent=2,
        ))
    else:
        print_timeline(timeline)


if __name__ == "__main__":
    main()
