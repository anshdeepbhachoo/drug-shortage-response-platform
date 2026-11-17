import { useState, type ReactNode } from "react";
import { STATUS_WORDS, dateLabel, money, needsPerson, type OrderStatus, type PurchaseOrder } from "../lib/orders";
import { Status } from "./ui";

/* Charts for the Orders page. Plain HTML bars (they reflow on a phone), one hue for
 * magnitude, status colours only for "in time / too late", always with a word beside them. */

type Tip = { x: number; y: number; body: ReactNode } | null;

/** A hover tooltip positioned inside the chart's own box. */
function useTip() {
  const [tip, setTip] = useState<Tip>(null);
  const show = (e: React.MouseEvent | React.FocusEvent, body: ReactNode) => {
    const box = (e.currentTarget as HTMLElement).closest(".chart-wrap")?.getBoundingClientRect();
    const el = (e.currentTarget as HTMLElement).getBoundingClientRect();
    if (!box) return;
    const x = "clientX" in e ? e.clientX - box.left : el.left + el.width / 2 - box.left;
    setTip({ x: Math.min(Math.max(x, 90), box.width - 90), y: el.top - box.top, body });
  };
  const node = tip && (
    <div className="tooltip" style={{ left: tip.x, top: tip.y }} role="presentation">
      {tip.body}
    </div>
  );
  return { show, hide: () => setTip(null), node };
}

/* ---------------- 1. Where every order is: the pipeline ---------------- */

const STAGES: OrderStatus[] = ["pending_approval", "approved", "placed", "in_transit", "delayed", "delivered", "cancelled"];

export function PipelineChart({ orders, active, onPick }: { orders: PurchaseOrder[]; active: string; onPick: (status: OrderStatus) => void }) {
  const tip = useTip();
  const rows = STAGES.map((s) => {
    const list = orders.filter((o) => o.status === s);
    return { s, n: list.length, cost: list.reduce((t, o) => t + o.totalCost, 0), person: list.filter(needsPerson).length };
  });
  const max = Math.max(1, ...rows.map((r) => r.n));
  return (
    <div className="chart-wrap">
      <div className="hbars" role="list" aria-label="Orders by stage">
        {rows.map((r) => (
          <button
            key={r.s}
            role="listitem"
            className={`hbar-row ${active === r.s ? "selected" : ""} ${r.n === 0 ? "empty" : ""}`}
            onClick={() => onPick(r.s)}
            disabled={r.n === 0}
            aria-pressed={active === r.s}
            aria-label={`${STATUS_WORDS[r.s]}: ${r.n} orders, ${money(r.cost)}`}
            onMouseMove={(e) => tip.show(e, <><b>{STATUS_WORDS[r.s]}</b><br />{r.n} order{r.n === 1 ? "" : "s"} · {money(r.cost)}{r.person ? <><br />{r.person} passed to a person</> : null}</>)}
            onMouseLeave={tip.hide}
          >
            <span className="hbar-label">{STATUS_WORDS[r.s]}</span>
            <span className="hbar-track">
              {r.n > 0 && <span className={`hbar-fill ${r.s === "cancelled" ? "muted-fill" : ""}`} style={{ width: `${(r.n / max) * 100}%` }} />}
            </span>
            <span className="hbar-val">
              <b>{r.n}</b> <span className="muted">{r.n ? money(r.cost) : ""}</span>
            </span>
          </button>
        ))}
      </div>
      {tip.node}
    </div>
  );
}

/* ---------------- 2. Will it arrive before the hospital runs out? ---------------- */

function daysBetween(from: string, to: string) {
  const [a, b] = [from, to].map((d) => {
    const [y, m, day] = d.split("-").map(Number);
    return Date.UTC(y, m - 1, day);
  });
  return Math.round((b - a) / 86_400_000);
}

export type Arrival = { o: PurchaseOrder; arrive: number | null; runOut: number | null; late: boolean };

export function arrivalOf(o: PurchaseOrder, asOf: string): Arrival {
  // Sent orders have a supplier date; ones not sent yet would arrive one lead time after approval (today).
  const arrive = o.expected ? Math.max(0, daysBetween(asOf, o.expected)) : o.leadDays ?? null;
  const runOut = o.daysLeft;
  return { o, arrive, runOut, late: arrive != null && runOut != null && arrive > runOut };
}

export function ArrivalChart({ orders, asOf, selectedId, onPick }: { orders: PurchaseOrder[]; asOf: string; selectedId?: string; onPick: (id: string) => void }) {
  const tip = useTip();
  const [all, setAll] = useState(false);
  const open = orders
    .filter((o) => o.status !== "delivered" && o.status !== "cancelled" && o.daysLeft != null)
    .map((o) => arrivalOf(o, asOf))
    .sort((a, b) => Number(b.late) - Number(a.late) || (a.runOut ?? 999) - (b.runOut ?? 999));
  if (open.length === 0) return <p className="small muted">No open orders to track.</p>;
  const shown = all ? open : open.slice(0, 8);
  const span = Math.max(21, ...shown.map((r) => Math.max(r.arrive ?? 0, Math.min(r.runOut ?? 0, 60)) + 2));
  const pct = (d: number) => `${Math.min(d, span) / span * 100}%`;
  const ticks = [0, 7, 14, 21, 28, 35, 42, 49, 56].filter((t) => t <= span);
  const late = open.filter((r) => r.late).length;

  return (
    <div className="chart-wrap">
      <div className="arrive">
        <div className="arrive-axis" aria-hidden>
          <span />
          <span className="arrive-scale">
            {ticks.map((t) => (
              <span key={t} style={{ left: pct(t) }}>{t === 0 ? "today" : `+${t}d`}</span>
            ))}
          </span>
          <span />
        </div>
        {shown.map((r) => {
          const pending = r.o.status === "pending_approval";
          const label = `${r.o.drug} for ${r.o.siteName}: ${r.runOut != null ? `runs out in ${r.runOut} days` : "no run-out"}, ${r.arrive != null ? `${pending ? "would arrive" : "arrives"} in ${r.arrive} days` : "no delivery date yet"}`;
          return (
            <button
              key={r.o.id}
              className={`arrive-row ${selectedId === r.o.id ? "selected" : ""}`}
              onClick={() => onPick(r.o.id)}
              aria-label={label}
              onMouseMove={(e) => tip.show(e, <><b>{r.o.drug}</b> · {r.o.siteName}<br />Runs out in {r.runOut} days<br />{r.arrive != null ? (r.o.expected ? `${pending ? "Would arrive" : "Arrives"} ${dateLabel(r.o.expected)} (in ${r.arrive} days)` : `Would arrive in ${r.arrive} days if approved today`) : "No delivery date yet"}<br />{STATUS_WORDS[r.o.status]}</>)}
              onMouseLeave={tip.hide}
            >
              <span className="arrive-name">
                <b>{r.o.drug}</b>
                <span className="muted"> · {r.o.siteName}</span>
              </span>
              <span className="arrive-track">
                <span className="zone red" style={{ left: 0, width: pct(7) }} />
                <span className="zone amber" style={{ left: pct(7), width: `calc(${pct(14)} - ${pct(7)})` }} />
                {ticks.map((t) => <span key={t} className="gridline" style={{ left: pct(t) }} />)}
                {r.arrive != null && <span className={`arrive-bar ${pending ? "pending" : ""}`} style={{ width: pct(r.arrive) }} />}
                {r.arrive != null && <span className={`arrive-dot ${pending ? "pending" : ""}`} style={{ left: pct(r.arrive) }} />}
                {r.runOut != null && <span className="runout" style={{ left: pct(r.runOut) }} title={`Runs out in ${r.runOut} days`} />}
              </span>
              <span className="arrive-verdict">
                {r.arrive == null ? <Status tone="neutral">No date</Status> : r.late ? <Status tone="crit">Too late</Status> : <Status tone="good">In time</Status>}
              </span>
            </button>
          );
        })}
      </div>
      <div className="legend" style={{ marginTop: 10 }}>
        <span><i className="lg-bar" />Days until it arrives</span>
        <span><i className="lg-bar pending" />Not approved yet (if approved today)</span>
        <span><i className="lg-runout" />Day the hospital runs out</span>
        <span><i className="heat-red" />Under 7 days</span>
        <span><i className="heat-amber" />Under 14 days</span>
      </div>
      <div className="row-between small muted" style={{ marginTop: 8, flexWrap: "wrap", gap: 8 }}>
        <span>{late ? `${late} order${late > 1 ? "s" : ""} would arrive after the hospital runs out.` : "Every open order arrives before its hospital runs out."}</span>
        {open.length > 8 && (
          <button className="link small" onClick={() => setAll((v) => !v)}>
            {all ? "Show the 8 most urgent" : `Show all ${open.length}`}
          </button>
        )}
      </div>
      {tip.node}
    </div>
  );
}

/* ---------------- 3. Spend by supplier ---------------- */

export function SpendChart({ orders }: { orders: PurchaseOrder[] }) {
  const tip = useTip();
  const live = orders.filter((o) => o.status !== "cancelled");
  const bySupplier = new Map<string, { name: string; committed: number; waiting: number; n: number }>();
  for (const o of live) {
    const s = bySupplier.get(o.supplier) ?? { name: o.supplierName, committed: 0, waiting: 0, n: 0 };
    if (o.status === "pending_approval") s.waiting += o.totalCost;
    else s.committed += o.totalCost;
    s.n += 1;
    bySupplier.set(o.supplier, s);
  }
  const rows = [...bySupplier.values()].sort((a, b) => b.committed + b.waiting - (a.committed + a.waiting));
  if (rows.length === 0) return <p className="small muted">No orders yet.</p>;
  const max = Math.max(...rows.map((r) => r.committed + r.waiting));
  const totals = rows.reduce((t, r) => ({ c: t.c + r.committed, w: t.w + r.waiting }), { c: 0, w: 0 });
  return (
    <div className="chart-wrap">
      <div className="hbars">
        {rows.map((r) => (
          <div
            key={r.name}
            className="hbar-row static"
            tabIndex={0}
            aria-label={`${r.name}: ${money(r.committed)} approved or sent, ${money(r.waiting)} waiting for approval`}
            onMouseMove={(e) => tip.show(e, <><b>{r.name}</b> · {r.n} order{r.n === 1 ? "" : "s"}<br />Approved or sent: {money(r.committed)}<br />Waiting for approval: {money(r.waiting)}</>)}
            onMouseLeave={tip.hide}
          >
            <span className="hbar-label">{r.name}</span>
            <span className="hbar-track stacked">
              {r.committed > 0 && <span className="hbar-fill" style={{ width: `${(r.committed / max) * 100}%` }} />}
              {r.waiting > 0 && <span className="hbar-fill s2" style={{ width: `${(r.waiting / max) * 100}%` }} />}
            </span>
            <span className="hbar-val"><b>{money(r.committed + r.waiting)}</b></span>
          </div>
        ))}
      </div>
      <div className="legend" style={{ marginTop: 10 }}>
        <span><i style={{ background: "var(--series-1)" }} />Approved or sent · {money(totals.c)}</span>
        <span><i style={{ background: "var(--series-2)" }} />Waiting for approval · {money(totals.w)}</span>
      </div>
      {tip.node}
    </div>
  );
}
