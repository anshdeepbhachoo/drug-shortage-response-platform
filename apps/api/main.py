"""HTTP API for the web UI: read the platform's state, approve or reject plans,
run the signal agent on a supplier email, and run a follow-up check.

    .venv/bin/uvicorn apps.api.main:app --reload --port 8000

Reads use DATABASE_URL_READONLY (readonly_app) when set. Writes go only through
the same code the CLIs use (approvals.py, results_store.py, followup_db.py),
with DATABASE_URL, so every rule (roles, re-checks, audit) still applies.
"""

from __future__ import annotations

import os
from datetime import date, datetime, time, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError, ProgrammingError

from apps.api.state import EMAILS, build_state
from services.agents.approvals import ALL_ROLES, ApprovalError, approve_plan, reject_plan
from services.agents.db_data import PostgresData

# The UI's people and the approval roles they hold.
UI_ROLES = {"supply": "supply_manager", "pharmacist": "pharmacist", "procurement": "procurement"}


class Decision(BaseModel):
    by: str = Field(min_length=1)
    role: str
    note: str | None = None
    option: int | None = None
    as_of: date | None = None


class RunEmail(BaseModel):
    email: str = Field(description="File name in mock/emails, e.g. clear.txt")
    as_of: date | None = None


class RunDay(BaseModel):
    day: date
    planner: str = Field(default="auto", pattern="^(auto|model|rules)$")
    max_plans: int = Field(default=3, ge=0, le=20)


class PlanDrug(BaseModel):
    by: str = Field(min_length=1)
    role: str
    as_of: date
    site_id: str | None = None
    planner: str = Field(default="auto", pattern="^(auto|model|rules)$")


class RunFollowup(BaseModel):
    now: datetime
    as_of: date | None = None


def _role(role: str) -> str:
    role = UI_ROLES.get(role, role)
    if role not in ALL_ROLES:
        raise HTTPException(403, f"{role!r} cannot decide plans.")
    return role


def _split_card_id(card_id: str) -> tuple[str, int | None]:
    """UI plan cards are "<plan_id>-O<n>"; a bare plan ID means the default option."""

    plan_id, sep, option = card_id.rpartition("-O")
    return (plan_id, int(option)) if sep and option.isdigit() else (card_id, None)


def create_app(read_engine: Engine, write_engine: Engine, llm=None) -> FastAPI:
    app = FastAPI(title="Drug shortage response API")

    def guard(call):
        try:
            return call()
        except LookupError as error:
            raise HTTPException(404, str(error))
        except ProgrammingError as error:
            if "does not exist" in str(error.orig):
                raise HTTPException(503, "Database is behind on migrations. Run: alembic upgrade head")
            raise
        except OperationalError:
            raise HTTPException(503, "Database is not reachable. Is Postgres running?")

    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/state")
    def state(as_of: date | None = None):
        return guard(lambda: build_state(read_engine, as_of))

    @app.get("/api/emails")
    def email_files():
        return sorted(path.name for path in EMAILS.glob("*.txt"))

    @app.post("/api/plans/{card_id}/approve")
    def approve(card_id: str, body: Decision):
        plan_id, option = _split_card_id(card_id)
        data = PostgresData(read_engine, as_of=body.as_of)
        try:
            # With a stock day, the decision is timed on the simulated clock (noon that day).
            at = datetime.combine(body.as_of, time(12)) if body.as_of else None
            return guard(lambda: approve_plan(write_engine, plan_id, body.by, _role(body.role), data,
                                              body.option or option, body.note, at=at))
        except ApprovalError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/plans/{card_id}/reject")
    def reject(card_id: str, body: Decision):
        plan_id, _ = _split_card_id(card_id)
        try:
            at = datetime.combine(body.as_of, time(12)) if body.as_of else None
            return guard(lambda: reject_plan(write_engine, plan_id, body.by, _role(body.role), body.note or "", at=at))
        except ApprovalError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/agent/run")
    def run_agent_on_email(body: RunEmail):
        """Run the signal pipeline on a mock supplier email and save the result. Uses Bedrock unless a model is injected."""

        from services.agents.agent import process_email
        from services.agents.results_store import open_event_for, save_pipeline_result

        path = (EMAILS / Path(body.email).name)
        if not path.is_file():
            raise HTTPException(404, f"No email {body.email!r} in mock/emails")
        source_ref = str(path.relative_to(EMAILS.parents[1]))
        existing = guard(lambda: open_event_for(write_engine, source_ref))
        if existing:
            raise HTTPException(409, f"{body.email} is already being handled as alert {existing}. "
                                     "Approve or reject its plan first; the same notice isn't planned twice.")

        def run():
            result = process_email(path.read_text(encoding="utf-8"), data=PostgresData(read_engine, as_of=body.as_of),
                                   llm=llm, today=body.as_of)
            result["saved"] = save_pipeline_result(write_engine, result, source_ref=source_ref)
            return result
        return guard(run)

    @app.post("/api/day/run")
    def run_simulated_day(body: RunDay):
        """Run one simulated day: ingest, inbox, stock watch, shortage reports, follow-up."""

        from services.agents.daily_run import run_day

        return guard(lambda: run_day(write_engine, read_engine, body.day, llm=llm,
                                     planner=body.planner, max_plans=body.max_plans))

    @app.post("/api/drugs/{din}/plan")
    def plan_drug(din: str, body: PlanDrug):
        """The supply manager asks for a plan for one drug now (Stock page)."""

        from services.agents.daily_run import plan_for_drug

        if UI_ROLES.get(body.role, body.role) != "supply_manager":
            raise HTTPException(403, "Only the supply manager can raise a plan from the Stock page.")
        return guard(lambda: plan_for_drug(write_engine, read_engine, din, body.as_of, body.by,
                                           body.site_id, llm=llm, planner=body.planner))

    # ---- Supplier orders (services/agents/order_*.py) -------------------------

    @app.get("/api/orders")
    def orders(as_of: date | None = None):
        """Every order the order agent manages, its timeline, and days of stock left."""

        from services.agents.order_view import orders_page

        return guard(lambda: orders_page(read_engine, PostgresData(read_engine, as_of=as_of)))

    @app.post("/api/orders/{order_id}/{decision}")
    def decide_order(order_id: str, decision: str, body: Decision):
        """Approve or reject a drafted order. Goes through OrderAgent: role, spend limit, re-check, audit."""

        from services.agents.order_agent import OrderError, UnknownOrder
        from services.agents.order_db import approve_order, reject_order

        if decision not in ("approve", "reject"):
            raise HTTPException(404, "Use /approve or /reject.")
        role = UI_ROLES.get(body.role, body.role)
        if role not in ("procurement", "supply_manager"):
            raise HTTPException(403, "Only procurement or the supply manager can decide supplier orders.")
        note = (body.note or "").strip() or None
        if decision == "reject" and not note:
            raise HTTPException(422, "A note is needed to reject an order.")
        data = PostgresData(read_engine, as_of=body.as_of)
        # With a stock day, the decision is timed on the simulated clock (noon that day).
        at = datetime.combine(body.as_of, time(12)) if body.as_of else datetime.now(timezone.utc)

        def run():
            if decision == "approve":
                order = approve_order(write_engine, order_id, body.by, role, data, at, note)
            else:
                order = reject_order(write_engine, order_id, body.by, role, note, data, at)
            return {"order_id": order.order_id, "status": order.status}
        try:
            return guard(run)
        except UnknownOrder:
            raise HTTPException(404, f"No order {order_id}.")
        except OrderError as error:
            raise HTTPException(409, str(error))

    @app.post("/api/followup/run")
    def run_followup_check(body: RunFollowup):
        from services.agents.followup_db import run_followup

        def run():
            actions = run_followup(write_engine, PostgresData(read_engine, as_of=body.as_of), body.now)
            return [action.model_dump(mode="json") for action in actions]
        return guard(run)

    return app


def _engine(*names: str) -> Engine:
    for name in names:
        if os.environ.get(name):
            return create_engine(os.environ[name], pool_pre_ping=True)
    raise RuntimeError(f"Set one of {names} in .env")


def _app_from_env() -> FastAPI:
    load_dotenv()
    return create_app(_engine("DATABASE_URL_READONLY", "DATABASE_URL"), _engine("DATABASE_URL"))


app = _app_from_env() if os.environ.get("API_NO_AUTOSTART") != "1" else None
