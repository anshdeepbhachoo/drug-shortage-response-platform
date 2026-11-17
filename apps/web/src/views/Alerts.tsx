import { useState } from "react";
import { PlanCard } from "../components/PlanCard";
import { Empty, PageHead, Status, formatDays, type Tone } from "../components/ui";
import { SITES, dayLabel, drugById, siteName } from "../data/mock";
import { lineCoverage, tierFor } from "../lib/risk";
import { currentOptions, stageOf, type Stage } from "../lib/response";
import { useStore } from "../lib/store";
import type { Tier } from "../lib/types";

type Go = (view: "approvals" | "events", eventId?: string) => void;

const STAGES: { id: Stage; title: string; note: string }[] = [
  { id: "decide", title: "Needs decision", note: "a checked plan is waiting for approval" },
  { id: "attention", title: "Needs attention", note: "no checked plan yet" },
  { id: "tracking", title: "Approved · tracking", note: "follow-up is monitoring the response" },
];
const TIER_WORD: Record<Tier, string> = { red: "Critical", amber: "Watch", green: "OK" };
const TIER_TONE: Record<Tier, Tone> = { red: "crit", amber: "warn", green: "good" };
const SOURCE_WORD: Record<string, string> = {
  stock_watch: "Stock watch",
  supplier_email: "Supplier notice",
  shortage_report: "Health Canada report",
  manual: "Requested by a person",
};

/** One alert per drug: where it bites, how urgent, and where its response stands. */
function useAlerts() {
  const { state } = useStore();
  return state.events
    .filter((e) => e.status === "open" || e.status === "mitigated")
    .map((event) => {
      const sites = event.sites?.length ? event.sites : SITES.map((s) => s.id);
      const lines = state.stock
        .filter((l) => l.drugId === event.drugId && sites.includes(l.siteId))
        .map((l) => ({ line: l, days: lineCoverage(l), tier: tierFor(lineCoverage(l), l.safetyDays) as Tier }))
        .sort((a, b) => a.days - b.days);
      const worst = lines[0];
      const options = currentOptions(state.plans, event.id);
      return { event, lines, worst, tier: (worst?.tier ?? "green") as Tier, options, stage: stageOf(options) };
    })
    .sort((a, b) => (a.worst?.days ?? Infinity) - (b.worst?.days ?? Infinity));
}

export function Alerts({ go, focusEvent }: { go: Go; focusEvent?: string | null }) {
  const alerts = useAlerts();
  const [picked, setPicked] = useState<string | null>(focusEvent ?? null);
  const current = alerts.find((a) => a.event.id === picked) ?? alerts.find((a) => a.stage !== "tracking") ?? alerts[0];

  if (alerts.length === 0) {
    return (
      <>
        <PageHead label="Shortage Alerts" title="Shortage Alerts" lead="No open alerts." />
        <Empty title="No active shortages">Alerts appear when the daily run finds a drug that will not last at a facility.</Empty>
      </>
    );
  }

  const count = (stage: Stage) => alerts.filter((a) => a.stage === stage).length;
  const select = (id: string) => {
    setPicked(id);
    requestAnimationFrame(() => document.getElementById("alert-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  };

  return (
    <>
      <PageHead
        label="Shortage Alerts"
        title="Shortage Alerts"
        lead={`${alerts.length} active · ${count("decide")} need a decision · ${count("attention")} need attention · ${count("tracking")} approved and tracked · select an alert for its response plan`}
      />

      <section className="card" aria-labelledby="alerts-overview">
        <header className="card-head">
          <div>
            <div className="label">One alert per drug · coloured by lowest days of supply</div>
            <h3 id="alerts-overview">Alert overview</h3>
          </div>
        </header>
        <div className="card-body alert-stages">
          {STAGES.map((stage) => {
            const items = alerts.filter((a) => a.stage === stage.id);
            return (
              <div key={stage.id} className="alert-stage">
                <div className="alert-stage-head">
                  <b>{stage.title}</b> <span className="num muted">{items.length}</span>
                  <div className="small muted">{stage.note}</div>
                </div>
                {items.length === 0 ? (
                  <p className="small muted">None.</p>
                ) : (
                  items.map((a) => (
                    <button
                      key={a.event.id}
                      className={`alert-tile tier-${a.tier} ${current?.event.id === a.event.id ? "on" : ""}`}
                      onClick={() => select(a.event.id)}
                      aria-pressed={current?.event.id === a.event.id}
                    >
                      <span className="alert-name">{drugById(a.event.drugId)?.name ?? a.event.drugId}</span>
                      <span className="alert-days">
                        {a.worst ? formatDays(a.worst.days) : "–"}
                        <small> d</small>
                      </span>
                      <span className="alert-meta">
                        {a.lines.filter((l) => l.tier !== "green").length} facilit{a.lines.filter((l) => l.tier !== "green").length === 1 ? "y" : "ies"} at risk
                        {a.worst ? ` · lowest ${siteName(a.worst.line.siteId)}` : ""}
                      </span>
                      {a.options.some((p) => p.optionType === "substitute") && <span className="alert-flag">Substitute available</span>}
                    </button>
                  ))
                )}
              </div>
            );
          })}
        </div>
      </section>

      {current && <AlertDetail a={current} go={go} />}
    </>
  );
}

function AlertDetail({ a, go }: { a: ReturnType<typeof useAlerts>[number]; go: Go }) {
  const { state, live } = useStore();
  const drug = drugById(a.event.drugId);
  const sources = [...new Set((a.event.evidence ?? []).map((e) => SOURCE_WORD[e.source] ?? e.source))];
  const canPlan = state.role === "supply" && live.busy === null;
  // The simulated day the alert was first raised, from its source reference (e.g. "stock_watch:2026-11-15:…").
  const day = a.event.sourceRef.match(/\d{4}-\d{2}-\d{2}/)?.[0];
  const raisedOn = day ? new Date(`${day}T12:00`).toLocaleDateString("en-CA", { month: "short", day: "numeric" }) : null;
  return (
    <section id="alert-detail" className="card alert-detail" aria-labelledby="alert-detail-title">
      <header className="card-head">
        <div>
          <div className="label">
            Alert detail · {drug?.criticalList ? "critical-list drug" : "tracked drug"}
            {raisedOn ? ` · first raised ${raisedOn}` : ""}
          </div>
          <h3 id="alert-detail-title">{drug?.name ?? a.event.drugId}</h3>
        </div>
        <Status tone={a.stage === "tracking" ? "good" : a.stage === "decide" ? "warn" : "crit"}>{a.event.planState ?? "No plan"}</Status>
      </header>

      <div className="card-body alert-detail-grid">
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Facilities · days of supply</div>
          <table className="table alert-table">
            <thead>
              <tr>
                <th>Facility</th>
                <th className="num">Days</th>
                <th>Runs out</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {a.lines.map((l) => (
                <tr key={l.line.siteId}>
                  <td>{siteName(l.line.siteId)}</td>
                  <td className="num">
                    <b>{formatDays(l.days)}</b>
                  </td>
                  <td>{l.days >= 60 ? "60+ days" : dayLabel(Math.floor(l.days))}</td>
                  <td>
                    <Status tone={TIER_TONE[l.tier]}>{TIER_WORD[l.tier]}</Status>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 10 }}>
            Signals: {sources.join(" · ") || "stock watch"}.{" "}
            <button className="link small" onClick={() => go("events", a.event.id)}>
              Review signals →
            </button>
          </p>
        </div>

        <div className="stack" style={{ gap: 10 }}>
          <div className="row-between">
            <div className="label">Response plan · {a.options.length} option{a.options.length === 1 ? "" : "s"}</div>
            {a.stage === "decide" && (
              <button className="btn primary" onClick={() => go("approvals", a.event.id)}>
                Decide in Approval Queue
              </button>
            )}
          </div>
          {a.options.length === 0 ? (
            <div className="callout warn">
              <span className="ico" aria-hidden>
                !
              </span>
              <div style={{ flex: 1 }}>
                <strong>No plan generated yet.</strong>
                <span className="ink-2">The planner can propose options from current stock now.</span>
              </div>
              <button className="btn primary" disabled={!canPlan} onClick={() => void live.planDrug(a.event.drugId, a.worst?.line.siteId)}>
                {live.busy === "Planning a response" ? "Generating…" : "Generate plan"}
              </button>
            </div>
          ) : (
            a.options.map((p) => <PlanCard key={p.id} plan={p} compact recommended={p.rank === 1 && p.status !== "approved"} />)
          )}
          {a.stage === "attention" && a.options.length > 0 && (
            <p className="small muted">No transfer passed the safety checks. The options above need a person: escalate, conserve, or an approved substitute.</p>
          )}
        </div>
      </div>
    </section>
  );
}
