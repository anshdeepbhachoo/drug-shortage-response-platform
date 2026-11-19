"""One simulated day as a LangGraph graph.

    START → ingest → inbox → detect ─┬─ (drugs to plan) → plan_alerts ─┐
                                     └─ (nothing new) ─────────────────┴→ reports → followup → record → END

Nodes and who does the work in each:
  ingest       code    load the day's hospital data
  inbox        agent   assessment agent reads supplier notices (after the security screen), then planning
  detect       code    stock watch: which drugs need a new or redone plan
  plan_alerts  agent   planning agent (or rules planner) per alert, then code checks
  reports      code    Health Canada shortage reports, planned the same way
  followup     agent   follow-up agent on approved transfers (hard rules first)
  record       code    count what waits for people, write the day's audit entry

Routing is decided in code (conditional edges), never by a model. The agentic work in inbox,
plan_alerts, reports and followup is done by the agentic pipeline (pipeline.py): each signal is
handed to its drug's alert graph, and each evening's follow-up resumes the alerts paused there.

  python -m services.agents.pipeline.graph.daily --mermaid      # print the graph as a Mermaid diagram
"""

from __future__ import annotations

import argparse
from typing import TypedDict

from langchain_core.runnables.graph import NodeStyles
from langgraph.graph import END, START, StateGraph

from services.agents.pipeline.daily_run import (
    DayContext,
    step_detect,
    step_followup,
    step_inbox,
    step_ingest,
    step_plan_alerts,
    step_record,
    step_reports,
    summary_of,
)


class DayState(TypedDict, total=False):
    """What flows between the nodes. Plain JSON-able data only (no engines or models)."""

    ingested: int
    emails: list[dict]
    to_plan: list[dict]
    unchanged: int
    stock_watch: dict
    shortage_reports: dict
    followup: list[dict]
    summary: dict


def build_daily_graph(ctx: DayContext | None = None):
    """The compiled graph for one day. ctx is captured by the nodes, so it never enters graph state.

    With ctx=None the graph is built for drawing only (its nodes must not be run).
    """

    def ingest(state: DayState) -> DayState:
        return step_ingest(ctx)

    def inbox(state: DayState) -> DayState:
        return step_inbox(ctx)

    def detect(state: DayState) -> DayState:
        return step_detect(ctx)

    def plan_alerts(state: DayState) -> DayState:
        return step_plan_alerts(ctx, state["to_plan"], state["unchanged"])

    def skip_planning(state: DayState) -> str:
        return "plan_alerts" if state["to_plan"] else "reports"

    def reports(state: DayState) -> DayState:
        # Nothing to plan: the stock-watch summary still records the drugs whose plans still hold.
        empty = {} if "stock_watch" in state else {"stock_watch": {"alerts": [], "unchanged": state["unchanged"], "waiting": 0}}
        return {**empty, **step_reports(ctx)}

    def followup(state: DayState) -> DayState:
        return step_followup(ctx)

    def record(state: DayState) -> DayState:
        return {"summary": step_record(ctx, summary_of(ctx, state))}

    graph = StateGraph(DayState)
    graph.add_node("ingest", ingest)
    graph.add_node("inbox", inbox)
    graph.add_node("detect", detect)
    graph.add_node("plan_alerts", plan_alerts)
    graph.add_node("reports", reports)
    graph.add_node("followup", followup)
    graph.add_node("record", record)

    graph.add_edge(START, "ingest")
    graph.add_edge("ingest", "inbox")
    graph.add_edge("inbox", "detect")
    graph.add_conditional_edges("detect", skip_planning, {"plan_alerts": "plan_alerts", "reports": "reports"})
    graph.add_edge("plan_alerts", "reports")
    graph.add_edge("reports", "followup")
    graph.add_edge("followup", "record")
    graph.add_edge("record", END)
    return graph.compile()


def run_daily_graph(ctx: DayContext) -> dict:
    """Run the day through the graph. Returns the same summary as the classic pipeline."""

    final = build_daily_graph(ctx).invoke({})
    return final["summary"]


def mermaid() -> str:
    """The graph as Mermaid, with explicit dark-on-light colours so it reads in light and dark viewers."""

    ink = "color:#1c2330"
    styles = NodeStyles(
        default=f"fill:#e8f1fb,stroke:#0b5cad,stroke-width:1px,{ink},line-height:1.2",
        first=f"fill:#ffffff,stroke:#5b6575,{ink}",
        last=f"fill:#ffffff,stroke:#5b6575,{ink}",
    )
    return build_daily_graph().get_graph().draw_mermaid(node_colors=styles)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mermaid", action="store_true", help="Print the graph as a Mermaid diagram")
    args = parser.parse_args(argv)
    if args.mermaid:
        print(mermaid())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
