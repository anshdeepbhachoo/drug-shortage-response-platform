import { useState } from "react";
import { DRUGS, SITES, dayLabel, drugById, siteName, timeLabel } from "../data/mock";
import { lineCoverage, projection, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { StockLine } from "../lib/types";
import { Badge, Card, ProjectionChart, SourceChip, TierPill, formatDays } from "../components/ui";

export function RiskBoard({ go }: { go: (view: string) => void }) {
  const { state } = useStore();
  const [selected, setSelected] = useState<{ drugId: string; siteId: string } | null>(null);
  const projected = withApprovedPlans(state.stock, state.plans, state.events);

  const cells = state.stock.map((l) => ({ line: l, days: lineCoverage(l) }));
  const red = cells.filter((c) => tierFor(c.days, c.line.safetyDays) === "red").length;
  const amber = cells.filter((c) => tierFor(c.days, c.line.safetyDays) === "amber").length;
  const pending = state.plans.filter((p) => p.status === "pending-approval").length;
  const openEvents = state.events.filter((e) => e.status === "open").length;

  const sel = selected && state.stock.find((l) => l.drugId === selected.drugId && l.siteId === selected.siteId);
  const selProjected = selected && projected.find((l) => l.drugId === selected.drugId && l.siteId === selected.siteId);

  const broken = state.plans.filter((p) => p.status === "broken");

  return (
    <div className="view">
      {broken.map((p) => (
        <div key={p.id} className="callout bad row-between">
          <span>
            <strong>Approved plan {p.id} no longer holds.</strong> {p.title} would push {siteName(p.transfers[0]?.from ?? "")} below its safety floor after today's usage
            change. It was stopped before shipment.
          </span>
          <button className="btn" onClick={() => go("plans")}>
            Review plans
          </button>
        </div>
      ))}
      <div className="kpis">
        <Kpi label="Drug-site pairs at red" value={red} tone={red ? "bad" : "good"} />
        <Kpi label="At amber" value={amber} tone={amber ? "warn" : "good"} />
        <Kpi label="Open supply events" value={openEvents} tone={openEvents ? "warn" : "good"} onClick={() => go("events")} />
        <Kpi label="Plans awaiting approval" value={pending} tone={pending ? "info" : "neutral"} onClick={() => go("approvals")} />
      </div>

      <div className="split">
        <Card
          title="Days of supply by site"
          actions={
            <span className="muted small">
              Stock on hand ÷ daily usage, including open orders on their current ETA. <Badge tone="synthetic">synthetic inventory</Badge>
            </span>
          }
        >
          <table className="matrix">
            <thead>
              <tr>
                <th>Drug</th>
                {SITES.map((s) => (
                  <th key={s.id}>{s.name}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {DRUGS.map((d) => (
                <tr key={d.id}>
                  <td>
                    <div className="drug-name">{d.name}</div>
                    <div className="muted small">{d.form}</div>
                  </td>
                  {SITES.map((s) => {
                    const line = state.stock.find((l) => l.drugId === d.id && l.siteId === s.id)!;
                    const p = projected.find((l) => l.drugId === d.id && l.siteId === s.id)!;
                    const days = lineCoverage(line);
                    const after = lineCoverage(p);
                    const changed = Math.abs(after - days) > 0.05;
                    const isSel = selected?.drugId === d.id && selected.siteId === s.id;
                    return (
                      <td key={s.id}>
                        <button className={`cell cell-${tierFor(days, line.safetyDays)} ${isSel ? "selected" : ""}`} onClick={() => setSelected({ drugId: d.id, siteId: s.id })}>
                          <strong>{formatDays(days)}</strong>
                          <span>days</span>
                          {changed && <em className={`after after-${tierFor(after, line.safetyDays)}`}>→ {formatDays(after)} with approved plan</em>}
                        </button>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
          <div className="legend-row muted small">
            <TierPill tier="red">under 7 days</TierPill>
            <TierPill tier="amber">under 14 days</TierPill>
            <TierPill tier="green">14+ days</TierPill>
          </div>
        </Card>

        <div className="stack">
          {sel && selProjected ? (
            <CellDetail line={sel} projected={selProjected} />
          ) : (
            <Card title="Select a cell">
              <p className="muted">Click any drug and site to see its projection, open orders and the records the number comes from.</p>
            </Card>
          )}
          <Card title="Supply events" actions={<button className="btn ghost" onClick={() => go("events")}>Open inbox</button>}>
            <ul className="event-list">
              {state.events.map((e) => (
                <li key={e.id}>
                  <div className="row-between">
                    <strong>{e.title}</strong>
                    <Badge tone={e.status === "open" ? "bad" : e.status === "monitoring" ? "warn" : "good"}>{e.status}</Badge>
                  </div>
                  <div className="muted small">
                    {timeLabel(e.atMin)} · {e.source.replace(/-/g, " ")} · <SourceChip id={e.sourceRef} />
                  </div>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </div>
  );
}

function Kpi({ label, value, tone, onClick }: { label: string; value: number; tone: string; onClick?: () => void }) {
  return (
    <button className={`kpi kpi-${tone}`} onClick={onClick} disabled={!onClick}>
      <span className="kpi-value">{value}</span>
      <span className="kpi-label">{label}</span>
    </button>
  );
}

function CellDetail({ line, projected }: { line: StockLine; projected: StockLine }) {
  const drug = drugById(line.drugId);
  const days = lineCoverage(line);
  const after = lineCoverage(projected);
  const floor = line.dailyUsage * line.safetyDays;
  const series = [{ label: "Current", points: projection(line.onHand, line.dailyUsage, line.orders), color: "#c2410c" }];
  if (Math.abs(after - days) > 0.05) {
    series.push({ label: "With approved plan", points: projection(projected.onHand, projected.dailyUsage, projected.orders), color: "#0f766e" });
  }
  return (
    <Card title={`${drug.name} at ${siteName(line.siteId)}`} actions={<TierPill tier={tierFor(days, line.safetyDays)}>{formatDays(days)} days</TierPill>}>
      <ProjectionChart series={series} floor={floor} />
      <dl className="kv">
        <dt>On hand</dt>
        <dd>
          {line.onHand} {drug.unit} <SourceChip id={line.inventoryRef} />
        </dd>
        <dt>Daily usage</dt>
        <dd>
          {line.dailyUsage}/day <SourceChip id={line.siteId === "A" && line.drugId === "vaso" && line.dailyUsage === 10 ? "SITE-RPT-A-77" : line.usageRef} />
        </dd>
        <dt>Safety floor</dt>
        <dd>
          {line.safetyDays} days ({floor} {drug.unit})
        </dd>
        <dt>Open orders</dt>
        <dd>
          {line.orders.length === 0 && <span className="muted">None</span>}
          {line.orders.map((o) => (
            <div key={o.id}>
              {o.qty} from {o.supplier}, ETA {dayLabel(o.etaDay)}{" "}
              {o.status === "delayed" && <Badge tone="bad">delayed from {dayLabel(o.originalEtaDay ?? o.etaDay)}</Badge>} <SourceChip id={o.id} />
            </div>
          ))}
        </dd>
        <dt>Drug reference</dt>
        <dd>
          Health Canada DPD <SourceChip id={drug.dpdRef} /> {drug.criticalList && <SourceChip id="CDL-2026-V3" />}
        </dd>
      </dl>
    </Card>
  );
}
