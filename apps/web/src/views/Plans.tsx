import { PLAN_STATUS, PlanCard, usePlanFeasible } from "../components/PlanCard";
import { Card, ChartGuide, Empty, PageHead, Status } from "../components/ui";
import { useStore } from "../lib/store";
import type { Plan } from "../lib/types";

const costOf = (p: Plan) => Number((p.cost.match(/\$([\d,]+)/)?.[1] ?? "0").replace(/,/g, ""));

export function Plans() {
  const { state } = useStore();
  if (state.plans.length === 0) {
    return (
      <>
        <PageHead label="Plans" title="What are our options?" lead="Plans appear here as soon as an alert puts a hospital at risk." />
        <Empty title="No plans yet">Nothing is at risk right now. Move the story on to see a supplier delay.</Empty>
      </>
    );
  }

  const latestRun = state.plans[0].solverRun;
  const current = state.plans.filter((p) => p.solverRun === latestRun).sort((a, b) => a.rank - b.rank);
  const earlier = state.plans.filter((p) => p.solverRun !== latestRun);
  const event = state.events.find((e) => e.id === current[0].eventId)!;
  const replanned = latestRun === "SOLVER-32";

  return (
    <>
      <PageHead label="Plans" title="What are our options?" lead={`${event.summary} Here are the ways to cover the gap, best first. Every option is checked in code before anyone sees it.`} />

      {replanned && (
        <div className="callout warn">
          <span className="ico" aria-hidden>
            !
          </span>
          <div>
            <strong>No perfect option is left.</strong>
            <span className="ink-2">After Riverside's usage went up, no hospital can spare enough on its own without dropping under 14 days. These are the best compromises, with their trade-offs shown.</span>
          </div>
        </div>
      )}

      <Compare plans={current} />

      <section>
        <div className="section-head">
          <h2>Options in detail</h2>
          <div className="legend small">
            <span><i style={{ background: "var(--good)" }} />Days of stock after the plan</span>
            <span><i className="line" style={{ background: "none", borderLeft: "2px dashed var(--crit)", width: 2, height: 12, borderRadius: 0 }} />14-day safety floor</span>
            <span><i style={{ background: "var(--ink)", width: 3 }} />Days needed until the late order arrives</span>
          </div>
        </div>
        <div className="stack" style={{ gap: 16 }}>
          {current.map((p, i) => (
            <PlanCard key={p.id} plan={p} recommended={i === 0} actions={<RouteAction plan={p} />} />
          ))}
        </div>
      </section>

      {earlier.length > 0 && (
        <details className="card">
          <summary className="card-head" style={{ cursor: "pointer", paddingBottom: 16, listStyle: "none" }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>
                History
              </div>
              <h3>Earlier plans ({earlier.length})</h3>
            </div>
            <span className="meta">show</span>
          </summary>
          <div className="card-body stack" style={{ gap: 12 }}>
            {earlier.map((p) => (
              <PlanCard key={p.id} plan={p} compact={!p.decisionNote} />
            ))}
          </div>
        </details>
      )}
    </>
  );
}

/** Side-by-side comparison: cost as bars, plus timing and the safety result. */
function Compare({ plans }: { plans: Plan[] }) {
  const max = Math.max(...plans.map(costOf), 1);
  return (
    <Card label="At a glance" title="Compare the options">
      <ChartGuide
        open={false}
        items={[
          { mark: "cost", text: <>A <b>longer green bar</b> means the option <b>costs more</b>. Bars are scaled to the most expensive option.</> },
          { mark: "ok", text: <>The last column says whether the option passes the safety checks and where it is in the approval process.</> },
        ]}
        example={<>The planner ranks the cheapest safe option first (#1). If things change later (for example, a hospital starts using more), an option can stop being safe, and the status column says so.</>}
      />
      <table className="table" style={{ margin: "0 -20px", width: "calc(100% + 40px)" }}>
        <thead>
          <tr>
            <th>Option</th>
            <th style={{ width: "34%" }}>Cost</th>
            <th>Arrives</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {plans.map((p) => (
            <CompareRow key={p.id} plan={p} max={max} />
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function CompareRow({ plan, max }: { plan: Plan; max: number }) {
  const feasible = usePlanFeasible(plan);
  const cost = costOf(plan);
  return (
    <tr>
      <td>
        <span className="meta">#{plan.rank} </span>
        <b>{plan.title}</b>
      </td>
      <td>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }} title={plan.cost}>
          <div style={{ flex: 1, height: 10, position: "relative" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${(cost / max) * 100}%`, background: "var(--series-1)", borderRadius: "0 4px 4px 0", minWidth: 4 }} />
          </div>
          <span className="num nowrap" style={{ width: 62, textAlign: "right" }}>
            ${cost.toLocaleString()}
          </span>
        </div>
      </td>
      <td className="small ink-2">{plan.leadTime}</td>
      <td>{feasible ? <Status tone={PLAN_STATUS[plan.status].tone}>{PLAN_STATUS[plan.status].text}</Status> : <Status tone="crit">Breaks a safety rule</Status>}</td>
    </tr>
  );
}

function RouteAction({ plan }: { plan: Plan }) {
  const { state, dispatch } = useStore();
  const feasible = usePlanFeasible(plan);
  if (plan.status !== "proposed") return null;
  const canRoute = state.role === "supply" && feasible;
  return (
    <>
      <span className="small muted">{state.role !== "supply" ? "Only the supply manager can choose which plan goes for approval." : !feasible ? "Unsafe plans can't be sent." : ""}</span>
      <button className="btn" disabled={!canRoute} onClick={() => dispatch({ type: "route", planId: plan.id })}>
        Send this one for approval instead
      </button>
    </>
  );
}
