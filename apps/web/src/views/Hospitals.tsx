import { useState } from "react";
import { SupplyChart } from "../components/SupplyChart";
import { Card, Empty, PageHead, Status, formatDays, type Tone } from "../components/ui";
import { SITES, dayLabel, drugById } from "../data/mock";
import { responseFor } from "../lib/response";
import { lineCoverage, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { FollowUp } from "../lib/api";
import type { SupplyEvent, Tier } from "../lib/types";

type Go = (view: "approvals" | "alerts", eventId?: string) => void;

const FOLLOW_TONE: Record<string, Tone> = { wait: "good", close: "good", nudge: "warn", escalate: "crit", replan: "crit" };

/** The drug's one current alert (open, or approved and not yet closed). */
function alertFor(events: SupplyEvent[], drugId: string) {
  const mine = events.filter((e) => e.drugId === drugId);
  return mine.find((e) => e.status === "open") ?? mine.find((e) => e.status === "mitigated");
}

/** Shortage risk index weights: transparent and printed on the page so the number can be audited. */
const SEVERITY_WEIGHT: Record<Tier, number> = { red: 3, amber: 1, green: 0 };
const CRITICAL_LIST_MULTIPLIER = 2;
/** Fixed red / amber / green bands, so a colour means the same thing every day (not relative to today's worst). */
const RISK_BANDS = [
  { min: 0, label: "Low" },
  { min: 6, label: "Moderate" },
  { min: 12, label: "High" },
];
const band = (score: number) => RISK_BANDS.reduce((b, r, i) => (score >= r.min ? i : b), 0);

/** One summary per hospital: aggregated shortage risk, and what is being done about it. */
function useHospitals() {
  const { state } = useStore();
  const followups: FollowUp[] = state.live?.followups ?? [];
  // Stock as it will be once approved transfers land, to show what approvals have done.
  const projected = withApprovedPlans(state.stock, state.plans, state.events);
  const weight = (drugId: string, tier: Tier) => SEVERITY_WEIGHT[tier] * (drugById(drugId)?.criticalList ? CRITICAL_LIST_MULTIPLIER : 1);
  return SITES.map((site) => {
    const lines = state.stock
      .filter((l) => l.siteId === site.id)
      .map((l) => ({ line: l, days: lineCoverage(l), tier: tierFor(lineCoverage(l), l.safetyDays) }))
      .sort((a, b) => a.days - b.days);
    const atRisk = lines.filter((l) => l.tier !== "green");
    const critical = atRisk.filter((l) => l.tier === "red").length;
    const watch = atRisk.length - critical;
    const score = atRisk.reduce((sum, l) => sum + weight(l.line.drugId, l.tier), 0);
    const projectedScore = projected
      .filter((l) => l.siteId === site.id)
      .reduce((sum, l) => sum + weight(l.drugId, tierFor(lineCoverage(l), l.safetyDays)), 0);
    const responses = atRisk.map((l) => responseFor(l.line.drugId, state.events, state.plans, followups));
    const covered = responses.filter((r) => r.stage === "tracking").length;
    const waiting = new Set(
      state.plans.filter((p) => p.status === "pending-approval" && (p.sites ?? []).includes(site.id)).map((p) => p.solverRun),
    ).size;
    const incoming = followups.filter((f) => f.to === site.name);
    const outgoing = followups.filter((f) => f.from === site.name);
    return { site, atRisk, critical, watch, score, projectedScore, covered, band: band(score), waiting, incoming, outgoing };
  }).sort((a, b) => b.score - a.score || (a.atRisk[0]?.days ?? Infinity) - (b.atRisk[0]?.days ?? Infinity));
}

export function Hospitals({ go }: { go: Go }) {
  const { state } = useStore();
  const hospitals = useHospitals();
  const [picked, setPicked] = useState<string | null>(null);
  // Opens on the facility with the highest risk index; a tile click drills into another.
  const current = hospitals.find((h) => h.site.id === picked) ?? hospitals[0] ?? null;

  if (state.stock.length === 0) {
    return (
      <>
        <PageHead label="Network Dashboard" title="Network Dashboard" lead="No facility data loaded." />
        <Empty title={`No data for ${dayLabel(0)} yet`}>Run the day on the Overview first: it loads facility data.</Empty>
      </>
    );
  }

  const elevated = hospitals.filter((h) => h.band === RISK_BANDS.length - 1).length;
  const select = (siteId: string) => {
    setPicked(siteId);
    requestAnimationFrame(() => document.getElementById("supply-chart")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  };

  return (
    <>
      <PageHead
        label="Network Dashboard"
        title="Network Dashboard"
        lead={`${elevated} of ${hospitals.length} facilities at high shortage risk · select a facility for item-level detail`}
      />

      <section className="card" aria-labelledby="risk-title">
        <header className="card-head">
          <div>
            <div className="label">Shortage risk index · by facility</div>
            <h3 id="risk-title">Network risk heatmap</h3>
          </div>
        </header>
        <div className="card-body">
          <div className="risk-grid" role="listbox" aria-label="Facilities by shortage risk">
            {hospitals.map((h) => (
              <button
                key={h.site.id}
                role="option"
                aria-selected={current?.site.id === h.site.id}
                className={`risk-tile risk-${h.band} ${current?.site.id === h.site.id ? "on" : ""}`}
                onClick={() => select(h.site.id)}
              >
                <span className="risk-name">{h.site.name}</span>
                <span className="risk-score">{h.score}</span>
                <span className="risk-band">{RISK_BANDS[h.band].label} risk</span>
                {h.projectedScore !== h.score && <span className="risk-after">After approved plans: {h.projectedScore}</span>}
                <span className="risk-meta">
                  {h.critical} critical · {h.watch} watch{h.covered ? ` · ${h.covered} with approved response` : ""}
                  {h.atRisk[0] ? ` · lowest ${drugById(h.atRisk[0].line.drugId)?.name.split(" ")[0]} ${formatDays(h.atRisk[0].days)} d` : ""}
                </span>
                <span className="hosp-tip" role="tooltip">
                  <b>{h.site.name}</b>
                  {h.atRisk.length === 0 && <span>No items at risk.</span>}
                  {h.atRisk.slice(0, 4).map((l) => (
                    <span key={l.line.drugId}>
                      {drugById(l.line.drugId)?.name}: {formatDays(l.days)} d · {responseFor(l.line.drugId, state.events, state.plans, state.live?.followups ?? []).label.toLowerCase()}
                    </span>
                  ))}
                  {h.atRisk.length > 4 && <span className="muted">+{h.atRisk.length - 4} more</span>}
                  <span className="muted">
                    {h.waiting} pending approval{h.waiting === 1 ? "" : "s"}
                    {h.incoming.length ? ` · ${h.incoming.reduce((n, f) => n + f.units, 0)} units inbound` : ""}
                    {h.outgoing.length ? ` · ${h.outgoing.reduce((n, f) => n + f.units, 0)} units outbound` : ""}
                  </span>
                </span>
              </button>
            ))}
          </div>
          <div className="risk-legend">
            <span className="small muted">Risk index</span>
            {RISK_BANDS.map((r, i) => (
              <span key={r.label} className="risk-key">
                <i className={`risk-${i}`} />
                {r.label} {i === RISK_BANDS.length - 1 ? `${r.min}+` : r.min === RISK_BANDS[i + 1].min - 1 ? r.min : `${r.min}–${RISK_BANDS[i + 1].min - 1}`}
              </span>
            ))}
          </div>
          <p className="small muted" style={{ marginTop: 8 }}>
            Index = sum over items at risk: critical (under 7 days of supply) {SEVERITY_WEIGHT.red}, watch (under 14 days) {SEVERITY_WEIGHT.amber}; critical-list drugs ×{CRITICAL_LIST_MULTIPLIER}.
          </p>
        </div>
      </section>

      {current && (
        <>
          <SupplyChart site={current.site.id} onSite={select} />
          <HospitalDetail h={current} go={go} />
        </>
      )}
    </>
  );
}

/** "Nov 16, 12:00" in the simulated clock's own terms (stored as UTC, shown as written). */
function arrival(iso: string) {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-CA", { month: "short", day: "numeric", timeZone: "UTC" });
  const time = d.toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "UTC" });
  return `${day}, ${time}`;
}

function HospitalDetail({ h, go }: { h: ReturnType<typeof useHospitals>[number]; go: Go }) {
  const { state } = useStore();
  const followups = state.live?.followups ?? [];
  // Drugs where this facility gives stock in a plan waiting for approval, without being at risk itself.
  const donorDrugs = [
    ...new Set(
      state.plans
        .filter((p) => p.status === "pending-approval" && p.transfers.some((t) => t.from === h.site.id))
        .map((p) => p.drugId),
    ),
  ].filter((drugId) => !h.atRisk.some((l) => l.line.drugId === drugId));
  const rows = [
    ...h.atRisk.map((l) => ({ ...l, donor: false })),
    ...donorDrugs.flatMap((drugId) => {
      const line = state.stock.find((l) => l.siteId === h.site.id && l.drugId === drugId);
      return line ? [{ line, days: lineCoverage(line), tier: tierFor(lineCoverage(line), line.safetyDays) as Tier, donor: true }] : [];
    }),
  ];
  const transfers = [...h.incoming.map((f) => ({ f, dir: "in" as const })), ...h.outgoing.map((f) => ({ f, dir: "out" as const }))];

  return (
    <Card label="Facility detail" title={`${h.site.name} · open actions`}>
      <div className="stack" style={{ gap: 20 }}>
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Items requiring action</div>
          {rows.length === 0 ? (
            <p className="small muted">Every drug has at least 14 days of supply here.</p>
          ) : (
            <div className="tablewrap">
              <table className="table detail-table">
                <thead>
                  <tr>
                    <th>Drug</th>
                    <th>Critical list</th>
                    <th className="num">Days of supply</th>
                    <th>Runs out</th>
                    <th>Risk</th>
                    <th>Response</th>
                    <th aria-label="Action" />
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => {
                    const drug = drugById(r.line.drugId);
                    const alert = alertFor(state.events, r.line.drugId);
                    const response = responseFor(r.line.drugId, state.events, state.plans, followups);
                    return (
                      <tr key={r.line.drugId}>
                        <td>
                          <b>{drug?.name ?? r.line.drugId}</b>
                        </td>
                        <td>{drug?.criticalList ? "Yes" : "No"}</td>
                        <td className="num">{formatDays(r.days)}</td>
                        <td>{r.days >= 60 ? "60+ days" : dayLabel(Math.floor(r.days))}</td>
                        <td>
                          {r.donor ? (
                            <span className="small muted">Donor</span>
                          ) : (
                            <Status tone={r.tier === "red" ? "crit" : r.tier === "amber" ? "warn" : "good"}>
                              {r.tier === "red" ? "Critical" : r.tier === "amber" ? "Watch" : "OK"}
                            </Status>
                          )}
                        </td>
                        <td>
                          <Status tone={response.tone}>
                            {r.donor && response.stage === "decide" ? "Pending approval · gives stock" : response.label}
                            {response.detail ? ` · ${response.detail}` : ""}
                          </Status>
                        </td>
                        <td className="detail-action">
                          {alert &&
                            (response.stage === "decide" ? (
                              <button className="link small" onClick={() => go("approvals", alert.id)}>
                                Decide →
                              </button>
                            ) : (
                              <button className="link small" onClick={() => go("alerts", alert.id)}>
                                View alert →
                              </button>
                            ))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <div>
          <div className="label" style={{ marginBottom: 8 }}>Approved transfers</div>
          {transfers.length === 0 ? (
            <p className="small muted">No approved transfers in or out.</p>
          ) : (
            <div className="tablewrap">
              <table className="table detail-table">
                <thead>
                  <tr>
                    <th>Drug</th>
                    <th>Route</th>
                    <th>This facility</th>
                    <th className="num">Units</th>
                    <th>Expected by</th>
                    <th>Follow-up</th>
                  </tr>
                </thead>
                <tbody>
                  {transfers.map(({ f, dir }) => (
                    <tr key={f.id}>
                      <td>
                        <b>{f.drug}</b>
                      </td>
                      <td className="route">
                        {dir === "out" ? <b>{f.from}</b> : f.from}
                        <span className="route-arrow" aria-label="to">→</span>
                        {dir === "in" ? <b>{f.to}</b> : f.to}
                      </td>
                      <td>
                        <span className={`role-chip ${dir}`}>{dir === "in" ? "Receiving" : "Sending"}</span>
                      </td>
                      <td className="num">{f.units}</td>
                      <td className="nowrap">
                        {f.expectedBy ? arrival(f.expectedBy) : "–"}
                        {f.decision === "close" ? (
                          <span className="small" style={{ color: "var(--good-text)" }}> · arrived</span>
                        ) : (f.hoursOverdue ?? 0) > 0 ? (
                          <span className="overdue"> overdue {Math.round(f.hoursOverdue!)} h</span>
                        ) : null}
                      </td>
                      <td>
                        {f.decision ? (
                          <Status tone={FOLLOW_TONE[f.decision] ?? "neutral"}>{f.status.split(":")[0]}</Status>
                        ) : (
                          <span className="small muted">First check this evening</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
