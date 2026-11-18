import { useEffect, useState } from "react";
import { DRUGS, SITES, dayLabel, drugById, siteName } from "../data/mock";
import { responseFor } from "../lib/response";
import { lineCoverage, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Tier } from "../lib/types";
import { Origin, Status, formatDays } from "./ui";

const AXIS_MAX = 60;
const TICKS = [0, 7, 14, 30, 60];
const MAX_BARS = 10;
const pct = (days: number) => `${(Math.min(days, AXIS_MAX) / AXIS_MAX) * 100}%`;
const TIER_WORD: Record<Tier, string> = { red: "Critical", amber: "Watch", green: "OK" };

type Row = { key: string; label: string; days: number; after?: number; tier: Tier; drugId: string; siteId: string };

/** Days of supply, ranked: one facility's most critical drugs, or one drug across every facility. */
export function SupplyChart({ site, onSite, focusDrug, focusKey }: {
  site: string;
  onSite: (siteId: string) => void;
  /** A drug to highlight in the facility view (drilled into from the heatmap). */
  focusDrug?: string;
  /** Changes on every drill-down, so the chart switches back to the facility view. */
  focusKey?: number;
}) {
  const { state } = useStore();
  const [mode, setMode] = useState<"facility" | "drug">("facility");
  useEffect(() => {
    if (focusKey) setMode("facility");
  }, [focusKey]);
  const projected = withApprovedPlans(state.stock, state.plans, state.events);
  const cells = state.stock.map((line) => {
    const days = lineCoverage(line);
    const p = projected.find((l) => l.drugId === line.drugId && l.siteId === line.siteId);
    const after = p ? lineCoverage(p) : days;
    return { line, days, after: Math.abs(after - days) > 0.05 ? after : undefined, tier: tierFor(days, line.safetyDays) as Tier };
  });
  // Default drug: the most critical one anywhere in the network.
  const worstDrug = [...cells].sort((a, b) => a.days - b.days)[0]?.line.drugId ?? DRUGS[0]?.id ?? "";
  const [drug, setDrug] = useState(worstDrug);
  const drugId = cells.some((c) => c.line.drugId === drug) ? drug : worstDrug;

  const ranked = (mode === "facility" ? cells.filter((c) => c.line.siteId === site) : cells.filter((c) => c.line.drugId === drugId)).sort(
    (a, b) => a.days - b.days,
  );
  const shown = ranked.slice(0, mode === "facility" ? MAX_BARS : SITES.length);
  // A drilled-into drug is always shown, even when it isn't among the lowest ten.
  const focused = mode === "facility" && focusDrug ? ranked.find((c) => c.line.drugId === focusDrug) : undefined;
  if (focused && !shown.includes(focused)) shown.push(focused);
  const rows: Row[] = shown
    .map((c) => ({
      key: `${c.line.drugId}|${c.line.siteId}`,
      label: mode === "facility" ? drugById(c.line.drugId)?.name ?? c.line.drugId : siteName(c.line.siteId),
      days: c.days,
      after: c.after,
      tier: c.tier,
      drugId: c.line.drugId,
      siteId: c.line.siteId,
    }));
  const [hover, setHover] = useState<string | null>(null);
  const tip = rows.find((r) => r.key === hover);
  const below7 = rows.filter((r) => r.tier === "red").length;

  return (
    <section className="card supply-chart" id="supply-chart" aria-labelledby="supply-chart-title">
      <header className="card-head supply-head">
        <div>
          <div className="label">Days of supply</div>
          <h3 id="supply-chart-title">
            {mode === "facility" ? `Most critical items · ${siteName(site)}` : `${drugById(drugId)?.name ?? drugId} · all facilities`}
          </h3>
          <span className="small muted">
            {below7} of {rows.length} under 7 days{mode === "facility" && rows.length === MAX_BARS ? ` · lowest ${MAX_BARS} shown` : ""}
          </span>
        </div>
        <div className="supply-controls">
          <div className="segmented" role="group" aria-label="View by">
            <button className={mode === "facility" ? "on" : ""} aria-pressed={mode === "facility"} onClick={() => setMode("facility")}>
              By facility
            </button>
            <button className={mode === "drug" ? "on" : ""} aria-pressed={mode === "drug"} onClick={() => setMode("drug")}>
              By drug
            </button>
          </div>
          {mode === "facility" ? (
            <select className="input" value={site} onChange={(e) => onSite(e.target.value)} aria-label="Facility">
              {SITES.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          ) : (
            <select className="input" value={drugId} onChange={(e) => setDrug(e.target.value)} aria-label="Drug">
              {[...new Set(cells.map((c) => c.line.drugId))]
                .map((id) => drugById(id))
                .filter(Boolean)
                .sort((a, b) => a!.name.localeCompare(b!.name))
                .map((d) => (
                  <option key={d!.id} value={d!.id}>
                    {d!.name}
                  </option>
                ))}
            </select>
          )}
        </div>
      </header>

      <div className="card-body">
        <div className="sc-plot" onMouseLeave={() => setHover(null)}>
          <div className="sc-axis" aria-hidden>
            <span />
            <div className="sc-scale">
              {TICKS.map((t) => (
                <span key={t} className="sc-tick" style={{ left: pct(t) }}>
                  {t === AXIS_MAX ? "60+" : t}
                </span>
              ))}
            </div>
            <span />
            <span className="sc-colhead">Response</span>
          </div>
          <div className="sc-rows">
            {rows.map((r) => {
              const runs = r.days >= AXIS_MAX ? "not within 60 days" : dayLabel(Math.floor(r.days));
              const label = `${r.label}: ${formatDays(r.days)} days of supply, ${TIER_WORD[r.tier].toLowerCase()}, runs out ${runs}`;
              return (
                <button
                  key={r.key}
                  className={`sc-row ${hover === r.key ? "hover" : ""} ${mode === "facility" && r.drugId === focusDrug ? "focus" : ""}`}
                  onMouseEnter={() => setHover(r.key)}
                  onFocus={() => setHover(r.key)}
                  onBlur={() => setHover(null)}
                  onClick={() => {
                    if (mode === "drug") {
                      setMode("facility");
                      onSite(r.siteId);
                    }
                  }}
                  aria-label={label}
                >
                  <span className="sc-label" title={r.label}>
                    {r.label}
                  </span>
                  <span className="sc-track">
                    {TICKS.slice(1).map((t) => (
                      <span key={t} aria-hidden className={`sc-grid ${t === 7 ? "crit" : t === 14 ? "warn" : ""}`} style={{ left: pct(t) }} />
                    ))}
                    <span className={`sc-bar ${r.tier}`} style={{ width: pct(r.days) }} />
                    {r.after !== undefined && <span className="sc-after" style={{ left: pct(r.after) }} />}
                    {tip?.key === r.key && (
                      <span className="tooltip sc-tip" role="tooltip" style={{ left: pct(Math.max(r.days, 8)) }}>
                        <b>
                          {mode === "facility" ? r.label : drugById(r.drugId)?.name} · {siteName(r.siteId)}
                        </b>
                        <span className="tip-row">
                          {formatDays(r.days)} days of supply · {TIER_WORD[r.tier]} · runs out {runs}
                        </span>
                        {r.after !== undefined && <span className="tip-row">With approved plan: {formatDays(r.after)} days</span>}
                        <span className="tip-row">Response: {responseFor(r.drugId, state.events, state.plans, state.live?.followups ?? []).label.toLowerCase()}</span>
                      </span>
                    )}
                  </span>
                  <span className="sc-val">
                    {r.tier !== "green" && <span aria-hidden>{r.tier === "red" ? "✕" : "!"}</span>}
                    <b>{formatDays(r.days)}</b> d
                  </span>
                  <span className="sc-resp">
                    {(() => {
                      const resp = responseFor(r.drugId, state.events, state.plans, state.live?.followups ?? []);
                      if (resp.stage === "none") return <span className="small muted">{r.tier === "green" ? "–" : "No alert"}</span>;
                      return (
                        <>
                        {resp.stage === "tracking" && <Origin by="human" compact />}{" "}
                        <Status tone={resp.tone}>
                          {resp.label}
                          {resp.detail ? ` · ${resp.detail}` : ""}
                        </Status>
                        </>
                      );
                    })()}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
        <div className="legend" style={{ marginTop: 12 }}>
          <span>
            <i style={{ background: "var(--crit)" }} />
            Critical · under 7 days
          </span>
          <span>
            <i style={{ background: "var(--warn)" }} />
            Watch · under 14 days
          </span>
          <span>
            <i style={{ background: "var(--good)" }} />
            OK · 14+ days
          </span>
          <span>
            <i className="line" style={{ background: "var(--ink)" }} />
            With approved plan
          </span>
        </div>
      </div>
    </section>
  );
}
