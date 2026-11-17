import { useState } from "react";
import { PlanCard } from "../components/PlanCard";
import { Card, Empty, PageHead, Status } from "../components/ui";
import { USERS, drugById, siteName, timeLabel } from "../data/mock";
import { useStore } from "../lib/store";
import type { Plan } from "../lib/types";

export function Approvals() {
  const { state } = useStore();
  const isLive = state.live !== null;
  const [drugFilter, setDrugFilter] = useState("");
  const [siteFilter, setSiteFilter] = useState("");
  const allPending = state.plans.filter((p) => p.status === "pending-approval");
  const pending = allPending.filter(
    (p) => (!drugFilter || p.drugId === drugFilter) && (!siteFilter || (p.sites ?? []).includes(siteFilter)),
  );
  // Live: one decision per plan, so options are grouped by the plan they belong to.
  const groups = [...new Set(pending.map((p) => p.solverRun))].map((run) =>
    state.plans.filter((p) => p.solverRun === run && p.status === "pending-approval").sort((a, b) => a.rank - b.rank),
  );
  const waiting = isLive ? groups.length : pending.length;
  const pendingDrugs = [...new Set(allPending.map((p) => p.drugId))];
  const pendingSites = [...new Set(allPending.flatMap((p) => p.sites ?? []))].sort();
  const decisions = isLive
    ? state.audit.filter((a) => a.action === "Approved" || a.action === "Rejected")
    : state.audit.filter((a) => a.actor === "pharmacist");
  const isPharmacist = state.role === "pharmacist";
  const approved = isLive ? state.plans.filter((p) => p.status === "approved") : [];

  return (
    <>
      <PageHead
        label="Approvals"
        title={waiting ? `${waiting} plan${waiting > 1 ? "s" : ""} waiting for a decision` : "Nothing waiting for a decision"}
        lead={
          isLive
            ? "Each plan lists every option that passed its checks. Choose one. The supply manager can choose any option except a substitution, which needs a pharmacist. Transfers are re-checked against today's stock first. Nothing is sent."
            : "Only the pharmacist at the receiving hospital can approve a plan. Approving records the decision and writes the messages, but sends nothing."
        }
      />

      {!isLive && !isPharmacist && (
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

      {isLive && allPending.length > 0 && (
        <div className="row" style={{ gap: 16 }}>
          <label className="row" style={{ gap: 8 }}>
            <span className="label">Drug</span>
            <select className="input" value={drugFilter} onChange={(e) => setDrugFilter(e.target.value)} aria-label="Filter approvals by drug">
              <option value="">All drugs ({pendingDrugs.length})</option>
              {pendingDrugs.map((id) => (
                <option key={id} value={id}>
                  {drugById(id)?.name ?? id}
                </option>
              ))}
            </select>
          </label>
          <label className="row" style={{ gap: 8 }}>
            <span className="label">Hospital</span>
            <select className="input" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)} aria-label="Filter approvals by hospital">
              <option value="">All hospitals</option>
              {pendingSites.map((id) => (
                <option key={id} value={id}>
                  {siteName(id)}
                </option>
              ))}
            </select>
          </label>
          {(drugFilter || siteFilter) && (
            <span className="small muted">
              Showing {waiting} of {isLive ? new Set(allPending.map((p) => p.solverRun)).size : allPending.length}
            </span>
          )}
        </div>
      )}

      {pending.length === 0 ? (
        <Empty title="All caught up">New plans arrive here when a hospital is at risk.</Empty>
      ) : isLive ? (
        <div className="stack" style={{ gap: 28 }}>
          {groups.map((options) => (
            <PlanChoice key={options[0].solverRun} options={options} />
          ))}
        </div>
      ) : (
        <div className="stack" style={{ gap: 16 }}>
          {pending.map((p) => (
            <PlanCard key={p.id} plan={p} recommended actions={<Decide plan={p} />} />
          ))}
        </div>
      )}

      {approved.length > 0 && (
        <section>
          <div className="section-head">
            <h2>Approved plans</h2>
            <span className="meta">the option a person chose, in full</span>
          </div>
          <div className="stack" style={{ gap: 16 }}>
            {approved.map((p) => (
              <PlanCard key={p.id} plan={p} actions={<ApprovedBy plan={p} />} />
            ))}
          </div>
        </section>
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

function ApprovedBy({ plan }: { plan: Plan }) {
  const { state } = useStore();
  const record = state.audit.find((a) => a.action === "Approved" && a.refs.includes(plan.solverRun));
  const note = record?.detail.split('Note: "')[1]?.replace(/"$/, "");
  return (
    <span className="small ink-2" style={{ flex: 1 }}>
      <Status tone="good">Approved</Status>
      {record && (
        <>
          {" "}by <b>{record.actorName}</b> · {timeLabel(record.atMin)}
          {note && <> · “{note}”</>}
        </>
      )}
    </span>
  );
}

/** Live: one plan, all of its options; the person chooses one, or rejects the plan. */
function PlanChoice({ options }: { options: Plan[] }) {
  const { state, dispatch, live } = useStore();
  const [note, setNote] = useState("");
  const first = options[0];
  const busy = live.busy !== null;
  const canDecide = state.role !== "auditor" && !busy;
  return (
    <section className="stack" style={{ gap: 12 }}>
      <div className="section-head" style={{ marginBottom: 0 }}>
        <h2>
          {drugById(first.drugId)?.name ?? first.drugId}: choose one of {options.length} option{options.length > 1 ? "s" : ""}
        </h2>
        <span className="meta">Plan {first.solverRun}</span>
      </div>
      {options.map((p) => {
        const roles = p.chooseRoles ?? [];
        const mayChoose = canDecide && roles.includes(state.role);
        return (
          <PlanCard
            key={p.id}
            plan={p}
            recommended={p === first}
            actions={
              <>
                {!mayChoose && state.role !== "auditor" && (
                  <span className="small ink-2" style={{ flex: 1 }}>
                    {roles.length ? `Only ${roles.map(roleName).join(" or ")} can choose this option.` : "Nobody can choose this option."}
                  </span>
                )}
                <button className="btn primary" disabled={!mayChoose} onClick={() => dispatch({ type: "approve", planId: p.id, note })}>
                  Choose option {p.rank}
                </button>
              </>
            }
          />
        );
      })}
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <input
          className="input"
          style={{ flex: 1, minWidth: 200 }}
          placeholder="Note for the record (needed to reject)"
          aria-label="Note for the record"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          disabled={!canDecide}
        />
        <button
          className="btn"
          disabled={!canDecide || !note.trim()}
          title={note.trim() ? undefined : "Write why before rejecting"}
          onClick={() => dispatch({ type: "reject", planId: first.id, note })}
        >
          Reject the whole plan
        </button>
      </div>
    </section>
  );
}

function roleName(role: string) {
  return { supply: "the supply manager", pharmacist: "a pharmacist", procurement: "procurement" }[role] ?? role;
}

function Decide({ plan }: { plan: Plan }) {
  const { state, dispatch, live } = useStore();
  const [note, setNote] = useState("");
  // Live: anyone who can hold an approval role may try; the server enforces the rules.
  const allowed = state.live ? state.role !== "auditor" && live.busy === null : state.role === "pharmacist";
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
        {state.live ? `Approve option ${plan.rank}` : "Approve plan"}
      </button>
    </>
  );
}
