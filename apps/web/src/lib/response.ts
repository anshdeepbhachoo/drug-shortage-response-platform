import type { FollowUp } from "./api";
import type { Plan, SupplyEvent } from "./types";

export type Stage = "decide" | "attention" | "tracking";
export type ResponseTone = "good" | "warn" | "crit" | "neutral";

/** The drug's one current alert: open, or approved and not yet closed. */
export function alertFor(events: SupplyEvent[], drugId: string): SupplyEvent | undefined {
  const mine = events.filter((e) => e.drugId === drugId);
  return mine.find((e) => e.status === "open") ?? mine.find((e) => e.status === "mitigated");
}

/** The current plan's options for an alert: the newest plan that is not superseded or rejected. */
export function currentOptions(plans: Plan[], eventId: string): Plan[] {
  const mine = plans.filter((p) => p.eventId === eventId && p.status !== "superseded" && p.status !== "rejected");
  const latest = mine[0]?.solverRun;
  return mine.filter((p) => p.solverRun === latest).sort((a, b) => a.rank - b.rank);
}

export function stageOf(options: Plan[]): Stage {
  if (options.some((p) => p.status === "approved")) return "tracking";
  if (options.some((p) => p.status === "pending-approval")) return "decide";
  return "attention";
}

/** What kind of action an approved option is, in operator language. */
function approvedLabel(option: Plan): string {
  const kind = option.optionType;
  if (kind === "order" || (kind !== "transfer" && /\b(order|purchase|expedite)/i.test(option.title))) return "Order approved";
  if (kind === "transfer" || option.transfers.length) return "Transfer approved";
  if (kind === "substitute") return "Substitute approved";
  if (kind === "conserve") return "Conservation approved";
  if (kind === "escalate") return "Escalated to a person";
  return "Plan approved";
}

const FOLLOW_WORD: Record<string, string> = {
  wait: "on track",
  nudge: "awaiting confirmation",
  escalate: "escalated",
  replan: "replanning",
  close: "complete",
};

export type DrugResponse = { stage: Stage | "none"; label: string; tone: ResponseTone; detail?: string };

/** Where a drug's response stands: what was approved (or what is pending), and the latest follow-up check. */
export function responseFor(drugId: string, events: SupplyEvent[], plans: Plan[], followups: FollowUp[] = []): DrugResponse {
  const alert = alertFor(events, drugId);
  if (!alert) return { stage: "none", label: "No alert", tone: "neutral" };
  const options = currentOptions(plans, alert.id);
  const stage = stageOf(options);
  if (stage === "tracking") {
    const approved = options.find((p) => p.status === "approved")!;
    const checks = followups.filter((f) => f.drugId === drugId && f.decision);
    const worst = checks.find((f) => f.decision === "escalate" || f.decision === "replan") ?? checks[0];
    return {
      stage,
      label: approvedLabel(approved),
      tone: worst && (worst.decision === "escalate" || worst.decision === "replan") ? "warn" : "good",
      detail: worst ? FOLLOW_WORD[worst.decision!] : undefined,
    };
  }
  if (stage === "decide") return { stage, label: "Pending approval", tone: "warn" };
  return { stage, label: options.length ? "No safe option" : "No plan", tone: "crit" };
}
