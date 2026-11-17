import { useState } from "react";
import { Card, ChartGuide, PageHead, SourceChip, TierStatus, formatDays } from "../components/ui";
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

function dot(e: AuditEntry) {
  if (isHuman(e.actor)) return e.actorName.split(" ").map((p) => p[0]).join("");
  if (e.actor === "agent") return "AI";
  if (e.actor === "solver") return "P";
  return "S";
}

export function Audit() {
  const { state } = useStore();
  const [filter, setFilter] = useState<Filter>("all");
  const rule = FILTERS.find((f) => f.id === filter)!;
  const rows = state.audit.filter((a) => rule.match(a.actor));

  // Group entries that happened at the same minute.
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
        label="History"
        title="Everything that happened, in order"
        lead={`${state.audit.length} entries so far: ${humans} by people, ${ai} by the AI, the rest by the planner and system. Entries can only be added, never changed.`}
      />

      <div className="grid-2">
        <section className="card">
          <header className="card-head" style={{ alignItems: "center" }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>
                Newest first
              </div>
              <h3>Timeline</h3>
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
            {groups.map((g) => (
              <li key={`${g.at}-${g.items[0].id}`} className="tl-group">
                <div className="tl-time">{timeLabel(g.at)}</div>
                {g.items.map((e) => (
                  <div key={e.id} className="tl-item">
                    <span className={`who-dot ${isHuman(e.actor) ? "human" : e.actor === "agent" ? "agent" : ""}`} aria-hidden>
                      {dot(e)}
                    </span>
                    <div>
                      <div className="what">
                        <b>{who(e)}</b> · {e.action.toLowerCase()} <span className="mono muted">{e.object}</span>
                      </div>
                      <div className="detail">{e.detail}.</div>
                      <div className="chips">
                        {e.refs.map((r) => (
                          <SourceChip key={r} id={r} />
                        ))}
                      </div>
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
    <Card label="Trace a number" title="Where does this number come from?">
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
        <div className="trace-step root">
          <span className="node" />
          <div>
            <div className="row-between">
              <span style={{ fontSize: 22, fontWeight: 600 }} className="num">
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
        <Step text={`${line.onHand} ${drug.unit} on hand`} note="Stock count" refs={[line.inventoryRef]} />
        <Step text={`÷ ${line.dailyUsage} used per day`} note="Usage record" refs={[usageRef]} />
        {line.orders.map((o) => (
          <div key={o.id}>
            <Step text={`+ ${o.qty} arriving ${dayLabel(o.etaDay)}`} note={o.status === "delayed" ? `Order ${o.id}, originally due ${dayLabel(o.originalEtaDay ?? o.etaDay)}` : `Order ${o.id}`} refs={[o.id]} />
            {o.status === "delayed" && delayEvent && (
              <div className="trace-sub">
                <Step text="Date changed by a supplier alert" note="Alert" refs={[delayEvent.id]} />
                <Step text={`Read from the supplier's email, ${Math.round(delayEvent.confidence * 100)}% sure`} note="Original email" refs={[delayEvent.sourceRef]} />
              </div>
            )}
          </div>
        ))}
        <Step text={`Drug identity: ${drug.name}`} note="Health Canada drug database and critical drug list" refs={[drug.dpdRef, ...(drug.criticalList ? ["CDL-2026-V3"] : [])]} />
      </div>
      <p className="small muted" style={{ marginTop: 4 }}>
        {siteName(siteId)} · click any record ID to open it.
      </p>
    </Card>
  );
}

function Step({ text, note, refs }: { text: string; note: string; refs: string[] }) {
  return (
    <div className="trace-step">
      <span className="node" />
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
