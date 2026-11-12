import { PlanCard, usePlanFeasible } from "../components/PlanCard";
import { Badge, Card, Empty, SourceChip } from "../components/ui";
import { useStore } from "../lib/store";
import type { Plan } from "../lib/types";

export function Plans() {
  const { state } = useStore();
  if (state.plans.length === 0) {
    return (
      <div className="view">
        <Empty>No response plans yet. Plans appear once a supply event puts a site at risk.</Empty>
      </div>
    );
  }

  const latestRun = state.plans[0].solverRun;
  const current = state.plans.filter((p) => p.solverRun === latestRun).sort((a, b) => a.rank - b.rank);
  const earlier = state.plans.filter((p) => p.solverRun !== latestRun);
  const event = state.events.find((e) => e.id === current[0].eventId)!;
  const replanned = latestRun === "SOLVER-32";

  return (
    <div className="view">
      <Card
        title={event.title}
        actions={
          <span className="row gap">
            <SourceChip id={event.id} />
            <Badge tone={event.status === "open" ? "bad" : "good"}>{event.status}</Badge>
          </span>
        }
      >
        <p>{event.summary}</p>
        {replanned && (
          <div className="callout warn">
            <strong>No perfect plan exists.</strong> After Riverside's usage spike, no transfer-only plan keeps every site above its safety floor. The options below are ranked fallbacks, each
            with its tradeoffs shown.
          </div>
        )}
      </Card>

      <h2 className="section-title">
        Ranked plans <span className="muted small">solver run {latestRun}</span>
      </h2>
      <div className="plans">
        {current.map((p) => (
          <PlanCard key={p.id} plan={p} actions={<RouteAction plan={p} />} />
        ))}
      </div>

      {earlier.length > 0 && (
        <>
          <h2 className="section-title">Earlier plans</h2>
          <div className="plans">
            {earlier.map((p) => (
              <PlanCard key={p.id} plan={p} compact={!p.decisionNote} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}

function RouteAction({ plan }: { plan: Plan }) {
  const { state, dispatch } = useStore();
  const feasible = usePlanFeasible(plan);
  if (plan.status !== "proposed") return null;
  const canRoute = state.role === "supply" && feasible;
  return (
    <>
      <span className="muted small">{state.role !== "supply" ? "Only the supply manager can route plans." : !feasible ? "Infeasible plans cannot be routed." : ""}</span>
      <button className="btn" disabled={!canRoute} onClick={() => dispatch({ type: "route", planId: plan.id })}>
        Send this plan for approval instead
      </button>
    </>
  );
}
