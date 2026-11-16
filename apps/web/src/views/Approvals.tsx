import { useState } from "react";
import { PlanCard } from "../components/PlanCard";
import { Card, Empty, PageHead, Status } from "../components/ui";
import { USERS, timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import type { Plan } from "../lib/types";

export function Approvals() {
  const { state } = useStore();
  const pending = state.plans.filter((p) => p.status === "pending-approval");
  const decisions = state.audit.filter((a) => a.actor === "pharmacist");
  const isPharmacist = state.role === "pharmacist";

  return (
    <>
      <PageHead
        label="Approvals"
        title={pending.length ? `${pending.length} plan${pending.length > 1 ? "s" : ""} waiting for a decision` : "Nothing waiting for a decision"}
        lead="Only the pharmacist at the receiving hospital can approve a plan. Approving records the decision and writes the messages, but sends nothing."
      />

      {!isPharmacist && (
        <div className="callout info">
          <span className="ico" aria-hidden>
            i
          </span>
          <div>
            <strong>You're viewing as {USERS[state.role].name}, {USERS[state.role].title.toLowerCase()}.</strong>
            <span className="ink-2">You can see plans here, but only {USERS.pharmacist.name} can approve them. Switch person at the top right to try it.</span>
          </div>
        </div>
      )}

      {pending.length === 0 ? (
        <Empty title="All caught up">New plans arrive here when a hospital is at risk.</Empty>
      ) : (
        <div className="stack" style={{ gap: 16 }}>
          {pending.map((p) => (
            <PlanCard key={p.id} plan={p} recommended actions={<Decide plan={p} />} />
          ))}
        </div>
      )}

      <Card label="On the record" title="Past decisions">
        {decisions.length === 0 ? (
          <p className="muted">No decisions yet.</p>
        ) : (
          <div className="stack" style={{ gap: 12 }}>
            {decisions.map((d) => (
              <div key={d.id} className="row-between" style={{ alignItems: "flex-start" }}>
                <div>
                  <b>{d.detail.split(". Note:")[0]}</b>
                  <div className="small ink-2">
                    {d.actorName} · {timeLabel(d.atMin)}
                    {d.detail.includes("Note:") && <> · “{d.detail.split('Note: "')[1]?.replace(/"$/, "")}”</>}
                  </div>
                </div>
                <Status tone={d.action === "Approved" ? "good" : "crit"}>{d.action}</Status>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  );
}

function Decide({ plan }: { plan: Plan }) {
  const { state, dispatch } = useStore();
  const [note, setNote] = useState("");
  const allowed = state.role === "pharmacist";
  return (
    <>
      <input
        className="input"
        style={{ flex: 1, minWidth: 200 }}
        placeholder="Add a note for the record (optional)"
        aria-label="Note for the record"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        disabled={!allowed}
      />
      <button className="btn" disabled={!allowed} onClick={() => dispatch({ type: "reject", planId: plan.id, note })}>
        Reject
      </button>
      <button className="btn primary" disabled={!allowed} onClick={() => dispatch({ type: "approve", planId: plan.id, note })}>
        Approve plan
      </button>
    </>
  );
}
