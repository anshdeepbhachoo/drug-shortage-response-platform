import { useState } from "react";
import { Heatmap } from "../components/Heatmap";
import { Card, Empty, PageHead, Status, formatDays, type Tone } from "../components/ui";
import { SITES, dayLabel, drugById } from "../data/mock";
import { lineCoverage, tierFor } from "../lib/risk";
import { useStore } from "../lib/store";
import type { FollowUp } from "../lib/api";
import type { SupplyEvent, Tier } from "../lib/types";

type Go = (view: "approvals" | "events" | "board", eventId?: string) => void;

const TIER_COLOUR: Record<Tier, string> = { red: "var(--crit)", amber: "var(--warn)", green: "var(--good)" };
const FOLLOW_TONE: Record<string, Tone> = { wait: "good", close: "good", nudge: "warn", escalate: "crit", replan: "crit" };

/** The drug's one current alert (open, or approved and not yet closed). */
function alertFor(events: SupplyEvent[], drugId: string) {
  const mine = events.filter((e) => e.drugId === drugId);
  return mine.find((e) => e.status === "open") ?? mine.find((e) => e.status === "mitigated");
}

/** One summary per hospital: what is at risk there, and what is being done about it. */
function useHospitals() {
  const { state } = useStore();
  const followups: FollowUp[] = state.live?.followups ?? [];
  return SITES.map((site) => {
    const lines = state.stock
      .filter((l) => l.siteId === site.id)
      .map((l) => ({ line: l, days: lineCoverage(l), tier: tierFor(lineCoverage(l), l.safetyDays) }))
      .sort((a, b) => a.days - b.days);
    const atRisk = lines.filter((l) => l.tier !== "green");
    const critical = atRisk.filter((l) => l.tier === "red").length;
    const worst: Tier = critical ? "red" : atRisk.length ? "amber" : "green";
    const waiting = new Set(
      state.plans.filter((p) => p.status === "pending-approval" && (p.sites ?? []).includes(site.id)).map((p) => p.solverRun),
    ).size;
    const incoming = followups.filter((f) => f.to === site.name);
    const outgoing = followups.filter((f) => f.from === site.name);
    return { site, atRisk, critical, worst, waiting, incoming, outgoing };
  }).sort((a, b) => (a.atRisk[0]?.days ?? Infinity) - (b.atRisk[0]?.days ?? Infinity));
}

export function Hospitals({ go }: { go: Go }) {
  const { state } = useStore();
  const hospitals = useHospitals();
  const [picked, setPicked] = useState<string | null>(null);
  const current = hospitals.find((h) => h.site.id === picked) ?? null;

  if (state.stock.length === 0) {
    return (
      <>
        <PageHead label="Hospitals" title="How is each hospital doing?" lead="No hospital stock has arrived yet." />
        <Empty title={`No data for ${dayLabel(0)} yet`}>Run the day on Home first: it loads the hospitals' stock.</Empty>
      </>
    );
  }

  const red = hospitals.filter((h) => h.worst === "red").length;
  const amber = hospitals.filter((h) => h.worst === "amber").length;

  return (
    <>
      <PageHead
        label="Hospitals"
        title="How is each hospital doing?"
        lead={`${red} hospital${red === 1 ? "" : "s"} with a drug under 7 days, ${amber} more with one under 14. Hover a hospital for a summary; click it to see everything about it.`}
      />

      <div className="hosp-grid">
        {hospitals.map((h) => (
          <button
            key={h.site.id}
            className={`hosp-card ${picked === h.site.id ? "on" : ""}`}
            style={{ borderLeftColor: TIER_COLOUR[h.worst] }}
            onClick={() => setPicked(picked === h.site.id ? null : h.site.id)}
            aria-pressed={picked === h.site.id}
          >
            <span className="hosp-name">{h.site.name}</span>
            <span className="small" style={{ color: h.worst === "red" ? "var(--crit-text)" : h.worst === "amber" ? "var(--warn-text)" : "var(--good-text)" }}>
              {h.critical ? `${h.critical} drug${h.critical > 1 ? "s" : ""} under 7 days` : h.atRisk.length ? `${h.atRisk.length} drug${h.atRisk.length > 1 ? "s" : ""} under 14 days` : "All drugs 14+ days"}
            </span>
            <span className="small muted">
              {[
                h.waiting ? `${h.waiting} plan${h.waiting > 1 ? "s" : ""} waiting` : null,
                h.incoming.length ? `${h.incoming.reduce((n, f) => n + f.units, 0)} units coming in` : null,
                h.outgoing.length ? `${h.outgoing.reduce((n, f) => n + f.units, 0)} units going out` : null,
              ]
                .filter(Boolean)
                .join(" · ") || "Nothing in progress"}
            </span>
            <span className="hosp-tip" role="tooltip">
              <b>{h.site.name}</b>
              {h.atRisk.length === 0 && <span>Nothing at risk.</span>}
              {h.atRisk.slice(0, 4).map((l) => (
                <span key={l.line.drugId}>
                  {drugById(l.line.drugId)?.name}: {formatDays(l.days)} d · {(alertFor(state.events, l.line.drugId)?.planState ?? "no problem raised yet").toLowerCase()}
                </span>
              ))}
              {h.atRisk.length > 4 && <span className="muted">+{h.atRisk.length - 4} more</span>}
              {h.incoming.length > 0 && <span>In: {h.incoming.map((f) => `${f.units} from ${f.from.split(" ")[0]}`).join(", ")}</span>}
              {h.outgoing.length > 0 && <span>Out: {h.outgoing.map((f) => `${f.units} to ${f.to.split(" ")[0]}`).join(", ")}</span>}
            </span>
          </button>
        ))}
      </div>

      {current && <HospitalDetail h={current} go={go} close={() => setPicked(null)} />}

      <Card label="Days of stock" title="Every drug at every hospital">
        <Heatmap lines={state.stock} highlightSite={picked} onPick={(_, siteId) => setPicked(siteId)} />
      </Card>
    </>
  );
}

function HospitalDetail({ h, go, close }: { h: ReturnType<typeof useHospitals>[number]; go: Go; close: () => void }) {
  const { state } = useStore();
  const plans = [...new Set(state.plans.filter((p) => p.status === "pending-approval" && (p.sites ?? []).includes(h.site.id)).map((p) => p.solverRun))];
  const transfers = [...h.incoming.map((f) => ({ f, dir: "in" as const })), ...h.outgoing.map((f) => ({ f, dir: "out" as const }))];
  return (
    <Card label="Selected hospital" title={h.site.name} actions={<button className="link small" onClick={close}>Close</button>}>
      <div className="grid-even">
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Drugs at risk</div>
          {h.atRisk.length === 0 ? (
            <p className="small muted">Every drug has at least 14 days here.</p>
          ) : (
            <ul className="tile-rows">
              {h.atRisk.map((l) => {
                const alert = alertFor(state.events, l.line.drugId);
                return (
                  <li key={l.line.drugId}>
                    <span>
                      <b>{drugById(l.line.drugId)?.name}</b> · {formatDays(l.days)} d · {(alert?.planState ?? "no problem raised yet").toLowerCase()}
                    </span>
                    {alert && (
                      <button className="link small" onClick={() => go("events", alert.id)}>
                        Problem →
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
        <div className="stack" style={{ gap: 16 }}>
          <div>
            <div className="label" style={{ marginBottom: 8 }}>Plans waiting for a decision</div>
            {plans.length === 0 ? (
              <p className="small muted">None involve this hospital.</p>
            ) : (
              <ul className="tile-rows">
                {plans.map((run) => {
                  const first = state.plans.find((p) => p.solverRun === run)!;
                  return (
                    <li key={run}>
                      <span>{drugById(first.drugId)?.name}</span>
                      <button className="link small" onClick={() => go("approvals")}>
                        Decide →
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <div>
            <div className="label" style={{ marginBottom: 8 }}>Approved transfers</div>
            {transfers.length === 0 ? (
              <p className="small muted">None in or out.</p>
            ) : (
              <ul className="tile-rows">
                {transfers.map(({ f, dir }) => (
                  <li key={f.id}>
                    <span>
                      {dir === "in" ? `${f.units} ${f.drug.split(" ")[0]} from ${f.from}` : `${f.units} ${f.drug.split(" ")[0]} to ${f.to}`}
                    </span>
                    <Status tone={f.decision ? FOLLOW_TONE[f.decision] ?? "neutral" : "neutral"}>{f.status.split(":")[0]}</Status>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
