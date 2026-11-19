import { useState, type ReactNode } from "react";
import { SITES, timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import { Card, ChartGuide, Change, Empty, Meter, Origin, PageHead, SourceChip, Sources, Status } from "../components/ui";
import { InfoNotes } from "../components/PageNotes";
import "./signal-audit.css";
import { Icon, type IconName } from "../components/icons";
import type { AuditEntry, Email, Site, SupplyEvent } from "../lib/types";

/* ---------------------------------------------------------------
   Signal Review: every supplier email the platform received, what
   the AI read from it, and what was done about it afterwards.
---------------------------------------------------------------- */

const EVENT_STATUS: Record<SupplyEvent["status"], { tone: "crit" | "warn" | "good"; text: string }> = {
  open: { tone: "crit", text: "Needs action" },
  monitoring: { tone: "warn", text: "Watching" },
  mitigated: { tone: "good", text: "Handled" },
};

const SOURCE_WORDS: Record<SupplyEvent["source"], string> = {
  "supplier-email": "Supplier email",
  "hc-shortage-api": "Health Canada report",
  "site-report": "Hospital report",
};

const EVIDENCE_WORDS: Record<string, string> = {
  stock_watch: "Stock watch",
  supplier_email: "Supplier email",
  shortage_report: "Health Canada report",
  manual: "Supply manager",
  site_report: "Hospital report",
};

// Confidence at or above this is accepted without a person checking it first.
const AUTO_ACCEPT = 0.85;

type InboxFilter = "all" | "accepted" | "quarantined";

/** The alert this email turned into, if any (matched by its source record or listed evidence). */
function eventFor(email: Email, events: SupplyEvent[]) {
  return events.find((e) => e.sourceRef === email.id || e.evidence?.some((ev) => ev.ref === email.id));
}

/** Audit entries about this email, or about the alert it created: the "what happened next" log. */
function actionsFor(email: Email, event: SupplyEvent | undefined, audit: AuditEntry[]) {
  const ids = new Set([email.id, ...(event ? [event.id] : [])]);
  return audit.filter((a) => ids.has(a.object) || a.refs.some((r) => ids.has(r)));
}

/** "customer.service@northbridge-pharma.example" → "Northbridge Pharma" for a friendlier sender line. */
function senderName(from: string) {
  const domain = from.split("@")[1] ?? from;
  const word = domain.split(".")[0] ?? domain;
  return word
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/** Two-letter avatar for the sender. */
const initials = (from: string) =>
  senderName(from)
    .split(" ")
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

export function SignalReview({ openPlan }: { openPlan?: (eventId: string) => void } = {}) {
  const { state } = useStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<InboxFilter>("all");

  const blocked = state.emails.filter((e) => e.outcome === "quarantined").length;
  const accepted = state.emails.length - blocked;
  const inbox = state.emails.filter((e) => filter === "all" || e.outcome === filter);
  // Keep the open email if it is still in the list; otherwise fall back to the first one shown.
  const email = inbox.find((e) => e.id === selectedId) ?? inbox[0] ?? state.emails.find((e) => e.id === selectedId);
  const event = email ? eventFor(email, state.events) : undefined;

  return (
    // `.sa-page` scopes this page's styles (views/signal-audit.css) so no other page changes.
    <div className="sa-page">
      <PageHead
        label="Signal Review"
        title={state.live ? "Signal Review" : "What came in, and what the AI made of it"}
        lead={`${state.emails.length} supplier email${state.emails.length === 1 ? "" : "s"} received · ${accepted} accepted · ${blocked} blocked and sent to a person`}
      />

      {/* Three short notes instead of one long paragraph: what this page is and how to use it. */}
      <InfoNotes
        items={[
          { icon: "mail-open", title: "Read the original", text: "Each supplier email is shown exactly as it arrived. The sentence the AI relied on is highlighted." },
          { icon: "shield", title: "Checked before use", text: `The AI only returns a summary. It is used automatically at ${Math.round(AUTO_ACCEPT * 100)}% confidence or more; anything odd is blocked and sent to a person.` },
          { icon: "route", title: "See what happened next", text: "Below each email: the steps the platform took and the hospitals the signal reached." },
        ]}
      />

      {state.emails.length === 0 ? (
        <Empty title="No emails yet">Supplier emails appear here as they arrive.</Empty>
      ) : (
        <div className="mailbox">
          {/* ---- Left: inbox ---- */}
          <section className="card inbox" aria-label="Supplier inbox">
            <header className="inbox-head">
              <h3>
                <Icon name="mail" size={18} /> Inbox
              </h3>
              <div className="filters" role="group" aria-label="Filter emails">
                {(
                  [
                    ["all", "All", state.emails.length],
                    ["accepted", "Accepted", accepted],
                    ["quarantined", "Blocked", blocked],
                  ] as const
                ).map(([id, label, n]) => (
                  <button key={id} className={filter === id ? "active" : ""} aria-pressed={filter === id} onClick={() => setFilter(id)}>
                    {label} <span className="num">{n}</span>
                  </button>
                ))}
              </div>
            </header>
            <div className="mail-list">
              {inbox.map((e) => (
                <button key={e.id} className={`mail ${e.id === email?.id ? "active" : ""} ${e.outcome}`} onClick={() => setSelectedId(e.id)}>
                  <span className={`avatar ${e.outcome}`} aria-hidden>
                    {e.outcome === "accepted" ? initials(e.from) : <Icon name="shield-x" size={16} />}
                  </span>
                  <span className="mail-text">
                    <span className="mail-from">{senderName(e.from)}</span>
                    <strong>{e.subject}</strong>
                    <span className="meta">{timeLabel(e.atMin)}</span>
                  </span>
                  {/* Original status cue: green ✓ Accepted / red ✕ Blocked. */}
                  <Status tone={e.outcome === "accepted" ? "good" : "crit"}>{e.outcome === "accepted" ? "Accepted" : "Blocked"}</Status>
                </button>
              ))}
              {inbox.length === 0 && <p className="small muted inbox-empty">Nothing in this view.</p>}
            </div>
          </section>

          {/* ---- Right: the open email ---- */}
          {email && <Reader email={email} live={!!state.live} />}
        </div>
      )}

      {email && (
        <div className="grid-2 even">
          <Card label="What happened next" title="Actions taken on this email">
            <ActionFlow email={email} event={event} hasPlan={!!event && state.plans.some((p) => p.eventId === event.id)} />
            <ActionLog entries={actionsFor(email, event, state.audit)} />
            {event && openPlan && state.plans.some((p) => p.eventId === event.id) && (
              <button className="link small" onClick={() => openPlan(event.id)}>
                {state.live ? "View the plan for this alert →" : "See its plan →"}
              </button>
            )}
          </Card>
          <Card label="Signal map" title="Where this signal went">
            <SignalMap email={email} event={event} sites={SITES} />
          </Card>
        </div>
      )}

      {/* Every alert and the evidence behind it, as a grid of small cards. */}
      <Card label={state.live ? "Evidence" : "Alerts"} title={state.live ? "Evidence by alert" : "What the platform is tracking"}>
        {state.events.length === 0 ? (
          <p className="ink-2">No alerts yet.</p>
        ) : (
          <div className="evidence-grid">
            {state.events.map((e) => (
              <div key={e.id} className="evidence-card">
                <div className="row-between">
                  <strong>{e.title}</strong>
                  {state.live && e.planState ? (
                    <Status tone={e.planState.includes("approved") ? "good" : e.planState.includes("waiting") ? "warn" : "crit"}>{e.planState}</Status>
                  ) : (
                    <Status tone={EVENT_STATUS[e.status].tone}>{EVENT_STATUS[e.status].text}</Status>
                  )}
                </div>
                {state.live && e.evidence?.length ? (
                  <ul className="evidence-list">
                    {e.evidence.map((ev, i) => (
                      <li key={i}>
                        <b>{EVIDENCE_WORDS[ev.source] ?? ev.source}:</b> {ev.summary || ev.ref}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="small ink-2">{e.summary}</p>
                )}
                <span className="meta">
                  {SOURCE_WORDS[e.source]} · {timeLabel(e.atMin)}
                  {e.confidence < 1 && ` · ${Math.round(e.confidence * 100)}% confidence`}
                </span>
                <Sources ids={[e.id, e.sourceRef]} label="Records" />
                {openPlan && state.plans.some((p) => p.eventId === e.id) && (
                  <button className="link small" style={{ alignSelf: "flex-start" }} onClick={() => openPlan(e.id)}>
                    {state.live ? "View plan →" : "See its plan →"}
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------
   Reader: the email itself, set like a letter (larger type, wider
   line spacing, soft paper background), then the AI's reading.
---------------------------------------------------------------- */
function Reader({ email, live }: { email: Email; live: boolean }) {
  const x = email.extraction;
  const ok = email.outcome === "accepted";
  // Only show the facts the AI actually found (live data has fewer fields than the demo).
  const facts: { icon: IconName; label: string; value: ReactNode }[] = [
    { icon: "alert", label: "What happened", value: x.eventType },
    { icon: "pill", label: "Drug", value: x.drug },
    ...(x.newEta ? [{ icon: "calendar" as IconName, label: "Delivery date", value: x.originalEta ? <Change was={x.originalEta} now={x.newEta} /> : x.newEta }] : []),
    ...(x.poRef ? [{ icon: "box" as IconName, label: "Order · hospital", value: <>{x.qty} vials for {x.site} <SourceChip id={x.poRef} /></> }] : []),
  ];

  return (
    <section className="card reader" aria-label="Open email">
      <header className="reader-head">
        <span className={`avatar lg ${email.outcome}`} aria-hidden>
          {ok ? initials(email.from) : <Icon name="shield-x" size={20} />}
        </span>
        <div className="reader-title">
          <h2>{email.subject}</h2>
          <div className="reader-meta">
            <span>
              <b>{senderName(email.from)}</b> &lt;{email.from}&gt;
            </span>
            <span>
              <Icon name="clock" size={14} /> {timeLabel(email.atMin)}
            </span>
          </div>
        </div>
        <Status tone={ok ? "good" : "crit"}>{ok ? "Accepted" : "Blocked"}</Status>
      </header>

      {/* The email body, as received. */}
      <article className="mail-paper">{highlight(email.body, x.excerpt)}</article>
      {x.excerpt && email.body.includes(x.excerpt) && (
        <p className="paper-note">
          <mark>Highlighted</mark> {live ? "source text for the extraction." : "the sentence the AI based its answer on."}
        </p>
      )}

      <div className="reader-section">
        <h4>
          <Icon name="sparkle" size={16} />{" "}
          {live ? (
            <>
              <Origin by="agent" label="Assessment agent" /> extraction
            </>
          ) : (
            "What the AI understood"
          )}
        </h4>
        {x.drug && x.drug !== "None identified" ? (
          <div className="read-out">
            {facts.map((f) => (
              <div key={f.label}>
                <div className="label">
                  <Icon name={f.icon} size={14} /> {f.label}
                </div>
                <div className="v">{f.value}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="ink-2">No drug, order or date that matches anything we have on file.</p>
        )}
      </div>

      <div className="reader-section">
        <div className="row-between">
          <h4>
            <Icon name="shield" size={16} /> {live ? "Confidence" : "How sure the AI is"}
          </h4>
          <b className="num confidence">{Math.round(x.confidence * 100)}%</b>
        </div>
        <ChartGuide
          open={false}
          items={[
            { mark: x.confidence >= AUTO_ACCEPT ? "bar-good" : "bar-crit", text: <>The filled bar is <b>how sure the AI is</b> that it read the email correctly (0 to 100%).</> },
            { mark: "tick", text: <>The dark mark is the <b>85% line</b>. Above it, the reading is used automatically. Below it, or if anything looks odd, a person must check it first.</> },
          ]}
          example={<>This email scored <b>{Math.round(x.confidence * 100)}%</b>, so it was {x.flags.length > 0 ? "held back for a person to review" : "accepted"}.</>}
        />
        <Meter value={x.confidence} threshold={AUTO_ACCEPT} thresholdLabel="85%: accepted automatically" />
        {x.flags.length > 0 ? (
          <div className="callout crit">
            <span className="ico" aria-hidden>
              ✕
            </span>
            <div>
              <strong>Blocked and sent to a person to review</strong>
              <ul>
                {x.flags.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </div>
          </div>
        ) : ok ? (
          <div className="callout good">
            <span className="ico" aria-hidden>
              ✓
            </span>
            <div>
              <strong>Accepted and turned into an alert</strong>
              <span className="ink-2">Matched an open order by drug name and strength. Stock numbers were recalculated straight away.</span>
            </div>
          </div>
        ) : null}
      </div>

      <footer className="card-foot">
        <span className="small muted">The AI cannot edit data or follow instructions written inside an email.</span>
        <SourceChip id={email.id} />
      </footer>
    </section>
  );
}

/* ---------------------------------------------------------------
   ActionFlow: a four-step diagram of the email's path through the
   platform. The last step depends on the outcome.
---------------------------------------------------------------- */
function ActionFlow({ email, event, hasPlan }: { email: Email; event?: SupplyEvent; hasPlan: boolean }) {
  const ok = email.outcome === "accepted";
  const steps: { icon: IconName; title: string; note: string; state: "done" | "stop" }[] = [
    { icon: "mail", title: "Received", note: timeLabel(email.atMin), state: "done" },
    { icon: "sparkle", title: "Read by AI", note: `${Math.round(email.extraction.confidence * 100)}% sure`, state: "done" },
    { icon: ok ? "shield" : "shield-x", title: "Safety check", note: ok ? "Passed" : `${email.extraction.flags.length || 1} issue${email.extraction.flags.length === 1 ? "" : "s"}`, state: ok ? "done" : "stop" },
    ok
      ? { icon: "alert", title: "Alert created", note: event ? (hasPlan ? "Plan proposed" : "Being assessed") : "Alert raised", state: "done" }
      : { icon: "user", title: "Sent to a person", note: "Blocked until reviewed", state: "stop" },
  ];
  return (
    <ol className="flow" aria-label="What happened to this email">
      {steps.map((s) => (
        <li key={s.title} className={`flow-step ${s.state}`}>
          <span className="flow-ico">
            <Icon name={s.icon} size={18} />
          </span>
          <span className="flow-title">{s.title}</span>
          <span className="flow-note">{s.note}</span>
        </li>
      ))}
    </ol>
  );
}

/** The audit entries for this email, as a short list under the diagram. */
function ActionLog({ entries }: { entries: AuditEntry[] }) {
  if (entries.length === 0) return <p className="small muted action-empty">No recorded actions yet for this email.</p>;
  return (
    <ul className="action-log">
      {entries.slice(0, 6).map((a) => (
        <li key={a.id}>
          <span className="action-time">{timeLabel(a.atMin)}</span>
          <span>
            <b>{a.action}</b> <span className="muted">· {a.object}</span>
            {a.detail && <span className="action-detail">{a.detail}</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

/* ---------------------------------------------------------------
   SignalMap: a schematic map of the signal's route.
   Supplier → screening → each hospital. The hospitals the alert
   affects are highlighted; a held email stops at screening.
---------------------------------------------------------------- */
function SignalMap({ email, event, sites }: { email: Email; event?: SupplyEvent; sites: Site[] }) {
  const ok = email.outcome === "accepted";
  // A site is "hit" if the alert names it (by id or name) or the extraction names it.
  const names = [event?.siteId, ...(event?.sites ?? []), email.extraction.site].filter(Boolean).map((s) => String(s).toLowerCase());
  const hit = (s: Site) => ok && names.some((n) => n === s.id.toLowerCase() || n === s.short.toLowerCase() || n === s.name.toLowerCase());

  // Show at most six hospitals; the rest are summarised as "+N more".
  const shown = sites.slice(0, 6);
  const extra = sites.length - shown.length;
  const W = 520;
  const rowH = 38;
  const H = Math.max(200, shown.length * rowH + 40);
  const mid = H / 2;
  const siteY = (i: number) => mid - ((shown.length - 1) * rowH) / 2 + i * rowH;
  const nodes = { supplier: { x: 64, y: mid }, screen: { x: 230, y: mid }, hosp: 372 };

  return (
    <div className="signal-map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Signal route: ${senderName(email.from)} to screening${ok ? `, then to ${shown.filter(hit).map((s) => s.short).join(", ") || "the network"}` : ", held for review"}.`}>
        {/* Edges first so the nodes sit on top. */}
        <path d={`M${nodes.supplier.x + 26},${mid} L${nodes.screen.x - 26},${mid}`} className="map-edge on" />
        {shown.map((s, i) => (
          <path
            key={s.id}
            d={`M${nodes.screen.x + 26},${mid} C${nodes.screen.x + 80},${mid} ${nodes.hosp - 70},${siteY(i)} ${nodes.hosp - 12},${siteY(i)}`}
            className={`map-edge ${hit(s) ? "hit" : ok ? "" : "off"}`}
          />
        ))}
        {!ok && <path d={`M${nodes.screen.x},${mid + 26} L${nodes.screen.x},${H - 30}`} className="map-edge stop" />}

        {/* Supplier */}
        <g transform={`translate(${nodes.supplier.x},${mid})`}>
          <circle r={24} className="map-node" />
          <g transform="translate(-10,-10)">
            <Icon name="truck" size={20} />
          </g>
          <text y={42} textAnchor="middle" className="map-label">
            {senderName(email.from)}
          </text>
        </g>

        {/* Screening */}
        <g transform={`translate(${nodes.screen.x},${mid})`}>
          <circle r={24} className={`map-node ${ok ? "good" : "crit"}`} />
          <g transform="translate(-10,-10)">
            <Icon name={ok ? "shield" : "shield-x"} size={20} />
          </g>
          <text y={-34} textAnchor="middle" className="map-label">
            AI + safety check
          </text>
        </g>
        {!ok && (
          <g transform={`translate(${nodes.screen.x},${H - 18})`}>
            <text textAnchor="middle" className="map-label crit">
              Blocked: sent to a person
            </text>
          </g>
        )}

        {/* Hospitals */}
        {shown.map((s, i) => (
          <g key={s.id} transform={`translate(${nodes.hosp},${siteY(i)})`} className={hit(s) ? "map-site hit" : "map-site"}>
            <rect x={-12} y={-14} width={140} height={28} rx={4} className="map-pill" />
            <g transform="translate(-4,-8)">
              <Icon name="hospital" size={16} />
            </g>
            <text x={18} y={4.5} className="map-label">
              {s.short}
            </text>
          </g>
        ))}
        {extra > 0 && (
          <text x={nodes.hosp} y={H - 6} className="map-label muted">
            +{extra} more hospitals
          </text>
        )}
      </svg>
      <div className="legend map-legend">
        <span>
          <i className="line hit" /> Affected hospital
        </span>
        <span>
          <i className="line" /> Rest of network
        </span>
        {!ok && (
          <span>
            <i className="line stop" /> Stopped at screening
          </span>
        )}
      </div>
    </div>
  );
}

/** Wraps the excerpt the AI relied on in <mark> so it stands out in the email body. */
function highlight(body: string, excerpt: string) {
  const i = excerpt ? body.indexOf(excerpt) : -1;
  if (i < 0) return body;
  return (
    <>
      {body.slice(0, i)}
      <mark>{excerpt}</mark>
      {body.slice(i + excerpt.length)}
    </>
  );
}
