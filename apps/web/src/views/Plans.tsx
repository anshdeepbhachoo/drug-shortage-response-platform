import { useEffect, useState } from "react";
import { PlanCard, planStatus, usePlanFeasible } from "../components/PlanCard";
import { drugById, siteName } from "../data/mock";
import { lineCoverage } from "../lib/risk";
import { Card, ChartGuide, Empty, PageHead, Status, formatDays, type Tone } from "../components/ui";
import { useStore } from "../lib/store";
import type { Plan, SupplyEvent } from "../lib/types";

const costOf = (p: Plan) => Number((p.cost.match(/\$([\d,]+)/)?.[1] ?? "0").replace(/,/g, ""));

export function Plans({ focusEvent }: { focusEvent?: string | null } = {}) {
  const { state } = useStore();
  const live = state.live !== null;
  // Opened from an alert or the Stock page: start on that drug.
  const focusDrug = (focusEvent && state.events.find((e) => e.id === focusEvent)?.drugId) || "";
  const [drugFilter, setDrugFilter] = useState(focusDrug);
  const [siteFilter, setSiteFilter] = useState("");
  useEffect(() => setDrugFilter(focusDrug), [focusDrug]);

  if (state.plans.length === 0) {
    return (
      <>
        <PageHead label="Plans" title="What are our options?" lead="Plans appear here as soon as an alert puts a hospital at risk." />
        <Empty title="No plans yet">{live ? "Run a day at the top: the platform plans for every drug in danger on its own." : "Nothing is at risk right now. Move the story on to see a supplier delay."}</Empty>
      </>
    );
  }

  const drugIds = [...new Set(state.plans.map((p) => p.drugId))];
  const siteIds = [...new Set(state.plans.flatMap((p) => p.sites ?? []))].sort();
  const filters = (live || drugIds.length > 1) && (
    <div className="row" style={{ gap: 16 }}>
      <label className="row" style={{ gap: 8 }}>
        <span className="label">Drug</span>
        <select className="input" value={drugIds.includes(drugFilter) ? drugFilter : ""} onChange={(e) => setDrugFilter(e.target.value)} aria-label="Filter plans by drug">
          <option value="">{live ? "All drugs: overview" : `All drugs (${drugIds.length})`}</option>
          {drugIds.map((id) => (
            <option key={id} value={id}>
              {drugById(id)?.name ?? id}
            </option>
          ))}
        </select>
      </label>
      {live && siteIds.length > 0 && (
        <label className="row" style={{ gap: 8 }}>
          <span className="label">Hospital</span>
          <select className="input" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)} aria-label="Filter plans by hospital">
            <option value="">All hospitals</option>
            {siteIds.map((id) => (
              <option key={id} value={id}>
                {siteName(id)}
              </option>
            ))}
          </select>
        </label>
      )}
    </div>
  );

  // Live, no drug chosen: one row per drug the platform is planning for, most urgent first.
  if (live && !drugIds.includes(drugFilter)) {
    return (
      <>
        <PageHead
          label="Plans"
          title="Plans by drug"
          lead="The platform plans for every drug in danger on its own, from each day's stock. Emails and reports refine those plans. Pick a drug to see its options."
        />
        {filters}
        <DrugOverview siteFilter={siteFilter} onOpen={setDrugFilter} />
      </>
    );
  }

  const plans = state.plans.filter(
    (p) => (!drugIds.includes(drugFilter) || p.drugId === drugFilter) && (!siteFilter || (p.sites ?? []).includes(siteFilter)),
  );
  if (plans.length === 0) {
    return (
      <>
        <PageHead label="Plans" title="What are our options?" lead="No plans match these filters." />
        {filters}
        <Empty title="Nothing here">No plan for this drug involves {siteName(siteFilter)}. Clear the hospital filter to see them all.</Empty>
      </>
    );
  }

  // The current plan: the one opened from an alert, else the newest one still in play.
  const live_ = plans.find((p) => p.status !== "superseded" && p.status !== "rejected") ?? plans[0];
  const latestRun = (focusEvent && plans.find((p) => p.eventId === focusEvent && p.status !== "superseded")?.solverRun) || live_.solverRun;
  const current = plans.filter((p) => p.solverRun === latestRun).sort((a, b) => a.rank - b.rank);
  const earlier = plans.filter((p) => p.solverRun !== latestRun);
  const event = state.events.find((e) => e.id === current[0].eventId)!;
  const replanned = latestRun === "SOLVER-32";

  return (
    <>
      <PageHead
        label="Plans"
        title={live ? `${drugById(current[0].drugId)?.name ?? "Plan"}: options` : "What are our options?"}
        lead={`${event.summary} Here are the ways to cover the gap, best first. Every option is checked in code before anyone sees it.`}
      />
      {filters}
      {live && (
        <button className="link small" style={{ alignSelf: "flex-start" }} onClick={() => setDrugFilter("")}>
          ← All drugs
        </button>
      )}

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
  const { state } = useStore();
  const live = state.live !== null;
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
      <td>{feasible ? <Status tone={planStatus(plan.status, live).tone}>{planStatus(plan.status, live).text}</Status> : <Status tone="crit">Breaks a safety rule</Status>}</td>
    </tr>
  );
}

function RouteAction({ plan }: { plan: Plan }) {
  const { state, dispatch } = useStore();
  const feasible = usePlanFeasible(plan);
  // Live plans are routed by the platform: verified options go straight to approval.
  if (plan.status !== "proposed" || state.live) return null;
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

const SOURCE_WORDS: Record<string, string> = {
  stock_watch: "stock watch",
  supplier_email: "supplier email",
  shortage_report: "Health Canada report",
  manual: "supply manager",
  site_report: "hospital report",
};

const STATE_TONE = (state = ""): Tone =>
  state.includes("approved") ? "good" : state.includes("waiting") ? "warn" : state.includes("No plan") ? "neutral" : "crit";

/** Live: one row per drug with a plan in play, most urgent first. */
function DrugOverview({ siteFilter, onOpen }: { siteFilter: string; onOpen: (drugId: string) => void }) {
  const { state } = useStore();
  const byDrug = new Map<string, SupplyEvent[]>();
  for (const event of state.events) {
    if (event.status === "monitoring") continue;
    if (!state.plans.some((p) => p.eventId === event.id)) continue;
    byDrug.set(event.drugId, [...(byDrug.get(event.drugId) ?? []), event]);
  }

  const rows = [...byDrug.entries()]
    .map(([drugId, events]) => {
      const current = events[0]; // newest alert for the drug
      const sites = [...new Set(events.flatMap((e) => e.sites ?? (e.siteId ? [e.siteId] : [])))];
      const lines = state.stock.filter((l) => l.drugId === drugId && (sites.length === 0 || sites.includes(l.siteId)));
      const days = lines.length ? Math.min(...lines.map(lineCoverage)) : Infinity;
      const sources = [...new Set(events.flatMap((e) => (e.evidence ?? []).map((ev) => SOURCE_WORDS[ev.source] ?? ev.source)))];
      return { drugId, current, sites, days, sources, critical: drugById(drugId)?.criticalList ?? false };
    })
    .filter((r) => !siteFilter || r.sites.includes(siteFilter))
    .sort((a, b) => a.days - b.days || Number(b.critical) - Number(a.critical));

  if (rows.length === 0) {
    return <Empty title="Nothing here">No drug with a plan involves {siteName(siteFilter)}.</Empty>;
  }

  return (
    <div className="attention">
      {rows.map((r) => (
        <button key={r.drugId} className="attention-item" onClick={() => onOpen(r.drugId)}>
          <span className="big">{Number.isFinite(r.days) ? formatDays(r.days) : "–"}</span>
          <span>
            <strong>{drugById(r.drugId)?.name ?? r.drugId}</strong>
            {r.critical && <span className="tag" style={{ marginLeft: 8 }}>critical</span>}
            <div className="small ink-2">
              Days left at the worst hospital · {r.sites.length ? r.sites.map(siteName).join(", ") : "several hospitals"} · raised by{" "}
              {r.sources.join(", ") || "the platform"}
            </div>
          </span>
          <Status tone={STATE_TONE(r.current.planState)}>{r.current.planState ?? "Plan"}</Status>
        </button>
      ))}
    </div>
  );
}
