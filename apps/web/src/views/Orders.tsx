import { useEffect, useMemo, useState } from "react";
import { ArrivalChart, PipelineChart, SpendChart, arrivalOf } from "../components/OrderCharts";
import { Card, Empty, PageHead, Status, type Tone } from "../components/ui";
import { USERS } from "../data/mock";
import { useStore } from "../lib/store";
import {
  ACTION_WORDS,
  STATUS_WORDS,
  WHO_WORDS,
  approvalBlock,
  clearDecisions,
  dateLabel,
  decide,
  groupOf,
  money,
  needsPerson,
  refreshOrders,
  useOrders,
  when,
  withDecision,
  type Group,
  type OrderData,
  type OrderEvent,
  type PurchaseOrder,
} from "../lib/orders";

const GROUPS: { id: Group | "all"; label: string }[] = [
  { id: "all", label: "All" },
  { id: "waiting", label: "Waiting for approval" },
  { id: "person", label: "Needs a person" },
  { id: "moving", label: "On the way" },
  { id: "done", label: "Done" },
];

function statusTone(o: PurchaseOrder): Tone {
  if (needsPerson(o)) return "crit";
  if (o.status === "pending_approval" || o.status === "delayed") return "warn";
  if (o.status === "in_transit" || o.status === "delivered") return "good";
  return "neutral";
}

function OrderStatus({ o }: { o: PurchaseOrder }) {
  return <Status tone={statusTone(o)}>{needsPerson(o) ? "Needs a person" : STATUS_WORDS[o.status]}</Status>;
}

function DaysLeft({ o }: { o: PurchaseOrder }) {
  if (o.daysLeft == null) return <span className="muted">—</span>;
  const tone: Tone = o.daysLeft < 7 ? "crit" : o.daysLeft < 14 ? "warn" : "good";
  return <Status tone={tone}>{o.daysLeft} days</Status>;
}

/** Waiting orders first (most urgent first), then the rest by how soon the site runs out. */
function sortOrders(a: PurchaseOrder, b: PurchaseOrder) {
  const rank = (o: PurchaseOrder) => ({ person: 0, waiting: 1, moving: 2, done: 3 })[groupOf(o)];
  return rank(a) - rank(b) || Number(b.urgent) - Number(a.urgent) || (a.daysLeft ?? 999) - (b.daysLeft ?? 999);
}

/** A filter is a group (the four cards) or one status (a pipeline bar). */
type Filter = Group | "all" | PurchaseOrder["status"];
const matches = (o: PurchaseOrder, f: Filter) => f === "all" || groupOf(o) === f || o.status === f;

export function Orders() {
  const { state, live } = useStore();
  const asOf = state.live?.asOf;
  const store = useOrders();
  const { data, backend } = store;
  const decisions = backend === "file" ? store.decisions : {};
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Reload for the chosen day, and after every day run or decision elsewhere.
  useEffect(() => {
    void refreshOrders(asOf);
  }, [asOf, state.live]);

  const orders = useMemo(
    () => data.orders.map((o) => withDecision(o, decisions[o.id])).sort(sortOrders),
    [data, decisions],
  );
  const count = (g: Group) => orders.filter((o) => groupOf(o) === g).length;
  const shown = orders.filter((o) => matches(o, filter));
  const late = orders.filter((o) => o.status !== "delivered" && o.status !== "cancelled" && arrivalOf(o, data.asOf).late).length;
  const selected = orders.find((o) => o.id === selectedId) ?? shown[0] ?? orders[0];
  const waiting = count("waiting");
  const decidedHere = Object.keys(decisions).length;
  const canDecide = state.role === "procurement" || state.role === "supply";

  // On narrow screens the details sit below the list, so bring them into view.
  const pick = (id: string, scroll = false) => {
    setSelectedId(id);
    // Picked from a chart: make sure the list shows it.
    if (!orders.some((o) => o.id === id && matches(o, filter))) setFilter("all");
    if (scroll || window.matchMedia?.("(max-width: 1100px)").matches) {
      requestAnimationFrame(() => document.querySelector(".order-detail")?.scrollIntoView({ block: "start" }));
    }
  };

  return (
    <>
      <PageHead
        label="Orders"
        title={waiting ? `${waiting} supplier order${waiting > 1 ? "s" : ""} waiting for approval` : "No orders waiting for approval"}
        lead="Every evening the order agent drafts orders for hospitals running low. A person approves each one. The agent then sends it and follows it up until it arrives, passing anything unclear to a person."
      />

      <SourceBanner data={data} backend={backend} reason={store.offlineReason} viewer={canDecide ? null : USERS[state.role].name} liveApp={live.mode === "live"} />

      <div className="stats">
        {(
          [
            ["waiting", "Waiting for approval", "warn"],
            ["person", "Needs a person", "crit"],
            ["moving", "On the way", "good"],
            ["done", "Delivered or cancelled", "neutral"],
          ] as [Group, string, Tone][]
        ).map(([g, label, tone]) => (
          <button key={g} className="stat" onClick={() => setFilter(g)} aria-pressed={filter === g}>
            <span className="label">{label}</span>
            <span className="stat-value">{count(g)}</span>
            <Status tone={tone}>{filter === g ? "Showing below" : "Show these"}</Status>
          </button>
        ))}
      </div>

      {orders.length > 0 && (
        <>
          <div className="grid-even">
            <Card label="Pipeline · click a bar to list those orders" title="Where every order is">
              <PipelineChart orders={orders} active={filter} onPick={(s) => setFilter(filter === s ? "all" : s)} />
            </Card>
            <Card label="Spend · cancelled orders left out" title="Money by supplier">
              <SpendChart orders={orders} />
            </Card>
          </div>
          <Card
            className="arrive-card"
            label={`Open orders · days from ${dateLabel(data.asOf)}`}
            title="Will each order arrive before the hospital runs out?"
            actions={late ? <Status tone="crit">{late} too late</Status> : <Status tone="good">All in time</Status>}
          >
            <ArrivalChart orders={orders} asOf={data.asOf} selectedId={selected?.id} onPick={(id) => pick(id, true)} />
          </Card>
        </>
      )}

      <div className="grid-2">
        <section className="card">
          <header className="card-head">
            <div>
              <div className="label" style={{ marginBottom: 4 }}>
                {shown.length} order{shown.length === 1 ? "" : "s"}
              </div>
              <h3>Most urgent first</h3>
            </div>
          </header>
          <div className="filters" style={{ padding: "0 20px 12px" }}>
            {GROUPS.map((g) => (
              <button key={g.id} className={filter === g.id ? "active" : ""} onClick={() => setFilter(g.id)}>
                {g.label}
              </button>
            ))}
            {filter in STATUS_WORDS && (
              <button className="active" onClick={() => setFilter("all")} aria-label="Clear the stage filter">
                {STATUS_WORDS[filter as PurchaseOrder["status"]]} ✕
              </button>
            )}
          </div>
          {shown.length === 0 ? (
            <Empty title="Nothing here">No orders in this group.</Empty>
          ) : (
            <div style={{ overflowX: "auto" }}>
              <table className="table order-table">
                <thead>
                  <tr>
                    <th>Drug and hospital</th>
                    <th className="col-supplier">Supplier</th>
                    <th className="num col-cost">Cost</th>
                    <th>Stock left</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((o) => (
                    <tr
                      key={o.id}
                      className={o.id === selected?.id ? "selected" : ""}
                      onClick={() => pick(o.id)}
                      tabIndex={0}
                      onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && pick(o.id)}
                      aria-selected={o.id === selected?.id}
                    >
                      <td>
                        <strong>{o.drug}</strong>
                        <div className="small ink-2">
                          {o.qty} for {o.siteName}
                          <span className="sup-inline"> · {o.supplierName} · {money(o.totalCost)}</span>
                          {o.urgent && o.status === "pending_approval" && (
                            <>
                              {" · "}
                              <span className="urgent-word">Urgent</span>
                            </>
                          )}
                        </div>
                      </td>
                      <td className="small col-supplier">{o.supplierName}</td>
                      <td className="num col-cost">{money(o.totalCost)}</td>
                      <td>
                        <DaysLeft o={o} />
                      </td>
                      <td>
                        <OrderStatus o={o} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {selected && <OrderDetail key={selected.id} o={selected} />}
      </div>

      <AgentTimeline data={data} />

      {backend === "file" && decidedHere > 0 && (
        <p className="small muted">
          You decided {decidedHere} order{decidedHere > 1 ? "s" : ""} on this page. Those decisions are kept in this browser only.{" "}
          <button className="link small" onClick={clearDecisions}>
            Undo them
          </button>
        </p>
      )}
    </>
  );
}

function OrderDetail({ o }: { o: PurchaseOrder }) {
  const { state } = useStore();
  const { data, backend, decisions, busy } = useOrders();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const events = data.events.filter((e) => e.order === o.id);
  const local = backend === "file" ? decisions[o.id] : undefined;
  const replacement = data.orders.find((x) => x.replaces === o.id);
  const block = approvalBlock(o, state.role, data.procurementLimit);
  const pending = o.status === "pending_approval";

  const act = async (result: "approved" | "rejected") => {
    setError(null);
    const problem = await decide(o.id, { result, by: USERS[state.role].name, role: state.role, note, at: new Date().toISOString() });
    if (problem) setError(problem);
    else setNote("");
  };

  return (
    <Card
      className="order-detail"
      label={`${o.id} · ${o.kind === "shortage" ? "emergency order from a shortage plan" : o.replaces ? `replaces ${o.replaces}` : "routine reorder"}`}
      title={`${o.drug}`}
      actions={<OrderStatus o={o} />}
      footer={
        pending ? (
          <div className="stack" style={{ gap: 10, width: "100%" }}>
            <span className="small muted">
              {block ??
                (backend === "database"
                  ? "Approving saves the decision in the database. The agent sends the order at its next check."
                  : "Approving lets the agent send it at its next check. Nothing is sent from this page.")}
            </span>
            <div className="row" style={{ flexWrap: "wrap" }}>
              <input
                className="input"
                style={{ flex: 1, minWidth: 160 }}
                placeholder="Note (needed to reject)"
                aria-label="Note for the record"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={!!block || busy}
              />
              <button className="btn" disabled={!!block || busy || !note.trim()} onClick={() => act("rejected")}>
                Reject
              </button>
              <button className="btn primary" disabled={!!block || busy} onClick={() => act("approved")}>
                {busy ? "Saving…" : `Approve ${money(o.totalCost)}`}
              </button>
            </div>
            {error && (
              <div className="callout crit" role="alert">
                <span className="ico" aria-hidden>
                  !
                </span>
                <div>
                  <strong>Not saved.</strong>
                  <span className="ink-2">{error}</span>
                </div>
              </div>
            )}
          </div>
        ) : undefined
      }
    >
      <div className="stack" style={{ gap: 16 }}>
        {o.urgent && pending && (
          <div className="callout crit">
            <span className="ico" aria-hidden>
              !
            </span>
            <div>
              <strong>An order alone can't arrive in time.</strong>
              <span className="ink-2">{o.siteName} runs out before any supplier can deliver, so a transfer from another hospital is needed too.</span>
            </div>
          </div>
        )}
        {local && (
          <div className={`callout ${local.result === "approved" ? "good" : "info"}`}>
            <span className="ico" aria-hidden>
              {local.result === "approved" ? "✓" : "✕"}
            </span>
            <div>
              <strong>
                You {local.result} this order as {local.by}.
              </strong>
              <span className="ink-2">{local.note ? `Note: “${local.note}”` : "The agent would send it to the supplier at its next check."}</span>
            </div>
          </div>
        )}

        <dl className="facts">
          <dt>Hospital</dt>
          <dd>{o.siteName}</dd>
          <dt>Supplier</dt>
          <dd>{o.supplierName}</dd>
          <dt>Quantity</dt>
          <dd>
            {o.confirmedQty > 0 && o.confirmedQty < o.qty ? (
              <>
                {o.confirmedQty} of {o.qty} confirmed
                {replacement && <span className="muted">· rest ordered as {replacement.id}</span>}
              </>
            ) : (
              <>
                {o.qty} at ${o.unitCost.toFixed(2)}
              </>
            )}
          </dd>
          <dt>Cost</dt>
          <dd className="num">{money(o.totalCost)}</dd>
          <dt>Stock left</dt>
          <dd>
            <DaysLeft o={o} />
            {o.neededBy && <span className="muted">· order needed by {dateLabel(o.neededBy)}</span>}
          </dd>
          <dt>Expected</dt>
          <dd>{o.status === "delivered" ? `Delivered ${dateLabel(o.expected)}` : o.expected ? dateLabel(o.expected) : o.leadDays != null ? `About ${o.leadDays} days after it is sent` : "—"}</dd>
          {o.approvedBy && (
            <>
              <dt>Approved by</dt>
              <dd>
                {o.approvedBy} ({o.approvedRole?.replace("_", " ")})
              </dd>
            </>
          )}
        </dl>

        {o.reason && (
          <div>
            <div className="label" style={{ marginBottom: 4 }}>
              Why the agent wants it
            </div>
            <p className="small ink-2">{o.reason.replace(/ URGENT:.*$/, "")}</p>
          </div>
        )}

        <div>
          <div className="label" style={{ marginBottom: 4 }}>
            What happened
          </div>
          {events.length === 0 ? (
            <p className="small muted">{pending ? "Nothing yet. It waits for a person to decide." : "No follow-up recorded."}</p>
          ) : (
            <ul className="timeline">
              {events.map((e, i) => (
                <EventRow key={i} e={e} compact />
              ))}
            </ul>
          )}
        </div>
      </div>
    </Card>
  );
}

function EventRow({ e, compact }: { e: OrderEvent; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const order = useOrders().data.orders.find((o) => o.id === e.order);
  const human = e.who === "human";
  return (
    <li className="tl-item" style={compact ? { padding: "8px 0" } : undefined}>
      <span className={`who-dot ${human ? "human" : e.who === "agent" ? "agent" : ""}`} title={WHO_WORDS[e.who] ?? e.who} aria-label={WHO_WORDS[e.who] ?? e.who}>
        {human ? "P" : e.who === "agent" ? "AI" : "R"}
      </span>
      <div>
        <div className="what">
          <b>{ACTION_WORDS[e.action] ?? e.action}</b>
          {!compact && order && (
            <span className="ink-2">
              {" "}
              · {order.drug} for {order.siteName}
            </span>
          )}
        </div>
        <div className="detail">
          {compact && <span className="meta">{when(e.at)} · </span>}
          {human ? `${e.by}. ` : `${WHO_WORDS[e.who] ?? e.who}. `}
          {e.text}
        </div>
        {(e.sentVia || e.event || e.newOrder || (e.flags && e.flags.length > 0) || e.message) && (
          <div className="chips" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {e.sentVia && <Status tone="neutral">Sent to {e.to} (simulated)</Status>}
            {e.newOrder && <Status tone="warn">New order {e.newOrder} waiting for approval</Status>}
            {e.event && <Status tone="crit">Shortage alert raised</Status>}
            {e.flags?.some((f) => /instruction_override|impersonation|unauthorized/.test(f)) && <Status tone="crit">Suspicious reply blocked</Status>}
            {e.message && !e.sentVia && (
              <button className="link small" onClick={() => setOpen((x) => !x)} aria-expanded={open}>
                {open ? "Hide message" : `Message for ${e.to}`}
              </button>
            )}
          </div>
        )}
        {open && e.message && <pre className="letter small" style={{ marginTop: 6 }}>{e.message}</pre>}
        {e.event && <div className="detail">{e.event}</div>}
      </div>
    </li>
  );
}

function AgentTimeline({ data }: { data: OrderData }) {
  const [who, setWho] = useState<"all" | "human" | "agent">("all");
  const [more, setMore] = useState(false);
  const events = data.events.filter((e) => who === "all" || (who === "human" ? e.who === "human" : e.who !== "human"));
  const groups = events.reduce<Record<string, OrderEvent[]>>((acc, e) => {
    (acc[e.at] ??= []).push(e);
    return acc;
  }, {});

  return (
    <section className="card">
      <header className="card-head">
        <div>
          <div className="label" style={{ marginBottom: 4 }}>
            Follow-up · {data.outbox} messages through the simulated supplier channel
          </div>
          <h3>What the agent did, check by check</h3>
        </div>
        <div className="filters">
          {(
            [
              ["all", "Everything"],
              ["human", "People"],
              ["agent", "Agent"],
            ] as const
          ).map(([id, label]) => (
            <button key={id} className={who === id ? "active" : ""} onClick={() => setWho(id)}>
              {label}
            </button>
          ))}
        </div>
      </header>
      <p className="small ink-2" style={{ padding: "0 20px" }}>
        <b>P</b> a person decided. <b>R</b> a fixed rule decided (clear cases, or the backup when the AI is unavailable). <b>AI</b> the AI agent decided a grey-zone case, choosing only from options the rules allowed.
      </p>
      <WhoChart events={data.events} />
      <ul className="timeline">
        {Object.entries(groups).reverse().slice(0, more ? undefined : 6).map(([at, list]) => (
          <li key={at} className="tl-group">
            <div className="tl-time">{when(at)}</div>
            <ul className="timeline">
              {list.map((e, i) => (
                <EventRow key={i} e={e} />
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {Object.keys(groups).length > 6 && (
        <div style={{ padding: "0 20px 16px" }}>
          <button className="link small" onClick={() => setMore((m) => !m)}>
            {more ? "Show only the latest" : `Show all ${Object.keys(groups).length} checks`}
          </button>
        </div>
      )}
    </section>
  );
}

/** Who made the calls: people, fixed rules, or the AI. One stacked bar, labelled. */
function WhoChart({ events }: { events: OrderEvent[] }) {
  const parts = [
    { id: "human", label: "People", n: events.filter((e) => e.who === "human").length, cls: "s-human" },
    { id: "rule", label: "Fixed rules", n: events.filter((e) => e.who === "rule" || e.who === "fallback").length, cls: "s-rule" },
    { id: "agent", label: "AI agent", n: events.filter((e) => e.who === "agent").length, cls: "s-agent" },
  ];
  const total = parts.reduce((t, p) => t + p.n, 0);
  if (!total) return null;
  return (
    <div style={{ padding: "4px 20px 8px" }}>
      <div className="whobar" role="img" aria-label={parts.map((p) => `${p.label} ${p.n}`).join(", ")}>
        {parts.filter((p) => p.n).map((p) => (
          <span key={p.id} className={p.cls} style={{ flexGrow: p.n }} title={`${p.label}: ${p.n}`} />
        ))}
      </div>
      <div className="legend" style={{ marginTop: 8 }}>
        {parts.map((p) => (
          <span key={p.id}><i className={p.cls} />{p.label} · {p.n}</span>
        ))}
      </div>
    </div>
  );
}

function SourceBanner({ data, backend, reason, viewer, liveApp }: { data: OrderData; backend: string; reason: string | null; viewer: string | null; liveApp: boolean }) {
  const who = viewer ? ` You're viewing as ${viewer}; only procurement or the supply manager can approve orders.` : "";
  if (backend === "database" && data.catalog === false) {
    return (
      <div className="callout warn">
        <span className="ico" aria-hidden>!</span>
        <div>
          <strong>No supplier catalog in the database yet, so the order agent has nothing to order from.</strong>
          <span className="ink-2">
            Load it once with <code className="mono">.venv/bin/python -m services.agents.order_db load-catalog</code>, then run a day.
          </span>
        </div>
      </div>
    );
  }
  if (backend === "database") {
    return (
      <div className="callout good">
        <span className="ico" aria-hidden>
          ✓
        </span>
        <div>
          <strong>Live from the database.</strong>
          <span className="ink-2">
            The order agent runs every evening (20:00) when you click <b>Run day</b> at the top: it sends approved orders, reads supplier replies, chases late ones and drafts new reorders.
            Stock is from the {dateLabel(data.asOf)} count. Orders go through a simulated supplier channel, so nothing reaches a real supplier.{who}
          </span>
        </div>
      </div>
    );
  }
  return (
    <div className="callout info">
      <span className="ico" aria-hidden>
        i
      </span>
      <div>
        <strong>
          {backend === "loading"
            ? "Connecting to the database…"
            : `Showing a saved run of the order agent (${data.mode === "live" ? "with the AI" : "fixed rules"}, six-hospital test data), not the database.`}
        </strong>
        {backend === "file" && (
          <span className="ink-2">
            {liveApp ? reason ?? "The orders API did not answer." : "The platform API is not running."} Decisions you make now stay in this browser. To use the database, start it with{" "}
            <code className="mono">.venv/bin/uvicorn apps.api.main:app --port 8000</code>, then{" "}
            <button className="link small" onClick={() => refreshOrders()}>
              try again
            </button>
            .{who}
          </span>
        )}
      </div>
    </div>
  );
}
