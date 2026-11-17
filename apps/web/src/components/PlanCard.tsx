import type { ReactNode } from "react";
import { SITES, dayLabel, drugById, siteName, siteTag } from "../data/mock";
import { AMBER_BELOW, applyPlan, coverageDays, evaluatePlan, tierFor } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Plan, PlanStatus } from "../lib/types";
import { Sources, Status, TIER_TONE, formatDays, type Tone } from "./ui";

export const PLAN_STATUS: Record<PlanStatus, { tone: Tone; text: string }> = {
  proposed: { tone: "neutral", text: "Option" },
  "pending-approval": { tone: "warn", text: "Waiting for pharmacist" },
  approved: { tone: "good", text: "Approved" },
  rejected: { tone: "crit", text: "Rejected" },
  broken: { tone: "crit", text: "Stopped: no longer safe" },
  superseded: { tone: "neutral", text: "Replaced by a newer plan" },
};

/** Live data: approval isn't tied to one pharmacist, and "superseded" means another option was chosen. */
const LIVE_TEXT: Partial<Record<PlanStatus, string>> = {
  "pending-approval": "Waiting for approval",
  superseded: "Not chosen",
};

/** Live: what an option that moves no stock means, and what the server checked. */
const NON_TRANSFER: Record<string, { moves: string; check: string; checkDetail: string }> = {
  substitute: {
    moves: "No stock moves. Patients get the substitute drug instead; a pharmacist decides.",
    check: "Substitute is in the pre-approved substitution table",
    checkDetail: "Checked against the table; anything not in it is never offered",
  },
  conserve: {
    moves: "No stock moves. The at-risk hospitals cut how much they use until supply recovers.",
    check: "Nothing to calculate: a judgement call",
    checkDetail: "Choosing it records who decided; the clinical teams set the new usage",
  },
  escalate: {
    moves: "No stock moves. A person takes this on: calls the supplier, places an emergency order or arranges conservation.",
    check: "Nothing to calculate: a hand-off to a person",
    checkDetail: "Choosing it records who took the problem on",
  },
  order: {
    moves: "A new order is placed with a supplier.",
    check: "Order details are confirmed by procurement",
    checkDetail: "Nothing is sent from here",
  },
};

export function planStatus(status: PlanStatus, live: boolean) {
  const base = PLAN_STATUS[status];
  return live && LIVE_TEXT[status] ? { ...base, text: LIVE_TEXT[status]! } : base;
}

/** Live data: which drug, where, and what raised it, so a card makes sense on its own. */
function planSubject(drugName: string, event: { siteId?: string; source: string; sourceRef: string }) {
  const where = event.siteId ? siteName(event.siteId) : "several hospitals";
  const from = event.sourceRef.startsWith("stock_watch:")
    ? "stock watch"
    : event.sourceRef.startsWith("manual:")
      ? "the supply manager"
    : event.source === "supplier-email"
      ? "supplier email"
      : event.source === "hc-shortage-api"
        ? "Health Canada report"
        : "hospital report";
  return `${drugName} · ${where} · raised by ${from}`;
}

const MAX = 60;
const pct = (d: number) => `${(Math.min(d, MAX) / MAX) * 100}%`;

export function PlanCard({ plan, actions, compact, recommended }: { plan: Plan; actions?: ReactNode; compact?: boolean; recommended?: boolean }) {
  const { state } = useStore();
  const event = state.events.find((e) => e.id === plan.eventId)!;
  const drug = drugById(plan.drugId);
  const kind = state.live ? plan.optionType : undefined;
  const nonTransfer = kind !== undefined && kind !== "transfer" ? NON_TRANSFER[kind] : undefined;
  // Options that move no stock were checked by the server for what they are; the stock maths does not apply.
  // Planners write site codes ("from B to A"); people read facility names.
  const title =
    state.live && plan.transfers.length
      ? `Transfer ${plan.transfers.map((t) => `${t.qty} from ${siteName(t.from)} to ${siteName(t.to)}`).join(", ")}`
      : plan.title;
  // Substitutions change what patients receive, so the substitute drug is named explicitly.
  const isSubstitute = plan.optionType === "substitute" || /substitute/i.test(plan.title);
  const substitute = isSubstitute ? drugById(plan.title.match(/\b\d{8}\b/)?.[0] ?? "") : undefined;
  const { checks, feasible } = nonTransfer
    ? { checks: [{ label: nonTransfer.check, ok: true, hard: true, detail: nonTransfer.checkDetail }], feasible: true }
    : evaluatePlan(plan, state.stock, event, siteName, dayLabel, drug.unit);
  const hard = checks.filter((c) => c.hard);
  const soft = checks.filter((c) => !c.hard);
  const after = applyPlan(state.stock, plan, event.siteId);
  const status = planStatus(plan.status, state.live !== null);
  const faded = plan.status === "superseded" || plan.status === "rejected";

  return (
    <article className={`plan ${recommended && feasible ? "recommended" : ""} ${faded ? "faded" : ""}`}>
      <header className="plan-top">
        <span className="rank">#{plan.rank}</span>
        <div>
          {state.live && <div className="label" style={{ marginBottom: 4 }}>{planSubject(drug.name, event)}</div>}
          <h3>{title}</h3>
          {isSubstitute && (
            <div className="sub-callout">
              <b>Substitute drug:</b> {substitute ? `${substitute.name} (DIN ${substitute.id})` : "see rationale"} · replaces {drug.name} · requires pharmacist sign-off
            </div>
          )}
          <div className="row" style={{ marginTop: 4 }}>
            {recommended && feasible && <span className="badge-accent">Recommended</span>}
            {!feasible && <Status tone="crit">Breaks a safety rule</Status>}
            <span className="meta">
              Cost {plan.cost.replace("≈ ", "≈")} · {plan.leadTime}
            </span>
          </div>
        </div>
        <Status tone={status.tone}>{status.text}</Status>
      </header>

      {!compact && (
        <div className="plan-cols">
          <div>
            <div className="label" style={{ marginBottom: 8 }}>
              Transfers
            </div>
            <div className="flow">
              {plan.transfers.map((t, i) => (
                <div key={i} className="flow-row">
                  <span className="from">{siteName(t.from)}</span>
                  <span className="qty">
                    {t.qty} {drug.unit}
                  </span>
                  <span className="to">{siteName(t.to)}</span>
                </div>
              ))}
              {plan.orders.map((o, i) => (
                <div key={`o${i}`} className="flow-row">
                  <span className="from supplier">{o.supplier}</span>
                  <span className="qty">
                    {o.qty} by {dayLabel(o.etaDay)}
                  </span>
                  <span className="to">{siteName(event.siteId ?? "")}</span>
                </div>
              ))}
              {nonTransfer && <p className="small ink-2">{nonTransfer.moves}</p>}
              {plan.conservationUsage !== undefined && (
                <p className="small ink-2">Lakeshore also cuts daily use to {plan.conservationUsage} {drug.unit}.</p>
              )}
            </div>

            <div className="label" style={{ margin: "18px 0 8px" }}>
              Safety checks (deterministic)
            </div>
            <ul className="checks">
              {hard.map((c) => (
                <li key={c.label}>
                  <Status tone={c.ok ? "good" : "crit"}>{""}</Status>
                  <div>
                    {c.label}
                    <div className="sub">{c.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
            {soft.length > 0 && (
              <>
                <div className="label" style={{ margin: "14px 0 8px" }}>
                  Trade-offs
                </div>
                <ul className="checks">
                  {soft.map((c) => (
                    <li key={c.label}>
                      <Status tone="warn">{""}</Status>
                      <div>
                        {c.label}
                        <div className="sub">{c.detail}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>

          <div>
            <div className="label" style={{ marginBottom: 8 }}>
              Days of supply after plan
            </div>
            <div className="bars" role="img" aria-label="Days of stock at each hospital after this plan">
              {SITES.map((s) => {
                const st = after.get(s.id);
                if (!st) return null;
                const days = coverageDays(st.onHand, st.usage, st.arrivals);
                const tier = tierFor(days);
                const receiving = s.id === event.siteId;
                const needed = receiving && event.resupplyDay ? event.resupplyDay - 1 : undefined;
                return (
                  <div key={s.id} className="bar-row" style={{ cursor: "default", padding: "6px 4px" }} title={`${s.name}: ${formatDays(days)} days after this plan`}>
                    <span className="site">
                      {siteTag(s.id)}
                      {receiving && <span className="muted"> · gets stock</span>}
                    </span>
                    <div className="bar-track">
                      <span className="bar-zone red" style={{ left: 0, width: pct(7) }} />
                      <span className="bar-zone amber" style={{ left: pct(7), width: `calc(${pct(AMBER_BELOW)} - ${pct(7)})` }} />
                      <span className={`bar-fill ${TIER_TONE[tier]}`} style={{ width: pct(days) }} />
                      <span className="bar-floor" style={{ left: pct(AMBER_BELOW) }} />
                      {needed !== undefined && <span className="bar-after" style={{ left: pct(needed) }} title={`Needs ${needed} days until the delayed order arrives`} />}
                    </div>
                    <span className="bar-val">
                      <b>{formatDays(days)}</b>d
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {!compact && (
        <details className="why">
          <summary>{plan.explanation.startsWith("Rules planner") ? "Rationale · rules planner" : "Rationale · planning agent"}</summary>
          <div className="inner">
            <p>{plan.explanation}</p>
            <span className="small muted">Every number in this text was checked against the calculation before it was shown.</span>
            {plan.decisionNote && (
              <p>
                <b>Decision note:</b> “{plan.decisionNote}”
              </p>
            )}
            <Sources ids={[plan.solverRun, ...plan.citations]} />
          </div>
        </details>
      )}

      {actions && <footer className="plan-actions">{actions}</footer>}
    </article>
  );
}

export function usePlanFeasible(plan: Plan) {
  const { state } = useStore();
  const event = state.events.find((e) => e.id === plan.eventId)!;
  return evaluatePlan(plan, state.stock, event, siteName, dayLabel).feasible;
}
