import { useState } from "react";
import { PlanCard } from "../components/PlanCard";
import { Badge, Card, Empty } from "../components/ui";
import { USERS, timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import type { Plan } from "../lib/types";

export function Approvals() {
  const { state } = useStore();
  const pending = state.plans.filter((p) => p.status === "pending-approval");
  const decisions = state.audit.filter((a) => a.actor === "pharmacist");

  return (
    <div className="view">
      {state.role !== "pharmacist" && (
        <div className="callout info">
          You are viewing as <strong>{USERS[state.role].title}</strong>. Approval buttons are only enabled for the site pharmacist; the server enforces the same rule.
        </div>
      )}
      <h2 className="section-title">
        Awaiting your approval <Badge tone="info">{pending.length}</Badge>
      </h2>
      {pending.length === 0 ? (
        <Empty>Nothing to approve. New plans are routed here when a site is at risk.</Empty>
      ) : (
        <div className="plans">
          {pending.map((p) => (
            <PlanCard key={p.id} plan={p} actions={<Decide plan={p} />} />
          ))}
        </div>
      )}

      <Card title="Recent decisions">
        {decisions.length === 0 ? (
          <p className="muted">No decisions yet.</p>
        ) : (
          <table className="table">
            <tbody>
              {decisions.map((d) => (
                <tr key={d.id}>
                  <td className="nowrap muted">{timeLabel(d.atMin)}</td>
                  <td>
                    <Badge tone={d.action === "Approved" ? "good" : "bad"}>{d.action}</Badge>
                  </td>
                  <td className="mono">{d.object}</td>
                  <td>{d.detail}</td>
                  <td className="muted">{d.actorName}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

function Decide({ plan }: { plan: Plan }) {
  const { state, dispatch } = useStore();
  const [note, setNote] = useState("");
  const allowed = state.role === "pharmacist";
  return (
    <>
      <input className="input grow" placeholder="Optional note for the audit log" value={note} onChange={(e) => setNote(e.target.value)} disabled={!allowed} />
      <button className="btn ghost" disabled={!allowed} onClick={() => dispatch({ type: "reject", planId: plan.id, note })}>
        Reject
      </button>
      <button className="btn primary" disabled={!allowed} onClick={() => dispatch({ type: "approve", planId: plan.id, note })}>
        Approve plan
      </button>
    </>
  );
}
