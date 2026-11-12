import { useState } from "react";
import { Badge, Card, SourceChip, TierPill, formatDays } from "../components/ui";
import { DRUGS, SITES, dayLabel, drugById, siteName, timeLabel } from "../data/mock";
import { lineCoverage, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Actor } from "../lib/types";

const ACTOR_TONE: Record<Actor, "neutral" | "info" | "warn" | "good" | "synthetic"> = {
  system: "neutral",
  agent: "synthetic",
  solver: "info",
  supply: "warn",
  pharmacist: "good",
  procurement: "warn",
  auditor: "neutral",
};

export function Audit() {
  const { state } = useStore();
  const [filter, setFilter] = useState<Actor | "all">("all");
  const rows = state.audit.filter((a) => filter === "all" || a.actor === filter);

  return (
    <div className="view">
      <div className="split wide-left">
        <Card
          title="Audit log"
          actions={
            <span className="row gap">
              <Badge tone="neutral">append-only</Badge>
              <select className="input" value={filter} onChange={(e) => setFilter(e.target.value as Actor | "all")}>
                <option value="all">All actors</option>
                <option value="pharmacist">Pharmacist decisions</option>
                <option value="supply">Supply manager</option>
                <option value="procurement">Procurement</option>
                <option value="agent">AI agents</option>
                <option value="solver">Solver</option>
                <option value="system">System</option>
              </select>
            </span>
          }
        >
          <table className="table audit">
            <thead>
              <tr>
                <th>ID</th>
                <th>When</th>
                <th>Actor</th>
                <th>Action</th>
                <th>Detail</th>
                <th>Sources</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.id}>
                  <td className="mono muted">{a.id}</td>
                  <td className="nowrap">{timeLabel(a.atMin)}</td>
                  <td>
                    <Badge tone={ACTOR_TONE[a.actor]}>{a.actor}</Badge>
                    <div className="muted small">{a.actorName}</div>
                  </td>
                  <td>
                    <strong>{a.action}</strong>
                    <div className="mono small">{a.object}</div>
                  </td>
                  <td>{a.detail}</td>
                  <td>
                    <div className="chips">
                      {a.refs.map((r) => (
                        <SourceChip key={r} id={r} />
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Trace />
      </div>
    </div>
  );
}

function Trace() {
  const { state } = useStore();
  const [drugId, setDrugId] = useState("vaso");
  const [siteId, setSiteId] = useState("B");
  const line = state.stock.find((l) => l.drugId === drugId && l.siteId === siteId)!;
  const drug = drugById(drugId);
  const days = lineCoverage(line);
  const usageRef = drugId === "vaso" && siteId === "A" && line.dailyUsage === 10 ? "SITE-RPT-A-77" : line.usageRef;
  const delayEvent = state.events.find((e) => e.drugId === drugId && e.siteId === siteId && e.kind === "delay");
  const approved = state.plans.find((p) => p.status === "approved" && p.drugId === drugId);
  const projectedLine = withApprovedPlans(state.stock, state.plans, state.events).find((l) => l.drugId === drugId && l.siteId === siteId)!;
  const projectedDays = lineCoverage(projectedLine);

  return (
    <Card title="Trace a number to its source">
      <div className="row gap">
        <select className="input" value={drugId} onChange={(e) => setDrugId(e.target.value)}>
          {DRUGS.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
          {SITES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="trace">
        <div className="trace-node root">
          <div className="row-between">
            <strong>
              {drug.name} at {siteName(siteId)}: {formatDays(days)} days of supply
            </strong>
            <TierPill tier={tierFor(days, line.safetyDays)} />
          </div>
          <div className="muted small">Computed by the risk module (code, not a model)</div>
          {approved && Math.abs(projectedDays - days) > 0.05 && (
            <div className="row gap small trace-after">
              Once approved plan <SourceChip id={approved.id} /> is carried out: <strong>{formatDays(projectedDays)} days</strong>
              <TierPill tier={tierFor(projectedDays, line.safetyDays)} />
            </div>
          )}
        </div>
        <div className="trace-children">
          <div className="trace-node">
            On hand: {line.onHand} {drug.unit} <SourceChip id={line.inventoryRef} />
          </div>
          <div className="trace-node">
            Daily usage: {line.dailyUsage}/day <SourceChip id={usageRef} />
          </div>
          {line.orders.map((o) => (
            <div key={o.id} className="trace-node">
              Open order {o.id}: {o.qty} {drug.unit}, ETA {dayLabel(o.etaDay)} <SourceChip id={o.id} />
              {o.status === "delayed" && delayEvent && (
                <div className="trace-children">
                  <div className="trace-node">
                    ETA moved from {dayLabel(o.originalEtaDay ?? o.etaDay)} by supply event <SourceChip id={delayEvent.id} />
                    <div className="trace-children">
                      <div className="trace-node">
                        Extracted from supplier email, confidence {Math.round(delayEvent.confidence * 100)}% <SourceChip id={delayEvent.sourceRef} />
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))}
          <div className="trace-node">
            Drug identity: Health Canada DPD <SourceChip id={drug.dpdRef} />
            {drug.criticalList && (
              <>
                {" "}
                and critical list <SourceChip id="CDL-2026-V3" />
              </>
            )}
          </div>
        </div>
      </div>
    </Card>
  );
}
