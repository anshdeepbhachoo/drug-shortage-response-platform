import { useState } from "react";
import { DRUGS, SITES, dayLabel, siteTag } from "../data/mock";
import { lineCoverage, tierFor } from "../lib/risk";
import type { StockLine, Tier } from "../lib/types";
import { formatDays } from "./ui";

export type HeatCell = { line: StockLine; days: number; after?: number; tier: Tier };

/** Days of stock for every drug at every hospital, coloured by urgency (a Power BI style matrix).
 *  Drugs with 14+ days everywhere collapse into one row so the matrix shows only what matters. */
export function Heatmap({
  lines,
  after,
  selected,
  highlightSite,
  onPick,
}: {
  lines: StockLine[];
  /** Days after approved plans, by "drug|site", when they differ. */
  after?: Map<string, number>;
  selected?: { drugId: string; siteId: string };
  highlightSite?: string | null;
  onPick?: (drugId: string, siteId: string) => void;
}) {
  const [showAll, setShowAll] = useState(false);
  const cells = new Map<string, HeatCell>();
  for (const line of lines) {
    const days = lineCoverage(line);
    cells.set(`${line.drugId}|${line.siteId}`, { line, days, after: after?.get(`${line.drugId}|${line.siteId}`), tier: tierFor(days, line.safetyDays) });
  }
  const worst = (drugId: string) => Math.min(...SITES.map((s) => cells.get(`${drugId}|${s.id}`)?.days ?? Infinity));
  const drugs = DRUGS.filter((d) => SITES.some((s) => cells.has(`${d.id}|${s.id}`))).sort((a, b) => worst(a.id) - worst(b.id));
  const atRisk = drugs.filter((d) => SITES.some((s) => (cells.get(`${d.id}|${s.id}`)?.tier ?? "green") !== "green"));
  const fine = drugs.length - atRisk.length;
  const shown = showAll ? drugs : atRisk;

  return (
    <div className="heat-wrap">
      <table className="heat">
        <thead>
          <tr>
            <th scope="col" className="heat-drug">Drug</th>
            {SITES.map((s) => (
              <th key={s.id} scope="col" className={highlightSite === s.id ? "hl" : ""} title={s.name}>
                {siteTag(s.id)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.map((d) => (
            <tr key={d.id}>
              <th scope="row" className="heat-drug" title={d.name}>
                {d.name}
              </th>
              {SITES.map((s) => {
                const c = cells.get(`${d.id}|${s.id}`);
                if (!c) return <td key={s.id} className="heat-none">–</td>;
                const isSel = selected?.drugId === d.id && selected?.siteId === s.id;
                const runs = c.days >= 60 ? "not within 60 days" : dayLabel(Math.floor(c.days));
                const label = `${d.name} at ${s.name}: ${formatDays(c.days)} days, runs out ${runs}${c.after !== undefined ? `; ${formatDays(c.after)} days with the approved plan` : ""}`;
                return (
                  <td key={s.id} className={`heat-${c.tier} ${isSel ? "sel" : ""} ${highlightSite === s.id ? "hl" : ""}`} title={label}>
                    {onPick ? (
                      <button onClick={() => onPick(d.id, s.id)} aria-label={label}>
                        {formatDays(c.days)}
                        {c.after !== undefined && <span className="heat-after">→{formatDays(c.after)}</span>}
                      </button>
                    ) : (
                      <>{formatDays(c.days)}</>
                    )}
                  </td>
                );
              })}
            </tr>
          ))}
          {fine > 0 && (
            <tr>
              <th scope="row" className="heat-drug" colSpan={SITES.length + 1}>
                <button className="link small" onClick={() => setShowAll((v) => !v)}>
                  {showAll ? `Hide the ${fine} drugs with 14+ days everywhere` : `+ ${fine} more drugs, all 14+ days everywhere`}
                </button>
              </th>
            </tr>
          )}
        </tbody>
      </table>
      <div className="legend" style={{ marginTop: 8 }}>
        <span><i className="heat-red" />Under 7 days</span>
        <span><i className="heat-amber" />Under 14 days</span>
        <span><i className="heat-green" />14+ days</span>
        {after && after.size > 0 && <span>“→ n”: days with the approved plan</span>}
      </div>
    </div>
  );
}
