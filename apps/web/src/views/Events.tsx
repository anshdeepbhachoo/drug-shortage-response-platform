import { useState } from "react";
import { timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import { Badge, Card, Empty, SourceChip } from "../components/ui";

export function Events() {
  const { state } = useStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const email = state.emails.find((e) => e.id === selectedId) ?? state.emails[0];

  return (
    <div className="view">
      <div className="split">
        <div className="stack">
          <Card title="Supplier inbox" actions={<Badge tone="synthetic">synthetic emails</Badge>}>
            <ul className="inbox">
              {state.emails.map((e) => (
                <li key={e.id}>
                  <button className={`inbox-item ${e.id === email?.id ? "active" : ""}`} onClick={() => setSelectedId(e.id)}>
                    <div className="row-between">
                      <strong>{e.subject}</strong>
                      <Badge tone={e.outcome === "accepted" ? "good" : "bad"}>{e.outcome}</Badge>
                    </div>
                    <div className="muted small">
                      {e.from} · {timeLabel(e.atMin)}
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Supply events">
            <table className="table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Event</th>
                  <th>Source</th>
                  <th>Confidence</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {state.events.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.id}</td>
                    <td>
                      <strong>{e.title}</strong>
                      <div className="muted small">{e.summary}</div>
                    </td>
                    <td>
                      <SourceChip id={e.sourceRef} />
                    </td>
                    <td>{Math.round(e.confidence * 100)}%</td>
                    <td>
                      <Badge tone={e.status === "open" ? "bad" : e.status === "monitoring" ? "warn" : "good"}>{e.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>

        {email ? (
          <Card title={email.subject} actions={<SourceChip id={email.id} />}>
            <div className="email-grid">
              <div>
                <h4>Original message</h4>
                <pre className="email-body">{highlight(email.body, email.extraction.excerpt)}</pre>
              </div>
              <div>
                <h4>Notice extraction agent</h4>
                <dl className="kv">
                  <dt>Event type</dt>
                  <dd>{email.extraction.eventType}</dd>
                  <dt>Drug</dt>
                  <dd>{email.extraction.drug}</dd>
                  <dt>Match rule</dt>
                  <dd>{email.extraction.drugMatch}</dd>
                  {email.extraction.poRef && (
                    <>
                      <dt>Order</dt>
                      <dd>
                        <SourceChip id={email.extraction.poRef} /> {email.extraction.qty} vials
                      </dd>
                      <dt>ETA</dt>
                      <dd>
                        {email.extraction.originalEta} → <strong>{email.extraction.newEta}</strong>
                      </dd>
                      <dt>Site</dt>
                      <dd>{email.extraction.site}</dd>
                    </>
                  )}
                  <dt>Confidence</dt>
                  <dd>
                    <div className="confidence">
                      <div style={{ width: `${email.extraction.confidence * 100}%` }} className={email.extraction.confidence >= 0.85 ? "good" : "bad"} />
                    </div>
                    {Math.round(email.extraction.confidence * 100)}% (auto-accept at 85%)
                  </dd>
                </dl>
                {email.extraction.flags.length > 0 && (
                  <div className="callout bad">
                    <strong>Quarantined for human review</strong>
                    <ul>
                      {email.extraction.flags.map((f) => (
                        <li key={f}>{f}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <p className="muted small">
                  The agent returns a validated object and cannot write to the database or follow instructions inside the email. Highlighted text is the excerpt it cited.
                </p>
              </div>
            </div>
          </Card>
        ) : (
          <Empty>No emails yet.</Empty>
        )}
      </div>
    </div>
  );
}

function highlight(body: string, excerpt: string) {
  const i = body.indexOf(excerpt);
  if (i < 0) return body;
  return (
    <>
      {body.slice(0, i)}
      <mark>{excerpt}</mark>
      {body.slice(i + excerpt.length)}
    </>
  );
}
