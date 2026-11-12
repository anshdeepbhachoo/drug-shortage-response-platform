import { describe, expect, it } from "vitest";
import { initialStock } from "../data/mock";
import { coverageDays, evaluatePlan, tierFor } from "./risk";
import type { Plan, StockLine, SupplyEvent } from "./types";

const delayEvent: SupplyEvent = {
  id: "SE-1",
  drugId: "vaso",
  siteId: "B",
  kind: "delay",
  title: "",
  summary: "",
  source: "supplier-email",
  sourceRef: "",
  confidence: 0.92,
  atMin: 0,
  status: "open",
  resupplyDay: 23,
};

function delayedStock(riversideUsage = 6): StockLine[] {
  return initialStock().map((l) => {
    if (l.drugId !== "vaso") return l;
    if (l.siteId === "B") return { ...l, orders: l.orders.map((o) => ({ ...o, etaDay: 23 })) };
    if (l.siteId === "A") return { ...l, dailyUsage: riversideUsage };
    return l;
  });
}

function plan(partial: Partial<Plan>): Plan {
  return {
    id: "P",
    eventId: "SE-1",
    drugId: "vaso",
    solverRun: "S",
    rank: 1,
    title: "",
    transfers: [],
    orders: [],
    score: 1,
    cost: "",
    leadTime: "",
    explanation: "",
    citations: [],
    status: "proposed",
    ...partial,
  };
}

const evaluate = (p: Plan, stock: StockLine[]) => evaluatePlan(p, stock, delayEvent, (id) => id, (d) => `day ${d}`);

describe("coverageDays", () => {
  it("divides stock by usage when there are no orders", () => {
    expect(coverageDays(24, 4, [])).toBe(6);
  });

  it("counts an order that arrives before stock runs out", () => {
    expect(coverageDays(24, 4, [{ qty: 120, etaDay: 2 }])).toBe(36);
  });

  it("ignores an order that arrives after the stockout", () => {
    expect(coverageDays(24, 4, [{ qty: 120, etaDay: 23 }])).toBe(6);
  });

  it("returns fractional days", () => {
    expect(coverageDays(86, 4, [])).toBe(21.5);
  });
});

describe("tierFor", () => {
  it("maps days to red, amber and green", () => {
    expect(tierFor(6)).toBe("red");
    expect(tierFor(10.8)).toBe("amber");
    expect(tierFor(14)).toBe("green");
  });
});

describe("evaluatePlan", () => {
  const transfer72 = plan({ transfers: [{ from: "A", to: "B", qty: 72 }] });

  it("accepts the 72-vial transfer at normal Riverside usage", () => {
    expect(evaluate(transfer72, delayedStock()).feasible).toBe(true);
  });

  it("rejects the same transfer once Riverside usage rises to 10/day", () => {
    const { feasible, checks } = evaluate(transfer72, delayedStock(10));
    expect(feasible).toBe(false);
    expect(checks.find((c) => c.label.startsWith("A stays above"))?.ok).toBe(false);
  });

  it("finds no transfer-only plan after the spike, but the split plus expedite works", () => {
    const stock = delayedStock(10);
    const split = { transfers: [{ from: "A", to: "B", qty: 40 }, { from: "C", to: "B", qty: 22 }] };
    expect(evaluate(plan(split), stock).feasible).toBe(false);
    expect(evaluate(plan({ ...split, orders: [{ qty: 12, etaDay: 5, supplier: "X", premiumPct: 38 }] }), stock).feasible).toBe(true);
  });
});
