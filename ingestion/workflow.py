"""Run ingestion jobs as a workflow: tasks with dependencies, retries and a run record.

Like a Databricks job: each task waits for the tasks it depends on, a failed
task is retried, tasks downstream of a failure are skipped (not run on stale
input), and the whole run is recorded in ingestion_run as "workflow:<name>".
Each task also records its own ingestion_run rows, as it does when run alone.

The nightly workflow:

    shortages ──► dpd       DPD details for drugs in shortage only
             └──► signals   plan supply events from new shortage reports for our drugs

Usage:
  python -m ingestion.workflow nightly
  python -m ingestion.workflow nightly --dry-run            # show the plan, run nothing
  python -m ingestion.workflow nightly --only dpd           # re-run one task ("repair run")
  python -m ingestion.workflow nightly --batch-size 25 --max-batches 0 --shortage-max-pages 5
  python -m ingestion.workflow followup                     # check approved transfers (rules only)
  python -m ingestion.workflow day --date 2026-11-16       # the whole platform for one day (cron: daily)

Scheduling is left to the host, e.g. cron at 02:00:
  0 2 * * * cd /path/to/repo && .venv/bin/python -m ingestion.workflow nightly
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from dotenv import load_dotenv
from sqlalchemy.engine import Engine

from ingestion.lineage import finish_run, get_engine, start_run

Params = dict[str, Any]


@dataclass(frozen=True)
class Task:
    name: str
    run: Callable[[Engine | None, Params], dict]
    depends_on: tuple[str, ...] = ()
    retries: int = 1           # extra attempts after the first
    retry_delay: float = 30.0  # seconds, doubled after each failure


@dataclass
class TaskResult:
    name: str
    state: str                 # succeeded / failed / skipped
    attempts: int = 0
    seconds: float = 0.0
    stats: dict = field(default_factory=dict)
    error: str | None = None


def order_tasks(tasks: list[Task]) -> list[Task]:
    """Dependency order. Raises on unknown dependencies or cycles."""

    by_name = {task.name: task for task in tasks}
    if len(by_name) != len(tasks):
        raise ValueError("Task names must be unique.")
    for task in tasks:
        missing = set(task.depends_on) - set(by_name)
        if missing:
            raise ValueError(f"Task {task.name!r} depends on unknown task(s): {sorted(missing)}")

    ordered, done, visiting = [], set(), set()

    def visit(task: Task):
        if task.name in done:
            return
        if task.name in visiting:
            raise ValueError(f"Dependency cycle at task {task.name!r}")
        visiting.add(task.name)
        for dependency in task.depends_on:
            visit(by_name[dependency])
        visiting.discard(task.name)
        done.add(task.name)
        ordered.append(task)

    for task in tasks:
        visit(task)
    return ordered


def run_workflow(
    name: str,
    tasks: list[Task],
    engine: Engine | None,
    params: Params | None = None,
    only: set[str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    log: Callable[[str], None] = print,
) -> list[TaskResult]:
    """Run tasks in dependency order. only= runs just those tasks and assumes their inputs are fresh."""

    params = params or {}
    ordered = order_tasks(tasks)
    if only:
        unknown = only - {task.name for task in ordered}
        if unknown:
            raise ValueError(f"Unknown task(s): {sorted(unknown)}")
        ordered = [task for task in ordered if task.name in only]

    run_id = start_run(engine, f"workflow:{name}") if engine is not None else None
    results: dict[str, TaskResult] = {}

    for task in ordered:
        blocked = [dep for dep in task.depends_on if dep in results and results[dep].state != "succeeded"]
        if blocked:
            results[task.name] = TaskResult(task.name, "skipped", error=f"upstream not succeeded: {blocked}")
            log(f"[{name}] {task.name}: skipped ({', '.join(blocked)} did not succeed)")
            continue

        result = TaskResult(task.name, "failed")
        delay = task.retry_delay
        started = time.monotonic()
        for attempt in range(1, task.retries + 2):
            result.attempts = attempt
            log(f"[{name}] {task.name}: attempt {attempt}")
            try:
                result.stats = task.run(engine, params) or {}
                result.state, result.error = "succeeded", None
                break
            except Exception as error:  # a task failure must not crash the runner
                result.error = f"{type(error).__name__}: {error}"
                log(f"[{name}] {task.name}: attempt {attempt} failed: {result.error}")
                if attempt <= task.retries:
                    sleep(delay)
                    delay *= 2
        result.seconds = round(time.monotonic() - started, 2)
        results[task.name] = result
        log(f"[{name}] {task.name}: {result.state} in {result.seconds}s {result.stats or ''}")

    final = list(results.values())
    if run_id is not None:
        ok = sum(r.state == "succeeded" for r in final)
        problems = "; ".join(f"{r.name}: {r.state} ({r.error})" for r in final if r.state != "succeeded")
        finish_run(engine, run_id, "succeeded" if ok == len(final) else "failed",
                   rows_in=len(final), rows_ok=ok, error=problems or None)
    return final


# ---------------------------------------------------------
# The nightly workflow
# ---------------------------------------------------------

def nightly_tasks(shortage_client=None, dpd_client=None, llm=None, data=None) -> list[Task]:
    """Clients, model and data source are created per run unless passed in (tests pass fakes)."""

    def shortages(engine: Engine, params: Params) -> dict:
        from ingestion.shortage_client import ShortageClient
        from ingestion.shortage_ingest import run_ingest

        filters = {"type": "shortage", "filter_status": "active_confirmed", "orderby": "updated_date", "order": "desc"}
        client = shortage_client or ShortageClient()
        try:
            return run_ingest(engine=engine, client=client, max_pages=params.get("shortage_max_pages"), filters=filters)
        finally:
            if shortage_client is None:
                client.close()

    def dpd(engine: Engine, params: Params) -> dict:
        from ingestion.dpd_client import DpdClient
        from ingestion.dpd_ingest import run_from_shortages

        client = dpd_client or DpdClient()
        try:
            return run_from_shortages(
                engine,
                client,
                batch_size=params.get("batch_size", 50),
                max_batches=params.get("max_batches"),
                stale_days=params.get("stale_days", 7),
            )
        finally:
            if dpd_client is None:
                client.close()

    def signals(engine: Engine, params: Params) -> dict:
        import os

        from services.agents.db_data import PostgresData
        from services.agents.shortage_signals import pending_reports, run_shortage_signals

        if llm is None and not os.getenv("BEDROCK_MODEL_ID"):
            # Planning needs the model; leave reports pending for a run that has one.
            return {"skipped": "no model configured (BEDROCK_MODEL_ID)", "pending": len(pending_reports(engine))}

        source = data or (PostgresData.from_env() if os.getenv("DATABASE_URL_READONLY") else PostgresData(engine))
        result = run_shortage_signals(engine, source, llm=llm, limit=params.get("signal_limit", 20))
        result.pop("saved", None)
        return result

    return [
        Task("shortages", shortages, retries=2, retry_delay=60),
        Task("dpd", dpd, depends_on=("shortages",), retries=1, retry_delay=60),
        # No automatic retry: a failed report stays pending and is retried next run.
        Task("signals", signals, depends_on=("shortages",), retries=0),
    ]


def followup_tasks(data=None, now=None) -> list[Task]:
    """Check approved transfers (services/agents/followup_db.py). Rules only, no model:
    run `python -m services.agents.followup_db --live` to use Bedrock."""

    def followup(engine: Engine, params: Params) -> dict:
        import os
        from collections import Counter
        from datetime import datetime, timezone

        from services.agents.db_data import PostgresData
        from services.agents.followup_db import run_followup

        source = data or (PostgresData.from_env() if os.getenv("DATABASE_URL_READONLY") else PostgresData(engine))
        actions = run_followup(engine, source, now or datetime.now(timezone.utc))
        return {"checked": len(actions), **Counter(action.decision for action in actions)}

    return [Task("followup", followup, retries=1, retry_delay=60)]


def day_tasks(day=None, llm=None) -> list[Task]:
    """The whole platform for one day (services/agents/daily_run.py). Default day: today (UTC)."""

    def run(engine: Engine, params: Params) -> dict:
        import os
        from datetime import datetime, timezone

        from sqlalchemy import create_engine

        from services.agents.daily_run import run_day

        read = create_engine(os.environ["DATABASE_URL_READONLY"]) if os.getenv("DATABASE_URL_READONLY") else engine
        when = day or params.get("day") or datetime.now(timezone.utc).date()
        summary = run_day(engine, read, when, llm=llm, planner=params.get("planner", "auto"))
        return {"day": summary["day"], "emails": len(summary["emails"]),
                "alerts": len(summary["stock_watch"]["alerts"]), "followup": len(summary["followup"])}

    return [Task("day", run, retries=0)]


WORKFLOWS: dict[str, Callable[[], list[Task]]] = {"nightly": nightly_tasks, "followup": followup_tasks, "day": day_tasks}


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run an ingestion workflow")
    parser.add_argument("workflow", choices=sorted(WORKFLOWS))
    parser.add_argument("--only", action="append", help="Run only this task (repeatable)")
    parser.add_argument("--dry-run", action="store_true", help="Print the task order and exit")
    parser.add_argument("--batch-size", type=int, default=50, help="DPD drugs per batch")
    parser.add_argument("--max-batches", type=int, default=0, help="DPD batches per run (0 = until done)")
    parser.add_argument("--stale-days", type=int, default=7, help="Re-fetch DPD data older than this")
    parser.add_argument("--shortage-max-pages", type=int, default=None, help="Stop the shortage pull after N pages")
    parser.add_argument("--signal-limit", type=int, default=20, help="Shortage reports to plan per run")
    parser.add_argument("--date", type=lambda v: __import__("datetime").date.fromisoformat(v),
                        help="For the 'day' workflow: the day to run (default: today)")
    parser.add_argument("--planner", choices=["auto", "model", "rules"], default="auto", help="For the 'day' workflow")
    args = parser.parse_args(argv)

    tasks = WORKFLOWS[args.workflow]()
    ordered = order_tasks(tasks)
    if args.dry_run:
        for task in ordered:
            if not args.only or task.name in args.only:
                after = f" (after {', '.join(task.depends_on)})" if task.depends_on else ""
                print(f"{task.name}{after}, retries={task.retries}")
        return 0

    params = {
        "batch_size": args.batch_size,
        "max_batches": args.max_batches or None,
        "stale_days": args.stale_days,
        "shortage_max_pages": args.shortage_max_pages,
        "signal_limit": args.signal_limit,
        "day": args.date,
        "planner": args.planner,
    }
    try:
        results = run_workflow(args.workflow, tasks, get_engine(), params, set(args.only) if args.only else None)
    except (RuntimeError, ValueError) as error:
        print(f"Workflow failed to start: {error}", file=sys.stderr)
        return 1

    print("\n".join(f"{r.name:10} {r.state:9} attempts={r.attempts} {r.error or ''}" for r in results))
    return 0 if all(r.state == "succeeded" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
