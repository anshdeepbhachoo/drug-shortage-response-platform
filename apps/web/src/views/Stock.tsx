import { useState } from "react";
import { DRUGS, dayLabel, drugById, siteName } from "../data/mock";
import { lineCoverage, projection, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { StockLine, SupplyEvent, Tier } from "../lib/types";
import { Heatmap } from "../components/Heatmap";
import { Card, ChartGuide, Change, Empty, PageHead, ProjectionChart, Sources, Status, TierStatus, formatDays } from "../components/ui";

const AXIS_MAX = 60;
const runOutLabel = (days: number) => (days >= AXIS_MAX ? "not within 60 days" : dayLabel(Math.floor(days)));

type Key = { drugId: string; siteId: string };

export function Stock({ go }: { go: (view: string, focusEvent?: string) => void }) {
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
  // One decision per plan: count plans, not their options (same as the Approvals badge).
  const pending = new Set(state.plans.filter((p) => p.status === "pending-approval").map((p) => (state.live ? p.solverRun : p.id))).size;
  // Live: an alert stays on the board until it is closed, including after its plan is approved.
  const openEvents = state.events.filter((e) => e.status === "open" || (state.live && e.status === "mitigated")).length;
  const broken = state.plans.filter((p) => p.status === "broken");

  const [selected, setSelected] = useState<Key | null>(null);
  const [tab, setTab] = useState<"attention" | "all">("attention");

  if (cells.length === 0) {
    return (
      <>
        <PageHead label="Stock" title="How long will each drug last?" lead="No hospital stock has arrived yet." />
        <Empty title={`No data for ${dayLabel(0)} yet`}>Run this day at the top: it loads the hospitals' stock, usage and orders first.</Empty>
      </>
    );
  }

  // Default to the most urgent pair, else the first one (live data has no "vaso" / "B").
  const first = risky[0] ?? cells[0];
  const sel = selected ?? { drugId: first.line.drugId, siteId: first.line.siteId };
  const selCell = cells.find((c) => c.line.drugId === sel.drugId && c.line.siteId === sel.siteId) ?? first;
  const selProjected = projected.find((l) => l.drugId === selCell.line.drugId && l.siteId === selCell.line.siteId) ?? selCell.line;

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
        <Stat value={openEvents} label={state.live ? "Alerts being handled" : "Open alerts"} tone={openEvents ? "warn" : "good"} onClick={() => go("events")} />
        <Stat value={pending} label="Waiting for approval" tone={pending ? "warn" : "neutral"} onClick={() => go("approvals")} />
      </div>

      <div className="view-tabs" role="tablist" aria-label="Stock views">
        <button role="tab" aria-selected={tab === "attention"} className={tab === "attention" ? "active" : ""} onClick={() => setTab("attention")}>
          Needs attention <span className="num muted">{new Set(risky.map((c) => c.line.drugId)).size} drugs</span>
        </button>
        <button role="tab" aria-selected={tab === "all"} className={tab === "all" ? "active" : ""} onClick={() => setTab("all")}>
          All stock <span className="num muted">{DRUGS.length} drugs</span>
        </button>
      </div>

      <div className="grid-2">
        {tab === "attention" ? (
          <Attention risky={risky} selected={sel} pick={pick} />
        ) : (
        <Card label="Days of stock left, per hospital" title="Every drug at every hospital, most urgent first">
          <Heatmap
            lines={state.stock}
            after={new Map(cells.filter((c) => c.changed).map((c) => [`${c.line.drugId}|${c.line.siteId}`, c.after]))}
            selected={sel}
            onPick={(drugId, siteId) => pick({ drugId, siteId })}
          />
        </Card>
        )}

        <div id="detail">
          <CellDetail line={selCell.line} projected={selProjected} openPlan={(eventId) => go("plans", eventId)} />
        </div>
      </div>

    </>
  );
}

type Cell = { line: StockLine; days: number; after: number; tier: Tier; changed: boolean };

/** One row per drug in trouble, worst hospital first; open a row to see each hospital. */
function Attention({ risky, selected, pick }: { risky: Cell[]; selected: Key; pick: (k: Key) => void }) {
  const { state } = useStore();
  const [open, setOpen] = useState<string | null>(null);
  const byDrug = new Map<string, Cell[]>();
  for (const c of risky) byDrug.set(c.line.drugId, [...(byDrug.get(c.line.drugId) ?? []), c]);
  const drugs = [...byDrug.entries()];  // risky is sorted soonest first, so drugs are too
  if (drugs.length === 0) return <Empty title="Nothing needs attention">Every drug has at least 14 days at every hospital.</Empty>;
  return (
    <div className="attention">
      {drugs.map(([drugId, hospitals]) => {
        const drug = drugById(drugId);
        const worst = hospitals[0];
        const isOpen = open === drugId;
        return (
          <div key={drugId} className={`attention-drug ${isOpen ? "open" : ""}`}>
            <button
              className="attention-item"
              aria-expanded={isOpen}
              onClick={() => {
                setOpen(isOpen ? null : drugId);
                pick({ drugId, siteId: worst.line.siteId });
              }}
            >
              <span className="big">{formatDays(worst.days)}</span>
              <span>
                <strong>{drug.name}</strong>
                <div className="small ink-2">
                  {hospitals.length} hospital{hospitals.length > 1 ? "s" : ""} at risk · {siteName(worst.line.siteId)} runs out {runOutLabel(worst.days)}.
                  {state.live && <ProblemNote drugId={drugId} siteId={worst.line.siteId} />}
                </div>
              </span>
              <span className="row" style={{ flexWrap: "nowrap" }}>
                <TierStatus tier={worst.tier} />
                <span className="muted" aria-hidden>{isOpen ? "▴" : "▾"}</span>
              </span>
            </button>
            {isOpen && (
              <ul className="attention-sites">
                {hospitals.map((c) => (
                  <li key={c.line.siteId}>
                    <button
                      className={selected.drugId === drugId && selected.siteId === c.line.siteId ? "selected" : ""}
                      onClick={() => pick({ drugId, siteId: c.line.siteId })}
                    >
                      <span className="num"><b>{formatDays(c.days)}</b> d</span>
                      <span>
                        {siteName(c.line.siteId)} · runs out {runOutLabel(c.days)} at {c.line.dailyUsage} {drug.unit} a day
                        {c.changed && (
                          <>
                            {" "}· with the approved plan <Change was={`${formatDays(c.days)}`} now={`${formatDays(c.after)} days`} />
                          </>
                        )}
                      </span>
                      <TierStatus tier={c.tier} />
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        );
      })}
    </div>
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

function CellDetail({ line, projected, openPlan }: { line: StockLine; projected: StockLine; openPlan: (eventId: string) => void }) {
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
          { mark: "danger", text: <>The <b>pink band</b> under the floor is the danger zone. Any part of the line inside it means less than {line.safetyDays} days of stock.</> },
          { mark: "dot", text: <>The dot on the bottom marks the day the hospital would <b>run out completely</b>.</> },
          { mark: "gap", text: <>A <b>thick red stretch</b> along the bottom is the days with <b>no stock at all</b>, until the next delivery.</> },
          ...(changed ? [{ mark: "line-1" as const, text: <>The <b>green line</b> shows the same thing if the approved plan goes ahead. Compare the two lines to see what the plan buys you.</> }] : []),
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
            Move your mouse or finger along the chart, or focus it and use the arrow keys, to read any day.
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
      <PlanAction line={line} openPlan={openPlan} />
    </Card>
  );
}

/** Live data: the supply manager can ask for a plan for this drug now, without waiting for an alert. */
function PlanAction({ line, openPlan }: { line: StockLine; openPlan: (eventId: string) => void }) {
  const { state, live } = useStore();
  if (!state.live) return null;
  const open = drugAlert(state.events, line.drugId);
  const canPlan = state.role === "supply";
  const plan = async () => {
    const eventId = await live.planDrug(line.drugId, line.siteId);
    if (eventId) openPlan(eventId);
  };
  return (
    <div className="row-between" style={{ marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--line)" }}>
      <span className="small ink-2">
        {open
          ? open.status === "mitigated"
            ? "This drug's alert is being handled: its plan was approved."
            : "This drug already has an open alert and plan."
          : canPlan
            ? "Don't wait for an email: plan a response for this drug now."
            : "Only the supply manager can raise a plan from here."}
      </span>
      {open ? (
        <button className="btn" onClick={() => openPlan(open.id)}>
          See its plan →
        </button>
      ) : (
        <button className="btn primary" onClick={() => void plan()} disabled={!canPlan || live.busy !== null}>
          {live.busy === "Planning a response" ? "Planning…" : "Plan a response"}
        </button>
      )}
    </div>
  );
}

/** Live: does this drug at this hospital already have a problem being handled? */
function ProblemNote({ drugId, siteId }: { drugId: string; siteId: string }) {
  const { state } = useStore();
  const problem = drugAlert(state.events, drugId);
  if (!problem) return <b> No problem raised yet.</b>;
  const sites = problem.sites ?? [];
  if (sites.length && !sites.includes(siteId)) return <b> This drug has an alert, but its plan does not cover this hospital yet.</b>;
  return <b> Problem being handled: {(problem.planState ?? "plan in progress").toLowerCase()}.</b>;
}

/** Live: the drug's one current alert (open, or approved and not yet closed). Open wins. */
function drugAlert(events: SupplyEvent[], drugId: string): SupplyEvent | undefined {
  const mine = events.filter((e) => e.drugId === drugId);
  return mine.find((e) => e.status === "open") ?? mine.find((e) => e.status === "mitigated");
}
