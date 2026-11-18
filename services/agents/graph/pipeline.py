"""The agentic pipeline, as LangGraph graphs. Everything agents (and the human decision) do lives here;
the rest of the application only starts and resumes it.

Two graphs:

  Intake (one run per supplier notice, stateless)
      START → screen (code) ─┬─ suspicious ──────────────────────────→ END  (held for a person)
                             └→ assess (assessment agent) → END  (a typed SupplyEvent, or held for review)

  Alert lifecycle (one persistent thread per drug: "alert:<DIN>")
      START → gap (code) ─┬─ nothing to plan ───────────────────────────────→ save → END
                          └→ plan (planning agent or rules) → check (code) ─┬─ retry once with the reasons → plan
                                                                            └→ save (code) ─┬─ no checked option → END
                                                                                             └→ await_decision (human: graph pauses)
      await_decision ─┬─ approved → act ─┬─ transfer → follow_up
                      │                  ├─ order    → order (order agent drafts the supplier request) → order_follow_up
                      │                  └─ other    → END
                      ├─ rejected → END
                      ├─ refused (stock changed) → gap        (replan on today's stock)
                      └─ new signal → gap                     (an email or the stock watch refines the plan)
      follow_up (follow-up agent: graph pauses until each evening) ─┬─ all closed → END
                                                                    ├─ replan → gap (without the failed donor)
                                                                    └─ otherwise → follow_up (next evening)
      order_follow_up (order agent: pauses until each evening) ─┬─ all received → END
                                                                ├─ replan → gap (without that supplier's order)
                                                                └─ otherwise → order_follow_up (next evening)

State is checkpointed in Postgres (migration 7c2e91d4b5a0), so an alert can wait days for approval or
follow-up and resume exactly where it stopped. Engines, the model and the planner choice are passed at
run time (Deps) and never stored. Every node calls the same functions as the classic pipeline, so the
code checks, read-only tools, typed handoffs and approval rules are unchanged.

  python -m services.agents.graph.pipeline --mermaid      # both graphs as Mermaid diagrams
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, TypedDict

from langchain_core.runnables.graph import NodeStyles
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from sqlalchemy import text
from sqlalchemy.engine import Engine

from services.agents.agent import (
    MAX_EMAIL_LENGTH,
    MIN_CONFIDENCE,
    MODEL_ERRORS,
    _result,
    assess_email,
    check_plan,
    gap_for_event,
    plan_result,
    propose_plan,
    retry_feedback,
)
from services.agents.db_data import PostgresData
from services.agents.followup_db import run_followup
from services.agents.followup_rules import exclude_failed_donor
from services.agents.order_agent import draft_requests, resolve_model, run_order_followup
from services.agents.results_store import save_pipeline_result
from services.agents.rules_planner import rules_planner
from services.agents.schemas import PlanOption, PlanSet, SupplyEvent
from services.agents.security import inspect_supplier_email
from services.agents.tools import build_tool_registries


@dataclass
class Deps:
    """Run-time dependencies, given to every node. Not part of the checkpointed state."""

    write_engine: Engine
    read_engine: Engine
    llm: Any = None
    use_rules: bool = False

    def data(self, day: str) -> PostgresData:
        return PostgresData(self.read_engine, as_of=date.fromisoformat(day))


def thread(din: str) -> dict:
    return {"configurable": {"thread_id": f"alert:{din}"}}


# ---------------------------------------------------------
# Intake: one supplier notice → a typed SupplyEvent (or held)
# ---------------------------------------------------------

class IntakeState(TypedDict, total=False):
    email: str
    day: str
    outcome: str          # "plan" | "held"
    event: dict           # SupplyEvent, when outcome == "plan"
    result: dict          # the final result, when held


def _screen(state: IntakeState) -> IntakeState:
    email = state["email"]
    if len(email) > MAX_EMAIL_LENGTH:
        return {"outcome": "held", "result": _result("needs_review", ["Email exceeds the permitted length."])}
    screen = inspect_supplier_email(email)
    if screen.suspicious:
        return {"outcome": "held", "result": _result("needs_review", list(screen.reasons))}
    return {"outcome": "assess"}


def _assess(state: IntakeState, runtime: Runtime[Deps]) -> IntakeState:
    deps = runtime.context
    try:
        registry, _ = build_tool_registries(deps.data(state["day"]))
        event = assess_email(email=state["email"], assessment_registry=registry, llm=deps.llm)
    except MODEL_ERRORS as error:
        return {"outcome": "held", "result": _result("error", f"{type(error).__name__}: {error}")}
    assessment = event.model_dump()
    if event.next_action != "plan":
        return {"outcome": "held", "result": _result(event.next_action, event.reason, assessment)}
    if event.confidence < MIN_CONFIDENCE:
        return {"outcome": "held", "result": _result("needs_review", f"Confidence {event.confidence} is below {MIN_CONFIDENCE}.", assessment)}
    return {"outcome": "plan", "event": assessment}


def build_intake_graph():
    graph = StateGraph(IntakeState, context_schema=Deps)
    graph.add_node("screen", _screen)
    graph.add_node("assess", _assess)
    graph.add_edge(START, "screen")
    graph.add_conditional_edges("screen", lambda s: "assess" if s["outcome"] == "assess" else END, ["assess", END])
    graph.add_edge("assess", END)
    return graph.compile()


# ---------------------------------------------------------
# Alert lifecycle: one drug's alert, from signal to resolution
# ---------------------------------------------------------

class AlertState(TypedDict, total=False):
    din: str
    day: str
    trigger: dict          # {"event": SupplyEvent dict, "save": save kwargs, "restore": bool, "join": bool,
                           #  "agent_planner": bool (supplier notices are always planned by the agent)}
    gap: dict | None
    plan: dict | None
    attempt: int
    feedback: str | None
    retried: bool
    retry: bool
    exclude_site: str | None
    exclude_supplier: str | None  # a replan after an order failed: don't offer that supplier's order again
    order_requests: list[str]     # orders the order agent drafted a supplier request for
    last_checks: list[dict]       # the latest evening's follow-up checks (kept when a replan resets followup)
    replan: bool
    result: dict | None
    outcome: str
    alert_id: str | None
    plan_id: str | None
    decision: dict | None
    followup: list[dict]


FRESH = {"gap": None, "plan": None, "attempt": 0, "feedback": None, "retried": False, "retry": False,
         "replan": False, "result": None, "decision": None, "followup": []}


def _event(state: AlertState) -> SupplyEvent:
    return SupplyEvent(**state["trigger"]["event"])


def _gap(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    event = _event(state)
    gap = gap_for_event(event, event.model_dump(), runtime.context.data(state["day"]), date.fromisoformat(state["day"]),
                        restore=state["trigger"].get("restore", False))
    if not gap["ok"]:
        return {**FRESH, "result": gap["result"]}
    gap.pop("ok")
    return {**FRESH, "gap": gap}


def _plan(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    deps = runtime.context
    event, data = _event(state), deps.data(state["day"])
    try:
        _, planning_registry = build_tool_registries(data)
        use_rules = deps.use_rules and not state["trigger"].get("agent_planner", False)
        plan = propose_plan(event, state["gap"], data, deps.llm, planning_registry,
                            rules_planner if use_rules else None, state.get("feedback"))
    except MODEL_ERRORS as error:
        return {"plan": None, "result": _result("error", f"{type(error).__name__}: {error}", event.model_dump())}
    if state.get("exclude_supplier"):
        plan = PlanSet(options=[o for o in plan.options
                                if not any(line.supplier_id == state["exclude_supplier"] for line in o.order_lines)]
                       or [PlanOption(type="escalate", description="Escalate to a manager", moves=[],
                                      reasoning=f"The order from {state['exclude_supplier']} failed and nothing else covers the gap.")],
                       no_perfect_plan=plan.no_perfect_plan)
    if state.get("exclude_site"):
        # A replan after a donor failed: never offer that donor again.
        kept, _ = exclude_failed_donor(plan.options, state["exclude_site"])
        plan = PlanSet(options=kept or [PlanOption(
            type="escalate", description="Only the failed donor could cover this: escalate to a manager", moves=[],
            reasoning=f"{state['exclude_site']} did not ship and no other hospital can spare enough.")],
            no_perfect_plan=plan.no_perfect_plan or not kept)
    return {"plan": plan.model_dump(), "attempt": state.get("attempt", 0) + 1}


def _check(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    event, data = _event(state), runtime.context.data(state["day"])
    plan = PlanSet(**state["plan"])
    verified, rejected = check_plan(event, state["gap"], plan, data)
    feedback = retry_feedback(plan, verified, rejected, state["attempt"])
    if feedback:
        return {"retry": True, "feedback": feedback, "retried": True}
    return {"retry": False, "result": plan_result(event, event.model_dump(), state["gap"], plan, verified, rejected,
                                                  state.get("retried", False))}


def _save(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    deps = runtime.context
    from services.agents.daily_run import living_event  # the "one living alert per drug" rule lives there

    trigger = state["trigger"]
    existing = living_event(deps.write_engine, state["din"]) if trigger.get("join", True) else None
    saved = save_pipeline_result(deps.write_engine, state["result"], event_id=existing, **trigger["save"])
    return {"alert_id": saved["event_id"], "plan_id": saved["plan_id"], "outcome": state["result"]["status"]}


def _await_decision(state: AlertState) -> AlertState:
    """The human step. The graph stops here until the API resumes it with the decision."""

    value = interrupt({"waiting_for": "decision", "alert_id": state.get("alert_id"), "plan_id": state.get("plan_id")})
    update: AlertState = {"decision": value}
    if value.get("type") == "signal":
        update |= {"trigger": value["trigger"], "day": value["day"], "exclude_site": None, "exclude_supplier": None}
    elif value.get("type") == "refused":
        update |= {"day": value["day"]}
    return update


def _act(state: AlertState) -> AlertState:
    return {"outcome": f"approved_{state['decision'].get('option_type', 'option')}"}


def _model(deps: Deps):
    """The model for agent steps, or None when the run is on rules (the agents then use their rule fallbacks)."""

    return resolve_model(deps.llm, deps.use_rules)


def _order(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    """The order agent drafts the request to the supplier for the approved order (a person sends it)."""

    deps = runtime.context
    day = state["decision"].get("day") or state["day"]
    drafts = draft_requests(deps.write_engine, deps.data(day), datetime.combine(date.fromisoformat(day), time(12)),
                            _model(deps), plan_id=state["plan_id"])
    return {"outcome": "order_approved", "followup": [], "replan": False,
            "order_requests": [d["order_id"] for d in drafts]}


def _order_follow_up(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    """The order agent, once per evening. The graph stops here between checks."""

    value = interrupt({"waiting_for": "order_follow_up", "alert_id": state.get("alert_id"), "plan_id": state.get("plan_id")})
    if value.get("type") == "signal":
        return {"decision": {"type": "signal"}, "trigger": value["trigger"], "day": value["day"],
                "exclude_site": None, "exclude_supplier": None}

    deps = runtime.context
    actions = run_order_followup(deps.write_engine, deps.data(value["day"]), datetime.fromisoformat(value["now"]),
                                 _model(deps), plan_id=state["plan_id"])
    followup = [{"transfer": a.request_id, "decision": a.decision, "kind": "order"} for a in actions]
    update: AlertState = {"followup": followup, "last_checks": followup, "day": value["day"], "decision": {"type": "tick"},
                          "replan": False}
    replanned = [a.request_id for a in actions if a.decision == "replan"]
    if replanned:
        with deps.read_engine.connect() as conn:
            supplier = conn.execute(text("SELECT supplier_id FROM purchase_order WHERE order_id = :o"),
                                    {"o": replanned[0]}).scalar_one()
        update |= {"replan": True, "exclude_supplier": supplier, "trigger": {**state["trigger"], "restore": True}}
    return update


def _follow_up(state: AlertState, runtime: Runtime[Deps]) -> AlertState:
    """The follow-up agent, once per evening. The graph stops here between checks."""

    value = interrupt({"waiting_for": "follow_up", "alert_id": state.get("alert_id"), "plan_id": state.get("plan_id")})
    if value.get("type") == "signal":
        return {"decision": {"type": "signal"}, "trigger": value["trigger"], "day": value["day"], "exclude_site": None,
                "exclude_supplier": None}

    deps = runtime.context
    actions = run_followup(deps.write_engine, deps.data(value["day"]), datetime.fromisoformat(value["now"]),
                           plan_id=state["plan_id"])
    followup = [{"transfer": a.request_id, "decision": a.decision} for a in actions]
    update: AlertState = {"followup": followup, "last_checks": followup, "day": value["day"], "decision": {"type": "tick"},
                          "replan": False}
    replanned = [a.request_id for a in actions if a.decision == "replan"]
    if replanned:
        with deps.read_engine.connect() as conn:
            donor = conn.execute(text("SELECT from_site FROM plan_action WHERE action_id = :a"), {"a": replanned[0]}).scalar_one()
        update |= {"replan": True, "exclude_site": donor, "trigger": {**state["trigger"], "restore": True}}
    return update


def _after_gap(state: AlertState) -> str:
    return "plan" if state.get("gap") else "save"


def _after_plan(state: AlertState) -> str:
    return "check" if state.get("plan") else "save"


def _after_check(state: AlertState) -> str:
    return "plan" if state.get("retry") else "save"


def _after_save(state: AlertState) -> str:
    return "await_decision" if state.get("outcome") == "plan_generated" else END


def _after_decision(state: AlertState) -> str:
    kind = (state.get("decision") or {}).get("type")
    if kind == "approved":
        return "act"
    if kind in ("refused", "signal"):
        return "gap"
    return END  # rejected


def _after_act(state: AlertState) -> str:
    option_type = state["decision"].get("option_type")
    return {"transfer": "follow_up", "order": "order"}.get(option_type, END)


def _after_follow_up(state: AlertState) -> str:
    if state["decision"].get("type") == "signal" or state.get("replan"):
        return "gap"
    decisions = [f["decision"] for f in state.get("followup", [])]
    if decisions and all(d == "close" for d in decisions):
        return END
    return "follow_up"


def _after_order_follow_up(state: AlertState) -> str:
    if state["decision"].get("type") == "signal" or state.get("replan"):
        return "gap"
    decisions = [f["decision"] for f in state.get("followup", [])]
    if decisions and all(d == "close" for d in decisions):
        return END
    return "order_follow_up"


def build_alert_graph(checkpointer=None):
    graph = StateGraph(AlertState, context_schema=Deps)
    for name, node in [("gap", _gap), ("plan", _plan), ("check", _check), ("save", _save),
                       ("await_decision", _await_decision), ("act", _act), ("order", _order), ("follow_up", _follow_up),
                       ("order_follow_up", _order_follow_up)]:
        graph.add_node(name, node)
    graph.add_edge(START, "gap")
    graph.add_conditional_edges("gap", _after_gap, ["plan", "save"])
    graph.add_conditional_edges("plan", _after_plan, ["check", "save"])
    graph.add_conditional_edges("check", _after_check, ["plan", "save"])
    graph.add_conditional_edges("save", _after_save, ["await_decision", END])
    graph.add_conditional_edges("await_decision", _after_decision, ["act", "gap", END])
    graph.add_conditional_edges("act", _after_act, ["follow_up", "order", END])
    graph.add_edge("order", "order_follow_up")
    graph.add_conditional_edges("order_follow_up", _after_order_follow_up, ["gap", "order_follow_up", END])
    graph.add_conditional_edges("follow_up", _after_follow_up, ["gap", "follow_up", END])
    return graph.compile(checkpointer=checkpointer)


# ---------------------------------------------------------
# The interface the rest of the application uses
# ---------------------------------------------------------

class AgentPipeline:
    """Start, resume and inspect the agentic pipeline. The only way the app talks to it."""

    def __init__(self, write_engine: Engine, read_engine: Engine, llm=None, use_rules: bool = False):
        self.deps = Deps(write_engine, read_engine, llm, use_rules)

    @contextmanager
    def _graph(self):
        url = self.deps.write_engine.url.set(drivername="postgresql").render_as_string(hide_password=False)
        with PostgresSaver.from_conn_string(url) as saver:
            yield build_alert_graph(saver)

    def read_notice(self, email: str, day: date) -> dict:
        """Intake: screen and assess one supplier notice. Returns {"outcome": "plan", "event"} or {"outcome": "held", "result"}."""

        return build_intake_graph().invoke({"email": email, "day": day.isoformat()}, context=self.deps)

    def paused(self, din: str) -> dict | None:
        """What the drug's alert is paused on ({"waiting_for": "decision" | "follow_up" | "order_follow_up", "plan_id",
        ...}), or None."""

        with self._graph() as graph:
            snapshot = graph.get_state(thread(din))
        for task in snapshot.tasks:
            for pending in task.interrupts:
                return pending.value
        return None

    def waiting_for(self, din: str) -> str | None:
        return (self.paused(din) or {}).get("waiting_for")

    def signal(self, din: str, trigger: dict, day: date) -> dict:
        """New evidence for a drug (supplier notice, stock watch, report, request). Starts the alert's
        workflow, or sends a paused one back to planning. Returns the alert's state afterwards."""

        with self._graph() as graph:
            config = thread(din)
            if graph.get_state(config).next:
                graph.invoke(Command(resume={"type": "signal", "trigger": trigger, "day": day.isoformat()}), config,
                             context=self.deps)
            else:
                graph.invoke({**FRESH, "din": din, "day": day.isoformat(), "trigger": trigger, "exclude_site": None, "exclude_supplier": None,
                              "alert_id": None, "plan_id": None, "outcome": ""}, config, context=self.deps)
            return graph.get_state(config).values

    def decide(self, din: str, plan_id: str, decision: dict) -> dict | None:
        """A person decided on plan_id (approved, rejected, or the approval was refused). Resumes the alert
        only if it is paused on that plan; a decision on an older plan changes nothing here."""

        paused = self.paused(din) or {}
        if paused.get("waiting_for") != "decision" or paused.get("plan_id") != plan_id:
            return None
        with self._graph() as graph:
            graph.invoke(Command(resume=decision), thread(din), context=self.deps)
            return graph.get_state(thread(din)).values

    def tick(self, din: str, day: date, now: datetime) -> list[dict] | None:
        """Evening follow-up for one alert. Returns its follow-up actions, or None when it isn't waiting."""

        if self.waiting_for(din) not in ("follow_up", "order_follow_up"):
            return None
        with self._graph() as graph:
            graph.invoke(Command(resume={"type": "tick", "day": day.isoformat(), "now": now.isoformat()}), thread(din),
                         context=self.deps)
            return graph.get_state(thread(din)).values.get("last_checks", [])


# ---------------------------------------------------------
# Diagrams
# ---------------------------------------------------------

STYLES = NodeStyles(
    default="fill:#e8f1fb,stroke:#0b5cad,stroke-width:1px,color:#1c2330,line-height:1.2",
    first="fill:#ffffff,stroke:#5b6575,color:#1c2330",
    last="fill:#ffffff,stroke:#5b6575,color:#1c2330",
)


def mermaid() -> str:
    return "\n\n".join(g.get_graph().draw_mermaid(node_colors=STYLES) for g in (build_intake_graph(), build_alert_graph()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mermaid", action="store_true", help="Print both graphs as Mermaid diagrams")
    args = parser.parse_args(argv)
    if args.mermaid:
        print(mermaid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
