import { useState } from "react";
import { Card, ChartGuide, Origin, PageHead, SourceChip, Status, TierStatus, formatDays, type OriginKind, type Tone } from "../components/ui";
import "./signal-audit.css";
import { Icon, type IconName } from "../components/icons";
import { DRUGS, SITES, dayLabel, drugById, siteName, timeLabel } from "../data/mock";
import { lineCoverage, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Actor, AuditEntry, Plan } from "../lib/types";

type Filter = "all" | "people" | "agent" | "solver" | "system";

const FILTERS: { id: Filter; label: string; match: (a: Actor) => boolean }[] = [
  { id: "all", label: "Everything", match: () => true },
  { id: "people", label: "People", match: (a) => a === "pharmacist" || a === "supply" || a === "procurement" || a === "auditor" },
  { id: "agent", label: "AI", match: (a) => a === "agent" },
  { id: "solver", label: "Planner", match: (a) => a === "solver" },
  { id: "system", label: "System", match: (a) => a === "system" },
];

const isHuman = (a: Actor) => FILTERS[1].match(a);

function who(e: AuditEntry) {
  if (isHuman(e.actor)) return e.actorName;
  if (e.actor === "agent") return `AI (${e.actorName.replace("-", " ")})`;
  if (e.actor === "solver") return "Planner";
  return `System (${e.actorName.replace("-", " ")})`;
}

/** Text in the circle beside each timeline entry (original cue): initials for people, AI, P(lanner), S(ystem). */
function dot(e: AuditEntry) {
  if (isHuman(e.actor)) return e.actorName.split(" ").map((p) => p[0]).join("");
  if (e.actor === "agent") return "AI";
  if (e.actor === "solver") return "P";
  return "S";
}

export function History() {
  const { state } = useStore();
  const [filter, setFilter] = useState<Filter>("all");
  const rule = FILTERS.find((f) => f.id === filter)!;
  const rows = state.audit.filter((a) => rule.match(a.actor));

  // Group entries that happened at the same minute, so each time label appears once.
  const groups: { at: number; items: AuditEntry[] }[] = [];
  for (const r of rows) {
    const g = groups[groups.length - 1];
    if (g && g.at === r.atMin) g.items.push(r);
    else groups.push({ at: r.atMin, items: [r] });
  }

  const humans = state.audit.filter((a) => isHuman(a.actor)).length;
  const ai = state.audit.filter((a) => a.actor === "agent").length;

  return (
    // `.sa-page` scopes this page's styles (views/signal-audit.css) so no other page changes.
    <div className="sa-page">
      <PageHead
        label="Audit Trail"
        title={state.live ? "Audit Trail" : "Everything that happened, in order"}
        lead={`${state.audit.length} entries · ${humans} by people · ${ai} by the AI · the rest by the planner and system`}
      />

      <Stories />

      <div className="grid-2">
        <section className="card">
          <header className="card-head" style={{ alignItems: "center" }}>
            <div>
              <h3>
                <Icon name="clock" size={18} /> Timeline
              </h3>
            </div>
            <div className="filters" role="group" aria-label="Filter by who did it">
              {FILTERS.map((f) => (
                <button key={f.id} className={f.id === filter ? "active" : ""} onClick={() => setFilter(f.id)} aria-pressed={f.id === filter}>
                  {f.label} <span className="num">{state.audit.filter((a) => f.match(a.actor)).length}</span>
                </button>
              ))}
            </div>
          </header>
          <div style={{ marginTop: 12 }}>
            <ChartGuide
              open={false}
              items={[
                { mark: "tick", text: <>Read from <b>top to bottom</b>: newest first. Each grey time label groups what happened in that minute.</> },
                { mark: "dot", text: <>The circle says <b>who did it</b>: initials (dark) = a person, <b>AI</b> (yellow) = the AI, <b>P</b> = the planner, <b>S</b> = the system.</> },
              ]}
              example={<>Click a record ID chip to open the exact record that entry refers to.</>}
            />
          </div>
          <ol className="timeline" style={{ marginTop: 8, paddingBottom: 10 }}>
            {groups.length === 0 && <li className="small muted tl-empty">No entries for this filter.</li>}
            {groups.map((g) => (
              <li key={`${g.at}-${g.items[0].id}`} className="tl-group">
                <div className="tl-time">
                  <Icon name="clock" size={13} /> {timeLabel(g.at)}
                </div>
                {g.items.map((e) => (
                  <div key={e.id} className="tl-item">
                    <span className={`who-dot ${isHuman(e.actor) ? "human" : e.actor === "agent" ? "agent" : ""}`} aria-hidden>
                      {dot(e)}
                    </span>
                    <div className="tl-body">
                      <div className="what">
                        {state.live && <Origin by={isHuman(e.actor) ? "human" : e.actor === "agent" ? "agent" : "rules"} compact />}
                        <b>{who(e)}</b>
                        <span className="tl-action">{e.action}</span>
                        <span className="tl-object">{e.object}</span>
                      </div>
                      {e.detail && <p className="detail">{e.detail}.</p>}
                      {e.refs.length > 0 && (
                        <div className="chips">
                          {e.refs.map((r) => (
                            <SourceChip key={r} id={r} />
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </li>
            ))}
          </ol>
        </section>

        <Trace />
      </div>
    </div>
  );
}

function Trace() {
  const { state } = useStore();
  // Start on the first line that has an event (live data has no "vaso" / "B").
  const start = state.stock.find((l) => state.events.some((e) => e.drugId === l.drugId && e.siteId === l.siteId)) ?? state.stock[0];
  const [drugId, setDrugId] = useState(state.live ? start?.drugId ?? "" : "vaso");
  const [siteId, setSiteId] = useState(state.live ? start?.siteId ?? "" : "B");
  const line = state.stock.find((l) => l.drugId === drugId && l.siteId === siteId) ?? start;
  if (!line) return null;
  const drug = drugById(drugId);
  const days = lineCoverage(line);
  const usageRef = drugId === "vaso" && siteId === "A" && line.dailyUsage === 10 ? "SITE-RPT-A-77" : line.usageRef;
  const delayEvent = state.events.find((e) => e.drugId === drugId && e.siteId === siteId && e.kind === "delay");
  const approved = state.plans.find((p) => p.status === "approved" && p.drugId === drugId);
  const projectedLine = withApprovedPlans(state.stock, state.plans, state.events).find((l) => l.drugId === drugId && l.siteId === siteId)!;
  const projectedDays = lineCoverage(projectedLine);

  return (
    <Card label="Data lineage" title={state.live ? "Trace a figure to its source" : "Where does this number come from?"}>
      <div className="row" style={{ marginBottom: 16 }}>
        <select className="input" value={drugId} onChange={(e) => setDrugId(e.target.value)} aria-label="Drug">
          {DRUGS.map((d) => (
            <option key={d.id} value={d.id}>
              {d.name}
            </option>
          ))}
        </select>
        <select className="input" value={siteId} onChange={(e) => setSiteId(e.target.value)} aria-label="Hospital">
          {SITES.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      <ChartGuide
        open={false}
        items={[
          { mark: "tick", text: <>The <b>big number at the top</b> is the result. Each step below it is an ingredient of that number.</> },
          { mark: "dot", text: <>Follow the chain down to see <b>what it was calculated from</b> and which record each part came from.</> },
        ]}
        example={<>Stock on hand ÷ daily use, plus any deliveries due = days of stock.</>}
      />
      <div className="trace-chain">
        {/* The result sits at the top; every step below is an ingredient of it. */}
        <div className="trace-step root">
          <span className="node">
            <Icon name="check" size={12} />
          </span>
          <div>
            <div className="row-between">
              <span className="num trace-result">
                {formatDays(days)} days
              </span>
              <TierStatus tier={tierFor(days, line.safetyDays)} />
            </div>
            <div className="s">Calculated in code by the risk module, not by AI.</div>
            {approved && Math.abs(projectedDays - days) > 0.05 && (
              <div className="s">
                After approved plan <SourceChip id={approved.id} />: <b style={{ color: "var(--ink)" }}>{formatDays(projectedDays)} days</b>
              </div>
            )}
          </div>
        </div>
        <Step icon="box" text={`${line.onHand} ${drug.unit} on hand`} note="Stock count" refs={[line.inventoryRef]} />
        <Step icon="hospital" text={`÷ ${line.dailyUsage} used per day`} note="Usage record" refs={[usageRef]} />
        {line.orders.map((o) => (
          <div key={o.id}>
            <Step icon="truck" text={`+ ${o.qty} arriving ${dayLabel(o.etaDay)}`} note={o.status === "delayed" ? `Order ${o.id}, originally due ${dayLabel(o.originalEtaDay ?? o.etaDay)}` : `Order ${o.id}`} refs={[o.id]} />
            {o.status === "delayed" && delayEvent && (
              <div className="trace-sub">
                <Step icon="alert" text="Date changed by a supplier alert" note="Alert" refs={[delayEvent.id]} />
                <Step icon="mail" text={`Read from the supplier's email, ${Math.round(delayEvent.confidence * 100)}% sure`} note="Original email" refs={[delayEvent.sourceRef]} />
              </div>
            )}
          </div>
        ))}
        <Step icon="pill" text={`Drug identity: ${drug.name}`} note="Health Canada drug database and critical drug list" refs={[drug.dpdRef, ...(drug.criticalList ? ["CDL-2026-V3"] : [])]} />
      </div>
      <p className="small muted" style={{ marginTop: 4 }}>
        {siteName(siteId)} · click any record ID to open it.
      </p>
    </Card>
  );
}

/** One link in the lineage chain: what it adds to the number, and the record it came from. */
function Step({ text, note, refs, icon = "link" }: { text: string; note: string; refs: string[]; icon?: IconName }) {
  return (
    <div className="trace-step">
      <span className="node">
        <Icon name={icon} size={12} />
      </span>
      <div>
        <div className="t">{text}</div>
        <div className="s">
          {note}
          {refs.map((r) => (
            <SourceChip key={r} id={r} />
          ))}
        </div>
      </div>
    </div>
  );
}

/* ---------- Response status: one row per approved plan, then the steps of the selected one ---------- */

type Stage = "detect" | "decide" | "act" | "follow";

const STAGE_WORD: Record<Stage, string> = { detect: "Detected", decide: "Decided", act: "Placed", follow: "Follow-up" };

function stageOf(e: AuditEntry): Stage | null {
  const a = e.action;
  if (a === "Order orchestration" || a === "Notification read") return null; // the summary repeats the attempts
  if (a.startsWith("Signal pipeline") || a === "Requested a plan") return "detect";
  if (a === "Approved" || a === "Rejected") return "decide";
  if (/^Order (placed|email ready|rejected|not submitted|escalated to procurement|held|released|request drafted)/.test(a)) return "act";
  return "follow";
}

const AGENT_NAME: Record<string, string> = {
  "stock-watch": "Stock watch",
  "signal-agent": "Assessment agent",
  "order-orchestrator": "Order orchestrator",
  "order-agent": "Order agent",
  "followup-agent": "Follow-up agent",
};

const PIPELINE: Record<string, string> = {
  plan_generated: "Alert raised, plan options built",
  needs_review: "Sent for review",
  ignore: "No action needed",
};

function actorLabel(e: AuditEntry) {
  return isHuman(e.actor) ? e.actorName : AGENT_NAME[e.actorName] ?? e.actorName.replace(/-/g, " ");
}

function actionLabel(e: AuditEntry) {
  if (e.action.startsWith("Signal pipeline: ")) {
    const step = e.action.slice("Signal pipeline: ".length);
    return PIPELINE[step] ?? step.replace(/_/g, " ");
  }
  return e.action;
}

function originOf(e: AuditEntry): OriginKind {
  return isHuman(e.actor) ? "human" : e.actor === "agent" ? "agent" : "rules";
}

function planEntries(audit: AuditEntry[], plan: Plan) {
  const run = plan.solverRun || plan.id;
  return audit.filter((e) => stageOf(e) && (e.object === run || e.object.startsWith(`${run}-`) || e.refs.includes(run))).slice().reverse();
}

function planStatus(plan: Plan, entries: AuditEntry[]): [Tone, string] {
  if ((plan.orders?.length ?? 0) === 0) return ["good", "Transfer approved"];
  if (entries.some((e) => /^Order (email ready|escalated|held)/.test(e.action))) return ["warn", "Waiting on a person"];
  if (entries.some((e) => e.action.startsWith("Order placed"))) return ["good", "Order placed"];
  return ["neutral", "Approved"];
}

/** The table cell for one stage: who did it (mark + name) and how many entries. */
function StageCell({ items }: { items: AuditEntry[] }) {
  if (items.length === 0) return <span className="muted">—</span>;
  const last = items[items.length - 1];
  return (
    <span className="stage-cell">
      <Origin by={originOf(last)} compact />
      <span>{actorLabel(last)}</span>
      {items.length > 1 && <span className="muted small">+{items.length - 1}</span>}
    </span>
  );
}

function Stories() {
  const { state } = useStore();
  const approved = state.plans.filter((p) => p.status === "approved");
  const [selected, setSelected] = useState<string | null>(null);
  if (!state.live || approved.length === 0) return null;

  const rows = approved.map((plan) => {
    const entries = planEntries(state.audit, plan);
    return { plan, entries, status: planStatus(plan, entries) };
  });
  const current = rows.find((r) => r.plan.id === selected) ?? rows[0];
  const placed = rows.filter((r) => r.status[1] === "Order placed").length;
  const waiting = rows.filter((r) => r.status[1] === "Waiting on a person").length;

  return (
    <div className="stack response-status" style={{ gap: 16 }}>
      <Card label={`Approved plans · ${rows.length} · ${placed} order${placed === 1 ? "" : "s"} placed · ${waiting} waiting on a person`} title="Response status">
        <div className="tablewrap">
          <table className="table detail-table">
            <thead>
              <tr>
                <th>Drug</th>
                <th>Approved option</th>
                <th>Detected</th>
                <th>Decided</th>
                <th>Placed</th>
                <th>Follow-up</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(({ plan, entries, status }) => {
                const at = (s: Stage) => entries.filter((e) => stageOf(e) === s);
                return (
                  <tr
                    key={plan.id}
                    className={plan.id === current.plan.id ? "selected" : ""}
                    onClick={() => setSelected(plan.id)}
                    aria-selected={plan.id === current.plan.id}
                  >
                    <td>
                      <b>{drugById(plan.drugId).name}</b>
                    </td>
                    <td className="option-cell">{plan.title}</td>
                    <td><StageCell items={at("detect")} /></td>
                    <td><StageCell items={at("decide")} /></td>
                    <td>{(plan.orders?.length ?? 0) === 0 ? <span className="muted">No order</span> : <StageCell items={at("act")} />}</td>
                    <td><StageCell items={at("follow")} /></td>
                    <td><Status tone={status[0]}>{status[1]}</Status></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>

      <Card label="Response detail · oldest first" title={drugById(current.plan.drugId).name} actions={<Status tone={current.status[0]}>{current.status[1]}</Status>}>
        <div className="tablewrap">
          <table className="table detail-table">
            <thead>
              <tr>
                <th>Step</th>
                <th>Done by</th>
                <th>Action</th>
                <th>Detail</th>
                <th>Time</th>
              </tr>
            </thead>
            <tbody>
              {current.entries.map((e) => (
                <tr key={e.id}>
                  <td className="nowrap">{STAGE_WORD[stageOf(e)!]}</td>
                  <td className="nowrap">
                    <span className="stage-cell">
                      <Origin by={originOf(e)} compact />
                      <span>{actorLabel(e)}</span>
                    </span>
                  </td>
                  <td>{actionLabel(e)}</td>
                  <td className="detail-cell">{e.detail || <span className="muted">—</span>}</td>
                  <td className="nowrap muted">{timeLabel(e.atMin)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
