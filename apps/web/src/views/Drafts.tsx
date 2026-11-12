import { useState } from "react";
import { Badge, Card, Empty } from "../components/ui";
import { useStore } from "../lib/store";

export function Drafts() {
  const { state, dispatch } = useStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const draft = state.drafts.find((d) => d.id === selectedId) ?? state.drafts[0];

  if (state.drafts.length === 0) {
    return (
      <div className="view">
        <Empty>No drafts yet. The drafting agent writes transfer requests and orders once a plan is approved.</Empty>
      </div>
    );
  }

  const copy = async () => {
    await navigator.clipboard?.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div className="view">
      <div className="split">
        <Card title="Drafts from approved plans">
          <ul className="inbox">
            {state.drafts.map((d) => (
              <li key={d.id}>
                <button className={`inbox-item ${d.id === draft.id ? "active" : ""}`} onClick={() => setSelectedId(d.id)}>
                  <div className="row-between">
                    <strong>{d.subject}</strong>
                    <Badge tone={d.status === "draft" ? "info" : "good"}>{d.status === "draft" ? "draft" : "sent manually"}</Badge>
                  </div>
                  <div className="muted small">
                    {d.kind} · to {d.to} · plan {d.planId}
                  </div>
                </button>
              </li>
            ))}
          </ul>
        </Card>
        <Card
          title={draft.subject}
          actions={
            <span className="row gap">
              <button className="btn ghost" onClick={copy}>
                {copied ? "Copied" : "Copy text"}
              </button>
              <button
                className="btn primary"
                disabled={draft.status !== "draft" || state.role !== "procurement"}
                onClick={() => dispatch({ type: "markSent", draftId: draft.id })}
                title={state.role !== "procurement" ? "Procurement only" : undefined}
              >
                Mark as sent
              </button>
            </span>
          }
        >
          <dl className="kv">
            <dt>Type</dt>
            <dd>{draft.kind}</dd>
            <dt>To</dt>
            <dd>{draft.to}</dd>
            <dt>Plan</dt>
            <dd className="mono">{draft.planId}</dd>
          </dl>
          <pre className="email-body">{draft.body}</pre>
          <p className="muted small">The platform never sends anything. Procurement sends drafts through the usual channel and marks them sent here, which writes to the audit log.</p>
        </Card>
      </div>
    </div>
  );
}
