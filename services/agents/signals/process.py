"""Signal -> plan, end to end: a supplier email (process_email) or an already structured supply event
(process_event) goes through the security screen, the assessment agent, the shortfall, the planning agent
and the code checks, and comes back as a result for a person to approve.

  python -m services.agents.signals.process data/synthetic/mock/emails/clear.txt
"""

from __future__ import annotations

import argparse
import json
import logging
import os
from datetime import date
from pathlib import Path

from services.agents.bedrock import MODEL_ERRORS
from services.agents.model_io import LLM
from services.agents.schemas import SupplyEvent
from services.agents.signals.assessment import MAX_EMAIL_LENGTH, MIN_CONFIDENCE, assess_email
from services.agents.signals.planning import _plan_for_event, _result
from services.agents.tools import build_tool_registries
from services.data.data_source import default_data
from services.rules.security import inspect_supplier_email

logger = logging.getLogger(__name__)


def default_today() -> date:
    """MOCK_TODAY pins "today" to the mock inventory date (e.g. 2026-11-15)."""

    value = os.getenv("MOCK_TODAY")
    return date.fromisoformat(value) if value else date.today()


# ---------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------

def _pipeline(email: str, data, llm: LLM | None, today: date):
    assessment_registry, planning_registry = build_tool_registries(data)

    event = assess_email(email=email, assessment_registry=assessment_registry, llm=llm)
    assessment = event.model_dump()

    if event.next_action != "plan":
        return _result(event.next_action, event.reason, assessment)

    if event.confidence < MIN_CONFIDENCE:
        return _result(
            "needs_review",
            f"Confidence {event.confidence} is below {MIN_CONFIDENCE}.",
            assessment,
        )

    return _plan_for_event(event, assessment, data, llm, today, planning_registry)


def process_email(email: str, data=None, llm: LLM | None = None, today: date | None = None):
    """
    Supplier email -> security screen -> assessment agent -> Python risk
    calculation -> planning agent -> Python transfer verification -> human.

    data:  anything with CsvData's methods (drug, stock, orders, ...). Defaults
           to default_data(): Postgres when AGENT_DATA_SOURCE=postgres, else data/synthetic/mock/.
    llm:   model call, defaults to Bedrock. Tests pass a fake.
    today: defaults to MOCK_TODAY or the system date.
    """

    if isinstance(email, str) and len(email) > MAX_EMAIL_LENGTH:
        return _result("needs_review", ["Email exceeds the permitted length."])

    screen = inspect_supplier_email(email)

    if screen.suspicious:
        return _result("needs_review", list(screen.reasons))

    try:
        return _pipeline(
            email,
            data if data is not None else default_data(),
            llm,
            today or default_today(),
        )
    except MODEL_ERRORS as error:
        logger.exception("Pipeline failed")
        return _result("error", f"{type(error).__name__}: {error}")


def process_event(event: SupplyEvent, data=None, llm: LLM | None = None, today: date | None = None, planner=None,
                  restore: bool = False):
    """
    A supply event that is already structured (e.g. from a Health Canada
    shortage report) -> Python risk calculation -> planning agent -> Python
    transfer verification -> human. No email, so no assessment agent.

    The event must already say next_action="plan"; anything else is returned
    for review without planning.

    restore=True: delay_weeks is the cover to restore, not a supplier delay
    (stock watch and person-raised alerts).
    """

    assessment = event.model_dump()
    if event.next_action != "plan":
        return _result(event.next_action, event.reason, assessment)

    data = data if data is not None else default_data()
    try:
        _, planning_registry = build_tool_registries(data)
        return _plan_for_event(event, assessment, data, llm, today or default_today(), planning_registry, planner,
                               restore=restore)
    except MODEL_ERRORS as error:
        logger.exception("Event planning failed")
        return _result("error", f"{type(error).__name__}: {error}", assessment)


# ---------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Mock drug-shortage response agent"
    )

    parser.add_argument(
        "email_file",
        help="Path to the supplier email text file",
    )

    parser.add_argument(
        "--db",
        action="store_true",
        help="Read stock, orders and drugs from Postgres (DATABASE_URL_READONLY) instead of data/synthetic/mock/ CSVs",
    )

    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        help="With --db: use the stock snapshot from this day (YYYY-MM-DD), e.g. a progression day",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="With --save: process the email even if its alert is still open",
    )

    parser.add_argument(
        "--save",
        action="store_true",
        help="Write the result to supply_event / plan / plan_action / audit_log (DATABASE_URL)",
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    email_path = Path(args.email_file)

    if not email_path.exists():
        raise FileNotFoundError(
            f"Email file not found: {email_path}"
        )

    email = email_path.read_text(encoding="utf-8")

    if args.save and not args.force:
        from ingestion.lineage import get_engine
        from services.store.results_store import open_event_for

        existing = open_event_for(get_engine(), str(email_path))
        if existing:
            print(f"{email_path} is already being handled as {existing}; not planning it twice. Use --force to override.")
            return

    data = None
    if args.db:
        from services.data.db_data import PostgresData

        data = PostgresData.from_env(as_of=args.as_of)

    result = process_email(email, data=data, today=args.as_of)

    if args.save:
        from ingestion.lineage import get_engine
        from services.store.results_store import save_pipeline_result

        result["saved"] = save_pipeline_result(
            get_engine(), result, source_ref=str(email_path)
        )

    print(
        json.dumps(
            result,
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
