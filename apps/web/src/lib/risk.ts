import type { ConstraintCheck, Plan, StockLine, SupplyEvent, Tier } from "./types";

export const HORIZON_DAYS = 60;
export const RED_BELOW = 7;
export const AMBER_BELOW = 14;

type Arrival = { qty: number; etaDay: number };

/** Days until stock runs out, counting arrivals at the start of their ETA day. */
export function coverageDays(onHand: number, usage: number, arrivals: Arrival[]): number {
  if (usage <= 0) return HORIZON_DAYS;
  let stock = onHand;
  for (let day = 1; day <= HORIZON_DAYS; day++) {
    stock += arrivals.filter((a) => a.etaDay === day).reduce((sum, a) => sum + a.qty, 0);
    if (stock < usage) return day - 1 + stock / usage;
    stock -= usage;
  }
  return HORIZON_DAYS;
}

export function projection(onHand: number, usage: number, arrivals: Arrival[], days = 30): number[] {
  const points = [onHand];
  let stock = onHand;
  for (let day = 1; day <= days; day++) {
    stock += arrivals.filter((a) => a.etaDay === day).reduce((sum, a) => sum + a.qty, 0);
    stock = Math.max(stock - usage, 0);
    points.push(stock);
  }
  return points;
}

export function tierFor(days: number, safetyDays = AMBER_BELOW): Tier {
  if (days < RED_BELOW) return "red";
  if (days < Math.min(AMBER_BELOW, safetyDays)) return "amber";
  return "green";
}

export function lineCoverage(line: StockLine): number {
  return coverageDays(line.onHand, line.dailyUsage, line.orders);
}

export interface SiteState {
  onHand: number;
  usage: number;
  arrivals: Arrival[];
}

/** Applies a plan's transfers, orders and conservation to a copy of the stock lines for one drug. */
export function applyPlan(lines: StockLine[], plan: Plan, recipientSiteId?: string): Map<string, SiteState> {
  const state = new Map<string, SiteState>();
  for (const l of lines.filter((l) => l.drugId === plan.drugId)) {
    state.set(l.siteId, { onHand: l.onHand, usage: l.dailyUsage, arrivals: [...l.orders] });
  }
  for (const t of plan.transfers) {
    const from = state.get(t.from);
    const to = state.get(t.to);
    if (from) from.onHand -= t.qty;
    if (to) to.onHand += t.qty;
  }
  if (recipientSiteId) {
    const recipient = state.get(recipientSiteId);
    if (recipient) {
      for (const o of plan.orders) recipient.arrivals.push({ qty: o.qty, etaDay: o.etaDay });
      if (plan.conservationUsage !== undefined) recipient.usage = plan.conservationUsage;
    }
  }
  return state;
}

export function evaluatePlan(
  plan: Plan,
  lines: StockLine[],
  event: SupplyEvent,
  siteName: (id: string) => string,
  dayLabel: (day: number) => string,
  unit = "units",
): { checks: ConstraintCheck[]; feasible: boolean } {
  const after = applyPlan(lines, plan, event.siteId);
  const checks: ConstraintCheck[] = [];

  const donors = [...new Set(plan.transfers.map((t) => t.from))];
  for (const donor of donors) {
    const s = after.get(donor);
    const line = lines.find((l) => l.siteId === donor && l.drugId === plan.drugId);
    if (!s || !line) continue;
    const cov = coverageDays(s.onHand, s.usage, s.arrivals);
    checks.push({
      label: `${siteName(donor)} stays above its ${line.safetyDays}-day safety floor`,
      ok: cov >= line.safetyDays,
      hard: true,
      detail: `${s.onHand} ${unit} left at ${s.usage}/day = ${cov.toFixed(1)} days`,
    });
  }

  if (event.siteId && event.resupplyDay) {
    const s = after.get(event.siteId)!;
    const cov = coverageDays(s.onHand, s.usage, s.arrivals);
    const needed = event.resupplyDay - 1;
    checks.push({
      label: `No stockout at ${siteName(event.siteId)} before resupply (${dayLabel(event.resupplyDay)})`,
      ok: cov >= needed,
      hard: true,
      detail: `Covers ${cov >= HORIZON_DAYS ? `${HORIZON_DAYS}+` : cov.toFixed(1)} days, needs ${needed}`,
    });
  }

  checks.push({
    label: "Substitutions only from the pre-approved table",
    ok: true,
    hard: true,
    detail: "No substitutions in this plan",
  });

  for (const o of plan.orders) {
    checks.push({
      label: `Expedited order from ${o.supplier}`,
      ok: false,
      hard: false,
      detail: `+${o.premiumPct}% unit cost, availability not yet confirmed by supplier`,
    });
  }
  if (plan.conservationUsage !== undefined && event.siteId) {
    const base = lines.find((l) => l.siteId === event.siteId && l.drugId === plan.drugId);
    checks.push({
      label: `Requires a conservation protocol at ${siteName(event.siteId)}`,
      ok: false,
      hard: false,
      detail: `Usage must drop from ${base?.dailyUsage}/day to ${plan.conservationUsage}/day. Clinical decision`,
    });
  }
  if (plan.transfers.length > 1) {
    checks.push({
      label: "Multiple shipments",
      ok: false,
      hard: false,
      detail: `${plan.transfers.length} courier runs to coordinate`,
    });
  }

  return { checks, feasible: checks.filter((c) => c.hard).every((c) => c.ok) };
}

/** Stock lines as they will look once every approved plan is carried out. */
export function withApprovedPlans(lines: StockLine[], plans: Plan[], events: SupplyEvent[]): StockLine[] {
  let result = lines.map((l) => ({ ...l, orders: [...l.orders] }));
  for (const plan of plans.filter((p) => p.status === "approved")) {
    const event = events.find((e) => e.id === plan.eventId);
    const after = applyPlan(result, plan, event?.siteId);
    result = result.map((l) => {
      if (l.drugId !== plan.drugId) return l;
      const s = after.get(l.siteId)!;
      return {
        ...l,
        onHand: s.onHand,
        dailyUsage: s.usage,
        orders: s.arrivals.map((a, i) =>
          i < l.orders.length ? l.orders[i] : { id: `${plan.id}-PO`, qty: a.qty, etaDay: a.etaDay, supplier: "", status: "expedited" as const },
        ),
      };
    });
  }
  return result;
}
