import type { ReactNode } from "react";
import { dayLabel, drugById, siteName } from "../data/mock";
import { evaluatePlan } from "../lib/risk";
import { useStore } from "../lib/store";
import type { Plan, PlanStatus } from "../lib/types";
import { Badge, SourceChip } from "./ui";

const STATUS_TONE: Record<PlanStatus, "neutral" | "info" | "good" | "bad" | "warn"> = {
  proposed: "neutral",
  "pending-approval": "info",
  approved: "good",
  rejected: "bad",
  broken: "bad",
  superseded: "warn",
};

export function PlanCard({ plan, actions, compact }: { plan: Plan; actions?: ReactNode; compact?: boolean }) {
  const { state } = useStore();
  const event = state.events.find((e) => e.id === plan.eventId)!;
  const drug = drugById(plan.drugId);
  const { checks, feasible } = evaluatePlan(plan, state.stock, event, siteName, dayLabel, drug.unit);
  const hard = checks.filter((c) => c.hard);
  const soft = checks.filter((c) => !c.hard);

  return (
    <article className={`plan ${feasible ? "" : "infeasible"} plan-${plan.status}`}>
      <header className="plan-head">
        <div className="rank">#{plan.rank}</div>
        <div className="grow">
          <h3>{plan.title}</h3>
          <div className="muted small">
            {plan.id} · solver run <SourceChip id={plan.solverRun} /> · score {plan.score.toFixed(2)}
          </div>
        </div>
        <div className="plan-badges">
          {!feasible && <Badge tone="bad">violates hard constraint</Badge>}
          <Badge tone={STATUS_TONE[plan.status]}>{plan.status.replace("-", " ")}</Badge>
        </div>
      </header>

      <div className="plan-moves">
        {plan.transfers.map((t, i) => (
          <div key={i} className="move">
            <span className="site-tag">{siteName(t.from)}</span>
            <span className="arrow">
              {t.qty} {drug.unit} →
            </span>
            <span className="site-tag">{siteName(t.to)}</span>
          </div>
        ))}
        {plan.orders.map((o, i) => (
          <div key={`o${i}`} className="move">
            <span className="site-tag supplier">{o.supplier}</span>
            <span className="arrow">
              {o.qty} {drug.unit} by {dayLabel(o.etaDay)} →
            </span>
            <span className="site-tag">{siteName(event.siteId ?? "")}</span>
          </div>
        ))}
        <div className="plan-meta">
          <span>
            <b>Cost</b> {plan.cost}
          </span>
          <span>
            <b>Timing</b> {plan.leadTime}
          </span>
        </div>
      </div>

      {!compact && (
        <div className="plan-body">
          <div>
            <h4>Hard constraints (checked in code)</h4>
            <ul className="checks">
              {hard.map((c) => (
                <li key={c.label} className={c.ok ? "ok" : "fail"}>
                  <span className="mark">{c.ok ? "✓" : "✕"}</span>
                  <div>
                    {c.label}
                    <div className="muted small">{c.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
            {soft.length > 0 && (
              <>
                <h4>Tradeoffs</h4>
                <ul className="checks">
                  {soft.map((c) => (
                    <li key={c.label} className="warn">
                      <span className="mark">!</span>
                      <div>
                        {c.label}
                        <div className="muted small">{c.detail}</div>
                      </div>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
          <div>
            <h4>
              Why this plan <Badge tone="good">numbers match solver output</Badge>
            </h4>
            <p className="explanation">{plan.explanation}</p>
            <div className="chips">
              {plan.citations.map((c) => (
                <SourceChip key={c} id={c} />
              ))}
            </div>
            {plan.decisionNote && <p className="muted small">Decision note: “{plan.decisionNote}”</p>}
          </div>
        </div>
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
