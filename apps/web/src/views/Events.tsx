import { useState } from "react";
import { timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import { Card, ChartGuide, Change, Empty, Meter, PageHead, SourceChip, Sources, Status } from "../components/ui";
import type { SupplyEvent } from "../lib/types";

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

export function Events({ openPlan }: { openPlan?: (eventId: string) => void } = {}) {
  const { state } = useStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const email = state.emails.find((e) => e.id === selectedId) ?? state.emails[0];
  const blocked = state.emails.filter((e) => e.outcome === "quarantined").length;

  return (
    <>
      <PageHead
        label="Alerts"
        title={state.live ? "Problems, and what raised them" : "What came in, and what the AI made of it"}
        lead={`An AI reads every supplier email and pulls out the facts: which drug, which order, and the new date. It can't change anything itself. ${blocked ? `${blocked} suspicious email${blocked > 1 ? "s were" : " was"} blocked and sent to a person.` : ""}`}
      />

      <div className="grid-2">
        {email ? (
          <section className="card">
            <header className="card-head">
              <div>
                <div className="label" style={{ marginBottom: 4 }}>
                  {email.from} · {timeLabel(email.atMin)}
                </div>
                <h3>{email.subject}</h3>
              </div>
              <Status tone={email.outcome === "accepted" ? "good" : "crit"}>{email.outcome === "accepted" ? "Accepted" : "Blocked"}</Status>
            </header>
            <div className="card-body stack" style={{ gap: 18 }}>
              <div>
                <div className="label" style={{ marginBottom: 8 }}>
                  1 · What the email says
                </div>
                <pre className="letter">{highlight(email.body, email.extraction.excerpt)}</pre>
                {email.extraction.excerpt && <p className="small muted" style={{ marginTop: 6 }}>Highlighted: the sentence the AI based its answer on.</p>}
              </div>

              <div>
                <div className="label" style={{ marginBottom: 8 }}>
                  2 · What the AI understood
                </div>
                {email.extraction.poRef ? (
                  <div className="read-out">
                    <div>
                      <div className="label">What happened</div>
                      <div className="v">{email.extraction.eventType}</div>
                    </div>
                    <div>
                      <div className="label">Drug</div>
                      <div className="v">{email.extraction.drug}</div>
                    </div>
                    <div>
                      <div className="label">Delivery date</div>
                      <div className="v">
                        <Change was={email.extraction.originalEta} now={email.extraction.newEta} />
                      </div>
                    </div>
                    <div>
                      <div className="label">Order · hospital</div>
                      <div className="v">
                        {email.extraction.qty} vials for {email.extraction.site} <SourceChip id={email.extraction.poRef} />
                      </div>
                    </div>
                  </div>
                ) : (
                  <p className="ink-2">No drug, order or date that matches anything we have on file.</p>
                )}
              </div>

              <div>
                <div className="row-between">
                  <div className="label">3 · How sure the AI is</div>
                  <b className="num">{Math.round(email.extraction.confidence * 100)}%</b>
                </div>
                <ChartGuide
                  open={false}
                  items={[
                    { mark: email.extraction.confidence >= 0.85 ? "bar-good" : "bar-crit", text: <>The filled bar is <b>how sure the AI is</b> that it read the email correctly (0 to 100%).</> },
                    { mark: "tick", text: <>The dark mark is the <b>85% line</b>. Above it, the reading is used automatically. Below it, or if anything looks odd, a person must check it first.</> },
                  ]}
                  example={<>This email scored <b>{Math.round(email.extraction.confidence * 100)}%</b>, so it was {email.extraction.flags.length > 0 ? "held back for a person to review" : "accepted"}.</>}
                />
                <Meter value={email.extraction.confidence} threshold={0.85} thresholdLabel="85%: accepted automatically" />
                {email.extraction.flags.length > 0 ? (
                  <div className="callout crit">
                    <span className="ico" aria-hidden>
                      ✕
                    </span>
                    <div>
                      <strong>Blocked and sent to a person to review</strong>
                      <ul>
                        {email.extraction.flags.map((f) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                    </div>
                  </div>
                ) : (
                  <div className="callout good">
                    <span className="ico" aria-hidden>
                      ✓
                    </span>
                    <div>
                      <strong>Accepted and turned into an alert</strong>
                      <span className="ink-2">Matched an open order by drug name and strength. Stock numbers were recalculated straight away.</span>
                    </div>
                  </div>
                )}
              </div>
            </div>
            <footer className="card-foot">
              <span className="small muted">The AI only returns a checked summary. It cannot edit data or follow instructions inside an email.</span>
              <SourceChip id={email.id} />
            </footer>
          </section>
        ) : (
          <Empty title="No emails yet">Supplier emails appear here as they arrive.</Empty>
        )}

        <div className="stack">
          <section className="card">
            <header className="card-head">
              <div>
                <div className="label" style={{ marginBottom: 4 }}>
                  Inbox
                </div>
                <h3>Supplier emails</h3>
              </div>
            </header>
            <div className="mail-list" style={{ marginTop: 10 }}>
              {state.emails.map((e) => (
                <button key={e.id} className={`mail ${e.id === email?.id ? "active" : ""}`} onClick={() => setSelectedId(e.id)}>
                  <strong>{e.subject}</strong>
                  <Status tone={e.outcome === "accepted" ? "good" : "crit"}>{e.outcome === "accepted" ? "Accepted" : "Blocked"}</Status>
                  <span className="meta">
                    {e.from.split("@")[1]} · {timeLabel(e.atMin)}
                  </span>
                </button>
              ))}
            </div>
          </section>

          <Card label={state.live ? "Problems" : "Alerts"} title={state.live ? "What the platform is dealing with" : "What the platform is tracking"}>
            <div className="stack" style={{ gap: 14 }}>
              {state.events.map((e) => (
                <div key={e.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <div className="row-between">
                    <strong>{e.title}</strong>
                    {state.live && e.planState ? (
                      <Status tone={e.planState.includes("approved") ? "good" : e.planState.includes("waiting") ? "warn" : "crit"}>{e.planState}</Status>
                    ) : (
                      <Status tone={EVENT_STATUS[e.status].tone}>{EVENT_STATUS[e.status].text}</Status>
                    )}
                  </div>
                  {state.live && e.evidence?.length ? (
                    <ul className="small ink-2" style={{ margin: 0, paddingLeft: 18 }}>
                      {e.evidence.map((ev, i) => (
                        <li key={i}>
                          <b>{EVIDENCE_WORDS[ev.source] ?? ev.source}:</b> {ev.summary || ev.ref}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <span className="small ink-2">{e.summary}</span>
                  )}
                  <span className="meta">
                    {SOURCE_WORDS[e.source]} · {timeLabel(e.atMin)}
                    {e.confidence < 1 && ` · ${Math.round(e.confidence * 100)}% sure`}
                  </span>
                  <Sources ids={[e.id, e.sourceRef]} label="Records" />
                  {openPlan && state.plans.some((p) => p.eventId === e.id) && (
                    <button className="link small" style={{ alignSelf: "flex-start" }} onClick={() => openPlan(e.id)}>
                      See its plan →
                    </button>
                  )}
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}

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
