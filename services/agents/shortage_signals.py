"""Turn Health Canada shortage reports into planned supply events.

A shortage report is already structured (drug, status, Tier 3, estimated end
date), so no assessment agent is needed: code builds the SupplyEvent, and the
same risk -> planning agent -> transfer checks used for supplier emails run on
it (agent.process_event). Results are saved like any other pipeline result.

Only reports for drugs the network tracks (tracked_drug) are used. Each report
is planned once: its supply_event records source_ref "shortage_report:<id>".
If planning fails (model down), nothing but an audit entry is written, so the
report is picked up again next run.

    python -m services.agents.shortage_signals            # needs Bedrock for planning
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from collections import Counter
from datetime import date, datetime

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.agents.agent import default_today, process_event
from services.agents.model_io import LLM
from services.agents.results_store import save_pipeline_result
from services.agents.schemas import SupplyEvent

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = ("active_confirmed",)
# When a report has no estimated end date (or it has passed), plan to cover this long.
DEFAULT_SHORTAGE_DAYS = 28
SOURCE_PREFIX = "shortage_report:"


def pending_reports(engine: Engine, statuses=ACTIVE_STATUSES, limit: int = 20) -> list[dict]:
    """Active reports for tracked drugs that have not been planned yet. Tier 3 first."""

    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT s.report_id, s.din, t.name AS drug_name, s.status, s.tier3, s.shortage_reason,
                   s.actual_start_date, s.estimated_end_date, s.updated_date
            FROM shortage_report s
            JOIN tracked_drug t ON t.din = s.din
            WHERE s.status = ANY(CAST(:statuses AS text[]))
              AND NOT EXISTS (
                  SELECT 1 FROM supply_event e
                  WHERE e.source_type = 'shortage_report'
                    AND e.source_ref = :prefix || s.report_id::text
              )
            ORDER BY COALESCE(s.tier3, FALSE) DESC, s.updated_date DESC NULLS LAST, s.report_id
            LIMIT :limit
        """), {"statuses": list(statuses), "prefix": SOURCE_PREFIX, "limit": limit}).all()
    return [dict(row._mapping) for row in rows]


def _as_date(value) -> date | None:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def event_from_report(report: dict, today: date) -> SupplyEvent:
    """Code, not a model, turns the report into a supply event."""

    end = _as_date(report.get("estimated_end_date"))
    if end and end > today:
        delay_days, horizon = (end - today).days, f"estimated end {end.isoformat()}"
    else:
        delay_days, horizon = DEFAULT_SHORTAGE_DAYS, (
            f"estimated end {end.isoformat()} has passed" if end else "no estimated end date"
        ) + f"; planning for {DEFAULT_SHORTAGE_DAYS} days"

    tier = "Tier 3 " if report.get("tier3") else ""
    reason = report.get("shortage_reason")
    return SupplyEvent(
        drug_din=report["din"],
        drug_name=report.get("drug_name"),
        # Rounded *down* so process_event's ceil(weeks * 7) gives back exactly delay_days
        # (rounding to nearest can land just above, e.g. 25 days -> 25.000003 -> 26).
        delay_weeks=math.floor(delay_days / 7 * 1_000_000) / 1_000_000,
        confidence=1.0,
        next_action="plan",
        reason=(
            f"Health Canada {tier}shortage report {report['report_id']} ({report.get('status')})"
            + (f": {reason}" if reason else "")
            + f". {horizon}."
        ),
    )


def run_shortage_signals(
    engine: Engine,
    data,
    llm: LLM | None = None,
    today: date | None = None,
    statuses=ACTIVE_STATUSES,
    limit: int = 20,
    planner=None,
) -> dict:
    """Plan every pending shortage report and save the results. Returns counts by outcome.

    planner: optional code planner (rules_planner) instead of the planning agent."""

    today = today or default_today()
    outcomes: Counter = Counter()
    saved = []

    for report in pending_reports(engine, statuses, limit):
        event = event_from_report(report, today)
        result = process_event(event, data=data, llm=llm, today=today, planner=planner)
        ids = save_pipeline_result(
            engine,
            result,
            source_ref=f"{SOURCE_PREFIX}{report['report_id']}",
            actor="shortage-signal",
            source_type="shortage_report",
            kind="shortage",
        )
        outcomes[result["status"]] += 1
        saved.append({"report_id": report["report_id"], "din": report["din"], "status": result["status"], **ids})
        logger.info("shortage report %s -> %s", report["report_id"], result["status"])

    return {"reports": sum(outcomes.values()), **dict(outcomes), "saved": saved}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Plan supply events from Health Canada shortage reports")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--as-of", type=date.fromisoformat, help="Plan against stock from this day")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from ingestion.lineage import get_engine
    from services.agents.db_data import PostgresData

    engine = get_engine()
    data = PostgresData.from_env(as_of=args.as_of)
    print(json.dumps(run_shortage_signals(engine, data, today=args.as_of, limit=args.limit), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
