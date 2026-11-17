import type { ReactNode } from "react";
import { LiveBar, latestReport } from "../components/LiveBar";
import { PageHead, Status, formatDays, type Tone } from "../components/ui";
import { drugById, siteName } from "../data/mock";
import { lineCoverage, tierFor } from "../lib/risk";
import { useStore } from "../lib/store";
import { STATUS_WORDS, money, needsPerson, useOrders } from "../lib/orders";
import { arrivalOf } from "../components/OrderCharts";

type Go = (view: "hospitals" | "board" | "events" | "approvals" | "orders" | "audit") => void;

/** Live home: a dashboard of tiles, each a summary that opens the page behind it. */
export function Home({ go }: { go: Go }) {
  return (
    <>
      <PageHead label="Home" title="Today across the network" lead="Each tile sums up one part of the platform. Click a tile to open it." />
      <LiveBar />
      <div className="tiles">
        <HospitalsTile go={go} />
        <StockTile go={go} />
        <ProblemsTile go={go} />
        <ApprovalsTile go={go} />
        <OrdersTile go={go} />
        <FollowUpTile go={go} />
        <InboxTile go={go} />
      </div>
    </>
  );
}

function Tile({ title, value, tone, sub, onOpen, children }: {
  title: string;
  value: ReactNode;
  tone?: Tone;
  sub: string;
  onOpen: () => void;
  children: ReactNode;
}) {
  return (
    <button className="tile" onClick={onOpen}>
      <span className="tile-head">
        <span className="tile-title">{title}</span>
        <span className="tile-open">Open →</span>
      </span>
      <span className="tile-value">
        {value}
        {tone && tone !== "neutral" && <Status tone={tone}>{""}</Status>}
      </span>
      <span className="small muted">{sub}</span>
      <span className="tile-body">{children}</span>
    </button>
  );
}

function Rows({ rows, empty }: { rows: { key: string; text: ReactNode; right?: ReactNode }[]; empty: string }) {
  if (rows.length === 0) return <span className="small muted">{empty}</span>;
  return (
    <ul className="tile-rows">
      {rows.map((r) => (
        <li key={r.key}>
          <span>{r.text}</span>
          {r.right && <span className="tile-right">{r.right}</span>}
        </li>
      ))}
    </ul>
  );
}

function HospitalsTile({ go }: { go: Go }) {
  const { state } = useStore();
  const sites = [...new Set(state.stock.map((l) => l.siteId))].map((siteId) => {
    const lines = state.stock.filter((l) => l.siteId === siteId).map((l) => ({ line: l, days: lineCoverage(l) }));
    const worst = lines.reduce((a, b) => (b.days < a.days ? b : a), lines[0]);
    return { siteId, worst, tier: tierFor(worst.days, worst.line.safetyDays) };
  }).sort((a, b) => a.worst.days - b.worst.days);
  const red = sites.filter((s) => s.tier === "red").length;
  const amber = sites.filter((s) => s.tier === "amber").length;
  return (
    <Tile title="Hospitals" value={red} tone={red ? "crit" : "good"} sub={`with a drug under 7 days · ${amber} more under 14`} onOpen={() => go("hospitals")}>
      <Rows
        empty="No hospital stock yet."
        rows={sites.slice(0, 3).map((s) => ({
          key: s.siteId,
          text: <b>{siteName(s.siteId)}</b>,
          right: `${drugById(s.worst.line.drugId)?.name.split(" ")[0]} ${formatDays(s.worst.days)} d`,
        }))}
      />
    </Tile>
  );
}

function StockTile({ go }: { go: Go }) {
  const { state } = useStore();
  const lines = state.stock.map((l) => ({ line: l, days: lineCoverage(l) }));
  const red = lines.filter((l) => tierFor(l.days, l.line.safetyDays) === "red").length;
  const amber = lines.filter((l) => tierFor(l.days, l.line.safetyDays) === "amber").length;
  // Worst hospital per drug, most urgent drugs first.
  const worst = new Map<string, (typeof lines)[number]>();
  for (const l of lines) {
    const seen = worst.get(l.line.drugId);
    if (!seen || l.days < seen.days) worst.set(l.line.drugId, l);
  }
  const top = [...worst.values()].filter((l) => tierFor(l.days, l.line.safetyDays) !== "green").sort((a, b) => a.days - b.days).slice(0, 3);
  return (
    <Tile title="Stock at risk" value={red} tone={red ? "crit" : "good"} sub={`critical (under 7 days) · ${amber} more under 14 days`} onOpen={() => go("board")}>
      <Rows
        empty="Every drug has at least 14 days everywhere."
        rows={top.map((l) => ({
          key: l.line.drugId,
          text: (
            <>
              <b>{drugById(l.line.drugId)?.name ?? l.line.drugId}</b> at {siteName(l.line.siteId)}
            </>
          ),
          right: `${formatDays(l.days)} d`,
        }))}
      />
    </Tile>
  );
}

function ProblemsTile({ go }: { go: Go }) {
  const { state } = useStore();
  const open = state.events.filter((e) => e.status === "open");
  const handled = state.events.filter((e) => e.status === "mitigated").length;
  const critical = open.filter((e) => drugById(e.drugId)?.criticalList).length;
  return (
    <Tile title="Problems" value={open.length} tone={open.length ? "warn" : "good"} sub={`open · ${critical} on the critical list · ${handled} handled`} onOpen={() => go("events")}>
      <Rows
        empty="No open problems."
        rows={open.slice(0, 3).map((e) => ({
          key: e.id,
          text: <b>{drugById(e.drugId)?.name ?? e.drugId}</b>,
          right: e.planState ?? "",
        }))}
      />
    </Tile>
  );
}

function ApprovalsTile({ go }: { go: Go }) {
  const { state } = useStore();
  const pending = state.plans.filter((p) => p.status === "pending-approval");
  const plans = [...new Set(pending.map((p) => p.solverRun))].map((run) => pending.filter((p) => p.solverRun === run));
  return (
    <Tile title="Waiting for a decision" value={plans.length} tone={plans.length ? "warn" : "good"} sub="plans, one decision each" onOpen={() => go("approvals")}>
      <Rows
        empty="Nothing waiting."
        rows={plans.slice(0, 3).map((options) => ({
          key: options[0].solverRun,
          text: <b>{drugById(options[0].drugId)?.name ?? options[0].drugId}</b>,
          right: `${options.length} option${options.length > 1 ? "s" : ""}`,
        }))}
      />
    </Tile>
  );
}

const FOLLOWUP_TONE: Record<string, Tone> = { wait: "good", close: "good", nudge: "warn", escalate: "crit", replan: "crit" };

function FollowUpTile({ go }: { go: Go }) {
  const { state } = useStore();
  const all = state.live?.followups ?? [];
  const trouble = all.filter((f) => f.decision === "escalate" || f.decision === "replan" || f.decision === "nudge").length;
  return (
    <Tile
      title="Follow-up on approved transfers"
      value={all.length}
      sub={all.length ? `approved transfers · ${trouble} need attention` : "approved transfers"}
      tone={trouble ? "warn" : all.length ? "good" : "neutral"}
      onOpen={() => go("audit")}
    >
      <Rows
        empty="Nothing approved yet. Approved transfers are checked every evening."
        rows={all.slice(0, 4).map((f) => ({
          key: f.id,
          text: `${f.drug.split(" ")[0]}: ${f.from} → ${f.to}, ${f.units} units`,
          right: <Status tone={f.decision ? FOLLOWUP_TONE[f.decision] ?? "neutral" : "neutral"}>{f.status.split(":")[0]}</Status>,
        }))}
      />
    </Tile>
  );
}

function InboxTile({ go }: { go: Go }) {
  const { state, live } = useStore();
  const asOf = state.live?.asOf ?? "";
  const report = latestReport(state.live?.dayReports ?? {}, state.live?.daysRun ?? [], asOf, live.lastDay);
  const mail = report?.emails ?? [];
  const blocked = mail.filter((m) => m.status.startsWith("needs")).length;
  const waiting = mail.filter((m) => m.status.startsWith("waiting")).length;
  const arriving = state.live?.inbox ?? [];
  return (
    <Tile
      title="Supplier inbox"
      value={mail.length}
      tone={blocked || waiting ? "warn" : mail.length ? "good" : "neutral"}
      sub={report ? `emails on ${report.day.slice(5)} · ${blocked} held for a person · ${waiting} waiting` : "no day run yet"}
      onOpen={() => go("events")}
    >
      <Rows
        empty={arriving.length ? `Arriving next: ${arriving.join(", ")}` : "No emails yet."}
        rows={mail.slice(0, 3).map((m) => ({ key: m.email, text: m.email, right: m.status.replace(/_/g, " ") }))}
      />
    </Tile>
  );
}

function OrdersTile({ go }: { go: Go }) {
  const { data, backend } = useOrders();
  const orders = data.orders;
  const waiting = orders.filter((o) => o.status === "pending_approval");
  const open = orders.filter((o) => o.status !== "delivered" && o.status !== "cancelled" && o.status !== "pending_approval");
  const person = orders.filter(needsPerson).length;
  const late = orders.filter((o) => o.status !== "delivered" && o.status !== "cancelled" && arrivalOf(o, data.asOf).late);
  const urgency = (o: (typeof orders)[number]) => o.daysLeft ?? 999;
  const top = [...late.sort((a, b) => urgency(a) - urgency(b)), ...waiting.filter((o) => !late.includes(o)).sort((a, b) => urgency(a) - urgency(b))].slice(0, 3);
  const empty = backend !== "database" ? "Orders API not connected." : data.catalog === false ? "No supplier catalog loaded yet." : "No supplier orders yet. Run a day.";
  return (
    <Tile
      title="Supplier orders"
      value={waiting.length}
      tone={late.length || person ? "crit" : waiting.length ? "warn" : orders.length ? "good" : "neutral"}
      sub={`waiting for approval (${money(waiting.reduce((t, o) => t + o.totalCost, 0))}) · ${open.length} on the way · ${late.length} too late`}
      onOpen={() => go("orders")}
    >
      <Rows
        empty={empty}
        rows={top.map((o) => ({
          key: o.id,
          text: <><b>{o.drug.split(" ")[0]}</b> for {o.siteName}</>,
          right: arrivalOf(o, data.asOf).late ? <Status tone="crit">too late</Status> : STATUS_WORDS[o.status].toLowerCase(),
        }))}
      />
    </Tile>
  );
}
