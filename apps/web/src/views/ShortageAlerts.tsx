import { useState } from "react";
import { planStatus, usePlanSummary } from "../components/PlanCard";
import { Empty, Origin, PageHead, Status, formatDays, type OriginKind, type Tone } from "../components/ui";
import { SITES, dayLabel, drugById, siteName } from "../data/mock";
import { lineCoverage, tierFor } from "../lib/risk";
import { currentOptions, stageOf, type Stage } from "../lib/response";
import { useStore } from "../lib/store";
import type { FollowUp } from "../lib/api";
import type { Plan, Tier } from "../lib/types";

type Go = (view: "approvals" | "events", eventId?: string, optionId?: string) => void;

const STAGES: { id: Stage; title: string; note: string }[] = [
  { id: "decide", title: "Needs decision", note: "a checked plan is waiting for approval" },
  { id: "attention", title: "Needs attention", note: "no checked plan yet" },
  { id: "tracking", title: "Approved · tracking", note: "follow-up is monitoring the response" },
];
const TIER_WORD: Record<Tier, string> = { red: "Critical", amber: "Watch", green: "OK" };
const TIER_TONE: Record<Tier, Tone> = { red: "crit", amber: "warn", green: "good" };
const SOURCE_WORD: Record<string, string> = {
  stock_watch: "Stock watch",
  supplier_email: "Supplier notice",
  shortage_report: "Health Canada report",
  manual: "Requested by a person",
};
/** Who turned each signal into the alert: the assessment agent reads supplier notices; code raises the rest. */
const SOURCE_ORIGIN: Record<string, OriginKind> = {
  stock_watch: "rules",
  supplier_email: "agent",
  shortage_report: "rules",
  manual: "human",
};

/** One alert per drug: where it bites, how urgent, and where its response stands. */
function useAlerts() {
  const { state } = useStore();
  return state.events
    .filter((e) => e.status === "open" || e.status === "mitigated")
    .map((event) => {
      const sites = event.sites?.length ? event.sites : SITES.map((s) => s.id);
      const lines = state.stock
        .filter((l) => l.drugId === event.drugId && sites.includes(l.siteId))
        .map((l) => ({ line: l, days: lineCoverage(l), tier: tierFor(lineCoverage(l), l.safetyDays) as Tier }))
        .sort((a, b) => a.days - b.days);
      const worst = lines[0];
      const options = currentOptions(state.plans, event.id);
      return { event, lines, worst, tier: (worst?.tier ?? "green") as Tier, options, stage: stageOf(options) };
    })
    .sort((a, b) => (a.worst?.days ?? Infinity) - (b.worst?.days ?? Infinity));
}

export function ShortageAlerts({ go, focusEvent }: { go: Go; focusEvent?: string | null }) {
  const alerts = useAlerts();
  const [picked, setPicked] = useState<string | null>(focusEvent ?? null);
  const focused = alerts.find((a) => a.event.id === focusEvent);
  // Opens on the tab of an alert linked from elsewhere, else on whatever needs a decision.
  const [tab, setTab] = useState<Stage | "all">(() => focused?.stage ?? (alerts.some((a) => a.stage === "decide") ? "decide" : "all"));
  const visible = tab === "all" ? alerts : alerts.filter((a) => a.stage === tab);
  const current = visible.find((a) => a.event.id === picked) ?? visible[0];

  if (alerts.length === 0) {
    return (
      <>
        <PageHead label="Shortage Alerts" title="Shortage Alerts" lead="No open alerts." />
        <Empty title="No active shortages">Alerts appear when the daily run finds a drug that will not last at a facility.</Empty>
      </>
    );
  }

  const count = (stage: Stage) => alerts.filter((a) => a.stage === stage).length;
  const select = (id: string) => {
    setPicked(id);
    requestAnimationFrame(() => document.getElementById("alert-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" }));
  };
  const tabs: { id: Stage | "all"; title: string; n: number }[] = [
    { id: "all", title: "All", n: alerts.length },
    ...STAGES.map((s) => ({ id: s.id, title: s.title, n: count(s.id) })),
  ];
  const note = tab === "all" ? "every active alert" : STAGES.find((s) => s.id === tab)!.note;

  return (
    <>
      <PageHead label="Shortage Alerts" title="Shortage Alerts" lead="One alert per drug, coloured by lowest days of supply · select an alert for its response plan" />

      <section className="card" aria-labelledby="alerts-overview">
        <header className="card-head" style={{ paddingBottom: 0 }}>
          <div>
            <div className="label">Alert overview</div>
            <h3 id="alerts-overview" className="sr-only">Alert overview</h3>
          </div>
        </header>
        <div className="card-body">
          <div className="view-tabs" role="tablist" aria-label="Alert stage">
            {tabs.map((t) => (
              <button
                key={t.id}
                role="tab"
                aria-selected={tab === t.id}
                className={tab === t.id ? "active" : ""}
                onClick={() => {
                  setTab(t.id);
                  setPicked(null);
                }}
              >
                {t.title} <span className="num muted">{t.n}</span>
              </button>
            ))}
          </div>
          <p className="small muted" style={{ margin: "10px 0" }}>
            {visible.length} alert{visible.length === 1 ? "" : "s"} · {note}
          </p>
          {visible.length === 0 ? (
            <p className="small muted">None at the moment.</p>
          ) : (
            <div className="alert-grid" role="tabpanel">
              {visible.map((a) => (
                <button
                  key={a.event.id}
                  className={`alert-tile tier-${a.tier} ${current?.event.id === a.event.id ? "on" : ""}`}
                  onClick={() => select(a.event.id)}
                  aria-pressed={current?.event.id === a.event.id}
                >
                  <span className="alert-name">{drugById(a.event.drugId)?.name ?? a.event.drugId}</span>
                  <span className="alert-days">
                    {a.worst ? formatDays(a.worst.days) : "–"}
                    <small> d</small>
                  </span>
                  <span className="alert-meta">
                    {a.lines.filter((l) => l.tier !== "green").length} facilit{a.lines.filter((l) => l.tier !== "green").length === 1 ? "y" : "ies"} at risk
                    {a.worst ? ` · lowest ${siteName(a.worst.line.siteId)}` : ""}
                  </span>
                  {a.options.some((p) => p.optionType === "substitute") && <span className="alert-flag">Substitute available</span>}
                </button>
              ))}
            </div>
          )}
        </div>
      </section>

      {current && <AlertDetail a={current} go={go} />}
    </>
  );
}

function AlertDetail({ a, go }: { a: ReturnType<typeof useAlerts>[number]; go: Go }) {
  const { state, live } = useStore();
  const drug = drugById(a.event.drugId);
  const sources = [...new Set((a.event.evidence ?? []).map((e) => e.source))];
  const canPlan = state.role === "supply" && live.busy === null;
  // The simulated day the alert was first raised, from its source reference (e.g. "stock_watch:2026-11-15:…").
  const day = a.event.sourceRef.match(/\d{4}-\d{2}-\d{2}/)?.[0];
  const raisedOn = day ? new Date(`${day}T12:00`).toLocaleDateString("en-CA", { month: "short", day: "numeric" }) : null;
  return (
    <section id="alert-detail" className="card alert-detail" aria-labelledby="alert-detail-title">
      <header className="card-head">
        <div>
          <div className="label">
            Alert detail · {drug?.criticalList ? "critical-list drug" : "tracked drug"}
            {raisedOn ? ` · first raised ${raisedOn}` : ""}
          </div>
          <h3 id="alert-detail-title">{drug?.name ?? a.event.drugId}</h3>
        </div>
        <Status tone={a.stage === "tracking" ? "good" : a.stage === "decide" ? "warn" : "crit"}>{a.event.planState ?? "No plan"}</Status>
      </header>

      <div className="card-body alert-detail-grid">
        <div>
          <div className="label" style={{ marginBottom: 8 }}>Facilities · days of supply</div>
          <table className="table alert-table">
            <thead>
              <tr>
                <th>Facility</th>
                <th className="num">Days</th>
                <th>Runs out</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {a.lines.map((l) => (
                <tr key={l.line.siteId}>
                  <td>{siteName(l.line.siteId)}</td>
                  <td className="num">
                    <b>{formatDays(l.days)}</b>
                  </td>
                  <td>{l.days >= 60 ? "60+ days" : dayLabel(Math.floor(l.days))}</td>
                  <td>
                    <Status tone={TIER_TONE[l.tier]}>{TIER_WORD[l.tier]}</Status>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="small muted" style={{ marginTop: 10 }}>
            Signals:{" "}
            <span className="origins">
              {(sources.length ? sources : ["stock_watch"]).map((src) => (
                <Origin key={src} by={SOURCE_ORIGIN[src] ?? "rules"} label={SOURCE_WORD[src] ?? src} />
              ))}
            </span>{" "}
            <button className="link small" onClick={() => go("events", a.event.id)}>
              Review signals →
            </button>
          </p>
        </div>

        <div className="stack" style={{ gap: 10 }}>
          <div className="row-between">
            <div className="label">Response plan · {a.options.length} option{a.options.length === 1 ? "" : "s"}</div>
            {a.stage === "decide" && (
              <button className="btn primary" onClick={() => go("approvals", a.event.id)}>
                Decide in Approval Queue
              </button>
            )}
          </div>
          {a.options.length === 0 ? (
            <div className="callout warn">
              <span className="ico" aria-hidden>
                !
              </span>
              <div style={{ flex: 1 }}>
                <strong>No plan generated yet.</strong>
                <span className="ink-2">The planner can propose options from current stock now.</span>
              </div>
              <button className="btn primary" disabled={!canPlan} onClick={() => void live.planDrug(a.event.drugId, a.worst?.line.siteId)}>
                {live.busy === "Planning a response" ? "Generating…" : "Generate plan"}
              </button>
            </div>
          ) : (
            <OptionsTable options={a.options} open={(optionId) => go("approvals", a.event.id, optionId)} />
          )}
          {a.stage === "tracking" && (() => {
            const approved = a.options.find((p) => p.status === "approved");
            const record = approved && state.audit.find((e) => e.action === "Approved" && e.refs.includes(approved.solverRun));
            const orders = (state.live?.followups ?? []).filter((f) => f.kind === "order" && approved && f.id.startsWith(`PO-${approved.solverRun}-`));
            return (
              <>
                {record && (
                  <p className="small ink-2">
                    Approved by <Origin by="human" label={record.actorName} /> · option {approved!.rank}
                  </p>
                )}
                {orders.map((o) => <OrderTracking key={o.id} order={o} />)}
              </>
            );
          })()}
          {a.stage === "attention" && a.options.length > 0 && (
            <p className="small muted">No transfer passed the safety checks. The options above need a person: escalate, conserve, or an approved substitute.</p>
          )}
        </div>
      </div>
    </section>
  );
}


const TYPE_WORD: Record<string, string> = {
  transfer: "Transfer", order: "Order", transfer_order: "Transfer + order", substitute: "Substitute",
  conserve: "Conserve", escalate: "Escalate",
};

/** The response plan's options, read-only: the decision is made in the Approval Queue (clicking a row opens it). */
function OptionsTable({ options, open }: { options: Plan[]; open: (optionId?: string) => void }) {
  return (
    <div className="tablewrap">
      <table className="table detail-table options-table">
        <thead>
          <tr>
            <th className="num">#</th>
            <th>Type</th>
            <th>Option</th>
            <th>Safety checks</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {options.map((p) => <OptionRow key={p.id} plan={p} open={open} />)}
        </tbody>
      </table>
    </div>
  );
}

function OptionRow({ plan, open }: { plan: Plan; open: (optionId?: string) => void }) {
  const { state } = useStore();
  const { title, feasible, by, kind } = usePlanSummary(plan);
  const status = planStatus(plan.status, state.live !== null);
  const top = plan.rank === 1 && plan.status !== "approved" && feasible;
  return (
    <tr className={plan.status === "approved" ? "approved" : ""} onClick={() => open(plan.status === "pending-approval" ? plan.id : undefined)} title="Open this option in the Approval Queue">
      <td className="num">{plan.rank}</td>
      <td className="nowrap">
        <span className="stage-cell">
          <Origin by={by.by} label={by.label} compact />
          {TYPE_WORD[kind ?? plan.optionType ?? ""] ?? "Option"}
        </span>
      </td>
      <td>
        {title}
        {top && <span className="badge-accent" style={{ marginLeft: 8 }}>Recommended</span>}
      </td>
      <td className="nowrap">{feasible ? <Status tone="good">Pass</Status> : <Status tone="crit">Breaks a rule</Status>}</td>
      <td className="nowrap"><Status tone={status.tone}>{status.text}</Status></td>
    </tr>
  );
}

const ORDER_TONE: Record<string, Tone> = { approved: "warn", placed: "good", in_transit: "good", delayed: "warn", delivered: "good", cancelled: "crit" };
const CHECK_TONE: Record<string, Tone> = { wait: "good", close: "good", nudge: "warn", escalate: "crit", replan: "crit" };

const REPLY_WORD: Record<string, string> = {
  confirmed: "Confirmed",
  partial: "Partial",
  delayed: "Delayed",
  backordered: "Backordered",
  cancelled: "Cancelled",
  question: "Question",
  unrelated: "Unrelated",
};
const SUBMIT_WORD: Record<string, string> = {
  accepted: "Placed",
  rejected: "Rejected",
  failed: "Not submitted",
  held: "Held for release",
  ready_to_send: "Email ready",
  escalated: "Escalated",
};
const SUBMIT_TONE: Record<string, Tone> = {
  accepted: "good", rejected: "crit", failed: "crit", held: "warn", ready_to_send: "warn", escalated: "crit",
};
const SUB_AGENT: Record<string, string> = {
  sap_erp: "SAP sub-agent", edi_x12: "EDI sub-agent", supplier_portal: "Portal sub-agent", email: "Email sub-agent",
};
const OUTCOME_WORD: Record<string, string> = {
  accepted: "order placed",
  handed_to_person: "handed to procurement",
  try_other_channel: "try another channel",
  needs_person: "a person must act",
  not_submitted: "not submitted",
};

/** What the order orchestrator agent did: each hand-off to a channel sub-agent (who chose it and why), how
 * the sub-agent read the system's answer, and the exact payload built for that system. */
function Submissions({ order: o }: { order: FollowUp }) {
  const { state, live } = useStore();
  const attempts = o.submissions ?? [];
  if (!attempts.length) return null;
  const last = attempts[attempts.length - 1];
  const canRelease = last.status === "held" && (state.role === "procurement" || state.role === "supply") && live.busy === null;
  const summary = o.orchestration;
  return (
    <div className="stack" style={{ gap: 4 }}>
      <details>
        <summary className="small">
          How it was placed: {attempts.length} step{attempts.length > 1 ? "s" : ""}
          {summary ? (summary.decided_by === "agent" ? ", by the orchestrator agent" : ", by the orchestrator rules") : ""}
        </summary>
      <div className="stack" style={{ gap: 4, paddingLeft: 8, marginTop: 4 }}>
      {summary && (
        <span className="small">
          <Origin by={summary.decided_by === "agent" ? "agent" : "rules"}
            label={summary.decided_by === "agent" ? "Order orchestrator agent" : "Order orchestrator rules"} compact />{" "}
          {summary.summary}
        </span>
      )}
      {attempts.map((a, i) => (
        <details key={i}>
          <summary className="small">
            <Origin by={a.chosenBy === "agent" ? "agent" : "rules"}
              label={a.chosenBy === "agent" ? "Chosen by the orchestrator agent" : "Chosen by the orchestrator rules"} compact />{" "}
            <Status tone={SUBMIT_TONE[a.status] ?? "neutral"}>{SUBMIT_WORD[a.status] ?? a.status}</Status>{" "}
            {SUB_AGENT[a.channel] ? `${SUB_AGENT[a.channel]} → ${a.target}` : a.target}
            {a.externalRef ? ` · ref ${a.externalRef}` : ""}
          </summary>
          <div className="stack" style={{ gap: 4, paddingLeft: 4 }}>
            {a.reason && <span className="small ink-2">Brief: {a.reason}</span>}
            {a.note && <span className="small ink-2">System answered: {a.note}</span>}
            {a.report && (
              <span className="small">
                <Origin by={a.report.read_by === "agent" ? "agent" : "rules"}
                  label={a.report.read_by === "agent" ? (SUB_AGENT[a.channel] ?? "Sub-agent") : "Sub-agent rules"} compact />{" "}
                Read as <b>{OUTCOME_WORD[a.report.outcome] ?? a.report.outcome}</b>: {a.report.summary}
              </span>
            )}
            {a.report?.steps?.length ? (
              <ol className="small muted" style={{ margin: 0, paddingLeft: 18 }}>
                {a.report.steps.map((s, j) => <li key={j}>{s}</li>)}
              </ol>
            ) : null}
            {a.payload != null && (
              <pre className="small ink-2" style={{ whiteSpace: "pre-wrap", overflowX: "auto", maxHeight: 220 }}>
                {Array.isArray((a.payload as { segments?: string[] }).segments)
                  ? (a.payload as { segments: string[] }).segments.join("~\n")
                  : JSON.stringify(a.payload, null, 2)}
              </pre>
            )}
            {a.status !== "escalated" && a.status !== "held" && (
              <p className="small muted">
                {a.mode === "simulate"
                  ? "Simulated: the payload was built and checked in code and the system's answer simulated. Nothing left this machine."
                  : "Sent to the live system."}
              </p>
            )}
          </div>
        </details>
      ))}
      </div>
      </details>
      {canRelease && (
        <div className="row" style={{ gap: 8 }}>
          <button className="btn" onClick={() => void live.releaseOrder(o.id)}>Release to supplier</button>
          <span className="small muted">Above the auto-submit limit: a person releases it.</span>
        </div>
      )}
    </div>
  );
}

/** One line that says where the order stands with the supplier: placed (where, reference, when), waiting on
 * procurement, or escalated. The detail is one click away. */
function placedLine(o: FollowUp): { tone: Tone; label: string; text: string } | null {
  const attempts = o.submissions ?? [];
  const when = (at: string) => new Date(at).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
  const placed = attempts.find((a) => a.status === "accepted");
  if (placed) return { tone: "good", label: "Placed", text: `via ${placed.target}${placed.externalRef ? ` · ref ${placed.externalRef}` : ""} · ${when(placed.at)}` };
  if (o.orderStatus && o.orderStatus !== "approved") return { tone: "good", label: "Placed", text: `${o.orderStatusText ?? o.orderStatus}, recorded by procurement` };
  const last = attempts[attempts.length - 1];
  if (!last) return null;
  const why = attempts.find((a) => a.report?.outcome === "needs_person")?.report?.summary;
  if (last.status === "ready_to_send") return { tone: "warn", label: "Waiting on procurement", text: `to send the email to ${o.from}${why ? `. ${why}` : ""}` };
  if (last.status === "escalated") return { tone: "crit", label: "Escalated to procurement", text: `to place by hand: ${last.note ?? ""}` };
  if (last.status === "held") return { tone: "warn", label: "Held", text: "for procurement to release (above the auto-submit limit)" };
  return null;
}

/** Who a person escalates an order to: whoever acts next. Code on the server decides; this only labels the button. */
const ESCALATE_ORDER_TO: Partial<Record<string, string>> = {
  supply: "procurement", pharmacist: "procurement", procurement: "the supply manager",
};

function EscalateButton({ objectId, to }: { objectId: string; to: string }) {
  const { live } = useStore();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  if (!open) {
    return (
      <div className="row">
        <button className="btn" disabled={live.busy !== null} onClick={() => setOpen(true)}>Escalate to {to}</button>
      </div>
    );
  }
  return (
    <div className="escalate-box">
      <label className="small" htmlFor={`esc-${objectId}`}>Escalate to {to}</label>
      <textarea id={`esc-${objectId}`} rows={3} value={message} placeholder={`What does ${to} need to do?`}
        onChange={(e) => setMessage(e.target.value)} autoFocus />
      <div className="row">
        <button className="btn primary" disabled={!message.trim() || live.busy !== null}
          onClick={() => void live.escalate(objectId, message).then(() => { setOpen(false); setMessage(""); })}>
          Send to {to}
        </button>
        <button className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
      </div>
    </div>
  );
}

const REPLY_TONE: Record<string, Tone> = { confirmed: "good", partial: "warn", delayed: "warn", backordered: "crit", cancelled: "crit", question: "warn" };

/** An approved order: where it stands, the order agent's latest check and its request to the supplier.
 * Procurement (or the supply manager) records what happened; nothing is sent from here. */
function OrderTracking({ order: o }: { order: FollowUp }) {
  const { state, live } = useStore();
  const canRecord = (state.role === "procurement" || state.role === "supply") && live.busy === null;
  const expected = o.expectedBy ? new Date(o.expectedBy).toLocaleDateString("en-CA", { month: "short", day: "numeric" }) : "–";
  const headline = placedLine(o);
  const escalateTo = o.orderStatus !== "delivered" && o.orderStatus !== "cancelled" ? ESCALATE_ORDER_TO[state.role] : undefined;
  return (
    <div className="callout info order-track" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
      <div className="row-between">
        <strong>
          Order {o.units} units · {o.from} → {o.to}
        </strong>
        <Status tone={ORDER_TONE[o.orderStatus ?? "approved"] ?? "neutral"}>{o.orderStatusText ?? o.orderStatus}</Status>
      </div>
      {headline && <span className="small"><Status tone={headline.tone}>{headline.label}</Status> {headline.text}</span>}
      <span className="small ink-2">
        Expected {expected} · {o.id}
      </span>
      <span className="small">
        {o.decision ? (
          <>
            <Origin by={o.decidedBy === "agent" ? "agent" : "rules"} label={o.decidedBy === "agent" ? "Order agent" : "Order rules"} compact />{" "}
            <Status tone={CHECK_TONE[o.decision] ?? "neutral"}>{o.status.split(":")[0]}</Status> {o.reason}
          </>
        ) : (
          <span className="muted">The order agent checks it this evening.</span>
        )}
      </span>
      {o.messageDraft && (
        <details>
          <summary className="small">Draft to {o.messageTo}</summary>
          <p className="small ink-2" style={{ whiteSpace: "pre-wrap" }}>{o.messageDraft}</p>
        </details>
      )}
      <Submissions order={o} />
      {(o.replies ?? []).map((r, i) => (
        <details key={i}>
          <summary className="small">
            {r.problems.length ? (
              <Status tone="crit">Needs a person</Status>
            ) : (
              <>
                <Origin by={r.readBy === "agent" ? "agent" : "rules"} label={r.readBy === "agent" ? "Order agent" : "Scripted reading"} compact />{" "}
                <Status tone={REPLY_TONE[r.kind ?? ""] ?? "neutral"}>{REPLY_WORD[r.kind ?? ""] ?? r.kind}</Status>
              </>
            )}{" "}
            Reply from {r.from}: {r.problems.length ? r.problems.join("; ") : r.summary}
          </summary>
          <p className="small ink-2" style={{ whiteSpace: "pre-wrap" }}>{r.text}</p>
          <p className="small muted">
            Received {new Date(r.receivedAt).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}. Supplier text is untrusted; code checked the reading before the order agent used it.
          </p>
        </details>
      ))}
      {/* The draft is the email channel's starting point; once SAP, EDI or a portal took the order it is not needed. */}
      {o.request && !(o.submissions ?? []).some((x) => x.status === "accepted") && (
        <details>
          <summary className="small">
            <Origin by={o.request.decidedBy === "agent" ? "agent" : "rules"} label={o.request.decidedBy === "agent" ? "Order agent" : "Order rules"} compact />{" "}
            Request to {o.request.to}: {o.request.subject}
          </summary>
          <p className="small ink-2" style={{ whiteSpace: "pre-wrap" }}>{o.request.body}</p>
          <p className="small muted">Drafted for procurement to send. Nothing is sent from here.</p>
        </details>
      )}
      {canRecord && (o.orderStatus === "approved" || o.orderStatus === "placed" || o.orderStatus === "in_transit" || o.orderStatus === "delayed") && (
        <div className="row" style={{ gap: 8 }}>
          {o.orderStatus === "approved" ? (
            <button className="btn" onClick={() => void live.recordOrder(o.id, "placed")}>Record as placed</button>
          ) : (
            <button className="btn" onClick={() => void live.recordOrder(o.id, "delivered")}>Record as received</button>
          )}
          <span className="small muted">Records what procurement did with the supplier.</span>
        </div>
      )}
      {escalateTo && <EscalateButton objectId={o.id} to={escalateTo} />}
    </div>
  );
}
