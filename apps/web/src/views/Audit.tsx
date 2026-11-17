import { useState } from "react";
import { Card, ChartGuide, InfoNotes, PageHead, SourceChip, TierStatus, formatDays } from "../components/ui";
import { Icon, type IconName } from "../components/icons";
import { DRUGS, SITES, dayLabel, drugById, siteName, timeLabel } from "../data/mock";
import { lineCoverage, tierFor, withApprovedPlans } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Actor, AuditEntry } from "../lib/types";

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

/** Icon and colour class for the circle beside each timeline entry. */
function actorMark(e: AuditEntry): { icon: IconName; cls: string } {
  if (isHuman(e.actor)) return { icon: "user", cls: "human" };
  if (e.actor === "agent") return { icon: "sparkle", cls: "agent" };
  if (e.actor === "solver") return { icon: "route", cls: "solver" };
  return { icon: "cog", cls: "system" };
}

/* The four kinds of actor, in the order work flows through the platform.
   Drawn as a diagram at the top of the page; each node also filters the timeline. */
const FLOW: { id: Filter; icon: IconName; title: string; note: string }[] = [
  { id: "system", icon: "cog", title: "System", note: "Brings data in" },
  { id: "agent", icon: "sparkle", title: "AI", note: "Reads and explains" },
  { id: "solver", icon: "route", title: "Planner", note: "Checks and ranks plans" },
  { id: "people", icon: "user", title: "People", note: "Make every decision" },
];

export function Audit() {
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
    <>
      <PageHead
        label="Audit Trail"
        title={state.live ? "Audit Trail" : "Everything that happened, in order"}
        lead={`${state.audit.length} entries · ${humans} by people · ${ai} by the AI · the rest by the planner and system`}
      />

      {/* Diagram: who acts at each stage, with how many entries each made. Click a stage to filter. */}
      <ol className="actor-flow" aria-label="Who did what">
        {FLOW.map((f) => {
          const n = state.audit.filter((a) => FILTERS.find((x) => x.id === f.id)!.match(a.actor)).length;
          return (
            <li key={f.id}>
              <button className={`actor-node ${f.id} ${filter === f.id ? "on" : ""}`} aria-pressed={filter === f.id} onClick={() => setFilter(filter === f.id ? "all" : f.id)}>
                <span className="actor-ico">
                  <Icon name={f.icon} size={20} />
                </span>
                <span className="actor-text">
                  <span className="actor-title">{f.title}</span>
                  <span className="actor-note">{f.note}</span>
                </span>
                <span className="actor-count num">{n}</span>
              </button>
            </li>
          );
        })}
      </ol>

      <InfoNotes
        items={[
          { icon: "book", title: "Append-only", text: "Entries can only be added, never edited or removed." },
          { icon: "clock", title: "Newest first", text: "Entries in the same minute are grouped under one time." },
          { icon: "link", title: "Trace any number", text: "Pick a drug and hospital on the right to see every record behind its days of stock." },
        ]}
      />

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
                    <span className={`who-dot ${actorMark(e).cls}`} aria-hidden>
                      <Icon name={actorMark(e).icon} size={14} />
                    </span>
                    <div className="tl-body">
                      <div className="what">
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
    </>
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
