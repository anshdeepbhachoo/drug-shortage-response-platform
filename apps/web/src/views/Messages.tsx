import { useState } from "react";
import { Card, Empty, PageHead, Status } from "../components/ui";
import { useStore } from "../lib/store";

export function Messages() {
  const { state, dispatch } = useStore();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const draft = state.drafts.find((d) => d.id === selectedId) ?? state.drafts[0];
  const toSend = state.drafts.filter((d) => d.status === "draft").length;

  if (state.drafts.length === 0) {
    return (
      <>
        <PageHead label="Messages" title="Messages ready to send" lead="Once a plan is approved, the AI writes the transfer requests and orders here for procurement to send." />
        <Empty title="No messages yet">They appear as soon as a pharmacist approves a plan.</Empty>
      </>
    );
  }

  const copy = async () => {
    try {
      await navigator.clipboard?.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked: nothing to do */
    }
  };

  const canSend = draft.status === "draft" && state.role === "procurement";

  return (
    <>
      <PageHead
        label="Messages"
        title={toSend ? `${toSend} message${toSend > 1 ? "s" : ""} ready to send` : "Every message has been sent"}
        lead="The AI drafts each message from the approved plan. The platform never sends anything: procurement sends it the usual way, then marks it sent so it goes on the record."
      />

      <div className="grid-2">
        <Card
          label={`${draft.kind} · to ${draft.to} · plan ${draft.planId}`}
          title={draft.subject}
          actions={<Status tone={draft.status === "draft" ? "warn" : "good"}>{draft.status === "draft" ? "Not sent yet" : "Sent"}</Status>}
          footer={
            <>
              <span className="small muted">{state.role !== "procurement" ? "Only procurement can mark messages as sent." : "Send it from your email, then mark it sent."}</span>
              <span className="row">
                <button className="btn" onClick={copy}>
                  {copied ? "Copied" : "Copy text"}
                </button>
                <button className="btn primary" disabled={!canSend} onClick={() => dispatch({ type: "markSent", draftId: draft.id })}>
                  Mark as sent
                </button>
              </span>
            </>
          }
        >
          <pre className="letter">{draft.body}</pre>
        </Card>

        <section className="card">
          <header className="card-head">
            <div>
              <div className="label" style={{ marginBottom: 4 }}>
                All messages
              </div>
              <h3>From approved plans</h3>
            </div>
          </header>
          <div className="mail-list" style={{ marginTop: 10 }}>
            {state.drafts.map((d) => (
              <button key={d.id} className={`mail ${d.id === draft.id ? "active" : ""}`} onClick={() => setSelectedId(d.id)}>
                <strong>{d.subject}</strong>
                <Status tone={d.status === "draft" ? "warn" : "good"}>{d.status === "draft" ? "To send" : "Sent"}</Status>
                <span className="meta">
                  {d.kind} · to {d.to}
                </span>
              </button>
            ))}
          </div>
        </section>
      </div>
    </>
  );
}
