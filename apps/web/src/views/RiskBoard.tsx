import { useState } from "react";
import { DRUGS, SITES, dayLabel, drugById, siteName } from "../data/mock";
import { AMBER_BELOW, RED_BELOW, lineCoverage, projection, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { StockLine, Tier } from "../lib/types";
import { Card, ChartGuide, Change, PageHead, ProjectionChart, Sources, Status, TIER_TONE, TierStatus, formatDays } from "../components/ui";

const AXIS_MAX = 60;
const pct = (days: number) => `${(Math.min(days, AXIS_MAX) / AXIS_MAX) * 100}%`;
const runOutLabel = (days: number) => (days >= AXIS_MAX ? "not within 60 days" : dayLabel(Math.floor(days)));

type Key = { drugId: string; siteId: string };

export function RiskBoard({ go }: { go: (view: string) => void }) {
  const { state } = useStore();
  const projected = withApprovedPlans(state.stock, state.plans, state.events);

  const cells = state.stock.map((line) => {
    const days = lineCoverage(line);
    const p = projected.find((l) => l.drugId === line.drugId && l.siteId === line.siteId)!;
    const after = lineCoverage(p);
    return { line, days, after, tier: tierFor(days, line.safetyDays) as Tier, changed: Math.abs(after - days) > 0.05 };
  });
  const risky = cells.filter((c) => c.tier !== "green").sort((a, b) => a.days - b.days);
  const red = cells.filter((c) => c.tier === "red").length;
  const amber = cells.filter((c) => c.tier === "amber").length;
  const pending = state.plans.filter((p) => p.status === "pending-approval").length;
  const openEvents = state.events.filter((e) => e.status === "open").length;
  const broken = state.plans.filter((p) => p.status === "broken");

  const [selected, setSelected] = useState<Key | null>(null);
  const sel = selected ?? (risky[0] ? { drugId: risky[0].line.drugId, siteId: risky[0].line.siteId } : { drugId: "vaso", siteId: "B" });
  const selCell = cells.find((c) => c.line.drugId === sel.drugId && c.line.siteId === sel.siteId)!;
  const selProjected = projected.find((l) => l.drugId === sel.drugId && l.siteId === sel.siteId)!;

  // Drugs with the lowest cover first, so problems are at the top.
  const drugsByRisk = [...DRUGS].sort((a, b) => {
    const min = (id: string) => Math.min(...cells.filter((c) => c.line.drugId === id).map((c) => c.days));
    return min(a.id) - min(b.id);
  });

  const pick = (k: Key) => {
    setSelected(k);
    document.getElementById("detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };

  const summary =
    risky.length === 0
      ? `All ${cells.length} drug and hospital pairs have at least 14 days of stock.`
      : `${risky.length} of ${cells.length} drug and hospital pairs need attention. The most urgent is ${drugById(risky[0].line.drugId).name} at ${siteName(risky[0].line.siteId)}, which runs out ${runOutLabel(risky[0].days)}.`;

  return (
    <>
      <PageHead label="Stock" title="How long will each drug last?" lead={summary} />

      {broken.map((p) => (
        <div key={p.id} className="callout crit">
          <span className="ico" aria-hidden>
            ✕
          </span>
          <div style={{ flex: 1 }}>
            <strong>An approved transfer was stopped before it shipped.</strong>
            <span className="ink-2">
              {p.title} would now leave {siteName(p.transfers[0]?.from ?? "")} under its safety floor, because its usage went up today. A new plan is needed.
            </span>
          </div>
          <button className="btn" onClick={() => go("plans")}>
            See new options
          </button>
        </div>
      ))}

      <div className="stats">
        <Stat value={red} label="Critical: under 7 days" tone={red ? "crit" : "good"} />
        <Stat value={amber} label="Watch: under 14 days" tone={amber ? "warn" : "good"} />
        <Stat value={openEvents} label="Open alerts" tone={openEvents ? "warn" : "good"} onClick={() => go("events")} />
        <Stat value={pending} label="Waiting for approval" tone={pending ? "warn" : "neutral"} onClick={() => go("approvals")} />
      </div>

      {risky.length > 0 && (
        <section>
          <div className="section-head">
            <h2>Needs attention</h2>
            <span className="meta">sorted by soonest to run out</span>
          </div>
          <div className="attention">
            {risky.map((c) => {
              const drug = drugById(c.line.drugId);
              return (
                <button key={`${c.line.drugId}-${c.line.siteId}`} className="attention-item" onClick={() => pick({ drugId: c.line.drugId, siteId: c.line.siteId })}>
                  <span className="big">{formatDays(c.days)}</span>
                  <span>
                    <strong>
                      {drug.name} at {siteName(c.line.siteId)}
                    </strong>
                    <div className="small ink-2">
                      Runs out {runOutLabel(c.days)} at {c.line.dailyUsage} {drug.unit} a day.
                      {c.changed && (
                        <>
                          {" "}
                          With the approved plan: <Change was={`${formatDays(c.days)} days`} now={`${formatDays(c.after)} days`} />
                        </>
                      )}
                    </div>
                  </span>
                  <TierStatus tier={c.tier} />
                </button>
              );
            })}
          </div>
        </section>
      )}

      <div className="grid-2">
        <Card
          label="Days of stock left, per hospital"
          title="All drugs, lowest first"
          footer={
            <div className="legend">
              <span>
                <i style={{ background: "var(--crit)" }} />
                Under {RED_BELOW} days
              </span>
              <span>
                <i style={{ background: "var(--warn)" }} />
                Under {AMBER_BELOW} days
              </span>
              <span>
                <i style={{ background: "var(--good)" }} />
                {AMBER_BELOW}+ days
              </span>
              <span>
                <i style={{ background: "none", borderLeft: "2px dashed var(--crit)", width: 2, borderRadius: 0 }} />
                14-day safety floor
              </span>
              <span>
                <i style={{ background: "var(--ink)", width: 3 }} />
                After approved plan
              </span>
            </div>
          }
        >
          <StripGuide cells={cells} />
          <table className="strip">
            <thead>
              <tr className="axis-row">
                <th />
                <th>
                  <div className="bar-row" style={{ cursor: "default" }}>
                    <span />
                    <div className="bar-track">
                      {[0, 7, 14, 30, 60].map((t) => (
                        <span key={t} className="tick" style={{ left: pct(t) }}>
                          {t === 60 ? "60+ d" : t}
                        </span>
                      ))}
                    </div>
                    <span />
                  </div>
                </th>
              </tr>
            </thead>
            <tbody>
              {drugsByRisk.map((d) => (
                <tr key={d.id} className="drug-row">
                  <td className="drug">
                    <strong>{d.name}</strong>
                    <span>{d.form}</span>
                  </td>
                  <td>
                    <div className="bars">
                      {SITES.map((s) => {
                        const c = cells.find((x) => x.line.drugId === d.id && x.line.siteId === s.id)!;
                        const isSel = sel.drugId === d.id && sel.siteId === s.id;
                        return (
                          <button
                            key={s.id}
                            className={`bar-row ${isSel ? "selected" : ""}`}
                            onClick={() => pick({ drugId: d.id, siteId: s.id })}
                            title={`${d.name} at ${s.short}: ${formatDays(c.days)} days${c.changed ? `, ${formatDays(c.after)} after the approved plan` : ""}`}
                            aria-label={`${d.name} at ${s.short}: ${formatDays(c.days)} days, ${c.tier === "green" ? "OK" : c.tier === "amber" ? "watch" : "critical"}`}
                          >
                            <span className="site">{s.short}</span>
                            <div className="bar-track">
                              <span className="bar-zone red" style={{ left: 0, width: pct(RED_BELOW) }} />
                              <span className="bar-zone amber" style={{ left: pct(RED_BELOW), width: `calc(${pct(AMBER_BELOW)} - ${pct(RED_BELOW)})` }} />
                              <span className={`bar-fill ${TIER_TONE[c.tier]}`} style={{ width: pct(c.days) }} />
                              <span className="bar-floor" style={{ left: pct(AMBER_BELOW) }} />
                              {c.changed && <span className="bar-after" style={{ left: pct(c.after) }} />}
                            </div>
                            <span className="bar-val">
                              {c.tier !== "green" && <span aria-hidden>{c.tier === "red" ? "✕" : "!"}</span>}
                              <b>{formatDays(c.days)}</b>d
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <div id="detail">
          <CellDetail line={selCell.line} projected={selProjected} />
        </div>
      </div>

    </>
  );
}

function Stat({ value, label, tone, onClick }: { value: number; label: string; tone: "good" | "warn" | "crit" | "neutral"; onClick?: () => void }) {
  return (
    <button className="stat" onClick={onClick} disabled={!onClick}>
      <span className="label">{label}</span>
      <span className="stat-value">
        {value}
        {tone !== "neutral" && value > 0 && <Status tone={tone}>{""}</Status>}
      </span>
      <span className="small muted">{onClick ? "Open →" : "\u00a0"}</span>
    </button>
  );
}

function CellDetail({ line, projected }: { line: StockLine; projected: StockLine }) {
  const drug = drugById(line.drugId);
  const days = lineCoverage(line);
  const after = lineCoverage(projected);
  const tier = tierFor(days, line.safetyDays);
  const floor = line.dailyUsage * line.safetyDays;
  const changed = Math.abs(after - days) > 0.05;
  const series = [{ label: "As things stand", points: projection(line.onHand, line.dailyUsage, line.orders), color: "var(--series-2)" }];
  if (changed) {
    series.push({ label: "With approved plan", points: projection(projected.onHand, projected.dailyUsage, projected.orders), color: "var(--series-1)" });
  }
  const marks = line.orders.filter((o) => o.etaDay <= 30).map((o) => ({ day: o.etaDay, label: `${o.qty} arrive` }));
  const delayed = line.orders.find((o) => o.status === "delayed");
  const usageRef = line.siteId === "A" && line.drugId === "vaso" && line.dailyUsage === 10 ? "SITE-RPT-A-77" : line.usageRef;

  return (
    <Card label="Selected" title={`${drug.name} at ${siteName(line.siteId)}`} actions={<TierStatus tier={tier}>{formatDays(days)} days</TierStatus>}>
      <p className="ink-2" style={{ marginBottom: 14 }}>
        {siteName(line.siteId)} has <b>{line.onHand} {drug.unit}</b> and uses <b>{line.dailyUsage} a day</b>. At this rate it runs out <b>{runOutLabel(days)}</b>.
        {delayed && (
          <>
            {" "}
            Its order of {delayed.qty} was due {dayLabel(delayed.originalEtaDay ?? delayed.etaDay)} but is now late until {dayLabel(delayed.etaDay)}.
          </>
        )}
        {changed && (
          <>
            {" "}
            The approved plan takes it to <b>{formatDays(after)} days</b>.
          </>
        )}
      </p>
      <ChartGuide
        items={[
          { mark: "line-2", text: <>The line is <b>how many {drug.unit} are left</b> on each day, for the next 30 days. It slopes down as stock is used.</> },
          ...(marks.length ? [{ mark: "drop" as const, text: <>Where it <b>jumps up</b>, a delivery arrives (dotted line at the top says how many).</> }] : []),
          { mark: "floor", text: <>The <b>dashed red line</b> is the safety floor: {line.safetyDays} days of stock ({floor} {drug.unit}). Dropping below it is the warning sign.</> },
          { mark: "dot", text: <>The dot on the bottom marks the day the hospital would <b>run out completely</b>.</> },
          ...(changed ? [{ mark: "line-1" as const, text: <>The <b>blue line</b> shows the same thing if the approved plan goes ahead. Compare the two lines to see what the plan buys you.</> }] : []),
        ]}
        example={
          <>
            Here, stock runs out <b>{runOutLabel(days)}</b>
            {line.orders[0] && line.orders[0].etaDay > days ? (
              <>
                {" "}but the next delivery only arrives <b>{dayLabel(line.orders[0].etaDay)}</b>: the flat stretch at zero in between is the shortage.
              </>
            ) : (
              "."
            )}{" "}
            Move your mouse along the chart to see the amount left on any day.
          </>
        }
      />
      <ProjectionChart series={series} floor={floor} unit={drug.unit} marks={marks} />
      <dl className="facts" style={{ marginTop: 16 }}>
        <dt>On hand</dt>
        <dd>
          {line.onHand} {drug.unit}
        </dd>
        <dt>Used per day</dt>
        <dd>{line.dailyUsage}</dd>
        <dt>Safety floor</dt>
        <dd>
          {line.safetyDays} days ({floor} {drug.unit})
        </dd>
        <dt>Orders</dt>
        <dd>
          {line.orders.length === 0 && <span className="muted">None open</span>}
          {line.orders.map((o) => (
            <span key={o.id}>
              {o.qty} from {o.supplier},{" "}
              {o.status === "delayed" ? <Change was={dayLabel(o.originalEtaDay ?? o.etaDay)} now={dayLabel(o.etaDay)} /> : `due ${dayLabel(o.etaDay)}`}
            </span>
          ))}
        </dd>
      </dl>
      <div style={{ marginTop: 14 }}>
        <Sources ids={[line.inventoryRef, usageRef, ...line.orders.map((o) => o.id), drug.dpdRef, ...(drug.criticalList ? ["CDL-2026-V3"] : [])]} />
      </div>
    </Card>
  );
}

function StripGuide({ cells }: { cells: { line: StockLine; days: number; tier: string }[] }) {
  const worst = [...cells].sort((a, b) => a.days - b.days)[0];
  return (
    <ChartGuide
      items={[
        { mark: "bar-good", text: <>Each <b>row</b> is one drug; each <b>bar</b> is one hospital. A longer bar means <b>more days</b> before that hospital runs out (stock on hand ÷ daily use, counting deliveries on their due day).</> },
        { mark: "bar-crit", text: <>Bar colour says how urgent: <b>red</b> under {RED_BELOW} days, <b>amber</b> under {AMBER_BELOW} days, <b>green</b> is fine.</> },
        { mark: "floor", text: <>The <b>dashed red line</b> is the {AMBER_BELOW}-day safety floor every hospital must stay above.</> },
        { mark: "after", text: <>A <b>thin black mark</b> shows where the bar will be once an approved plan is delivered.</> },
      ]}
      example={
        worst && (
          <>
            {drugById(worst.line.drugId).name} at {siteName(worst.line.siteId)} has the shortest bar: <b>{formatDays(worst.days)} days</b> of stock
            {worst.days < AMBER_BELOW ? ", which is left of the safety line, so it needs action." : ", which is still right of the safety line."} Click any bar to see its details on the right.
          </>
        )
      }
    />
  );
}
