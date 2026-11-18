import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import {
  HOSTILE_EMAIL,
  SUPPLIER_EMAIL,
  USERS,
  dayLabel,
  drugById,
  initialEvents,
  initialStock,
  siteName,
  applyLiveData,
} from "../data/mock";
import { api, type DaySummary, type FollowUp, type LiveState, type OrderStatus, type Planner } from "./api";
import type { AuditEntry, Draft, Email, Plan, Role, StockLine, SupplyEvent } from "./types";

export const STEPS = [
  { title: "Calm", detail: "Three sites, ten critical drugs, all green." },
  { title: "Supplier delay", detail: "A backorder email for vasopressin arrives and is turned into a supply event." },
  { title: "Ranked plans", detail: "The solver returns ranked plans; the top plan is routed to the Lakeshore pharmacist." },
  { title: "Approval", detail: "The pharmacist approves the transfer. The decision is logged and drafts are created." },
  { title: "Plan breaks", detail: "Riverside reports an ICU usage spike. The approved transfer now breaks its safety floor." },
  { title: "Re-plan", detail: "No perfect plan exists. The platform ranks fallbacks and shows the tradeoffs." },
  { title: "Trace", detail: "The re-plan is approved. Open the audit log and trace a number back to its source." },
];

const STEP_CLOCK = [0, 14, 15, 41, 200, 201, 232];

export interface State {
  step: number;
  clockMin: number;
  role: Role;
  stock: StockLine[];
  events: SupplyEvent[];
  emails: Email[];
  plans: Plan[];
  drafts: Draft[];
  audit: AuditEntry[];
  auditSeq: number;
  /** Set when the data comes from the API (database) instead of the scripted story. */
  live: {
    asOf: string;
    days: string[];
    daysRun: string[];
    inbox: string[];
    loadedDays: string[];
    dayReports: Record<string, DaySummary>;
    followups: FollowUp[];
  } | null;
}

type Action =
  | { type: "next" }
  | { type: "reset" }
  | { type: "role"; role: Role }
  | { type: "approve"; planId: string; note: string }
  | { type: "reject"; planId: string; note: string }
  | { type: "route"; planId: string }
  | { type: "markSent"; draftId: string }
  | { type: "load"; data: LiveState };

function audit(state: State, entry: Omit<AuditEntry, "id" | "atMin"> & { atMin?: number }): State {
  const seq = state.auditSeq + 1;
  const record: AuditEntry = { atMin: state.clockMin, ...entry, id: `AU-${String(seq).padStart(4, "0")}` };
  return { ...state, auditSeq: seq, audit: [record, ...state.audit] };
}

function initialState(role: Role = "supply"): State {
  let s: State = {
    step: 0,
    clockMin: 0,
    role,
    stock: initialStock(),
    events: initialEvents(),
    emails: [HOSTILE_EMAIL],
    plans: [],
    drafts: [],
    audit: [],
    auditSeq: 0,
    live: null,
  };
  s = audit(s, { atMin: -14400, actor: "system", actorName: "ingestion", action: "Import", object: "Critical drug list v3", detail: "214 rows, 0 quarantined", refs: ["CDL-2026-V3"] });
  s = audit(s, { atMin: -900, actor: "system", actorName: "ingestion", action: "Supply event", object: "SE-1038", detail: "Cyclophosphamide shortage report ingested; status monitoring", refs: ["HC-SHORT-0911"] });
  s = audit(s, { atMin: -620, actor: "agent", actorName: "notice-extraction", action: "Quarantine", object: "EMAIL-0187", detail: "Prompt-injection text ignored, no matching order; sent to human review", refs: ["EMAIL-0187"] });
  s = audit(s, { atMin: -360, actor: "system", actorName: "ingestion", action: "Ingestion run", object: "Run 87 (DPD)", detail: "1,248 rows in, 1,240 ok, 8 quarantined", refs: ["SNAP-4410"] });
  return s;
}

const PLAN_DEFAULTS = { drugId: "vaso", eventId: "SE-1042", orders: [], status: "proposed" as const };

function firstPlans(): Plan[] {
  return [
    {
      ...PLAN_DEFAULTS,
      id: "P-311",
      solverRun: "SOLVER-31",
      rank: 1,
      title: "Transfer 72 vials from Riverside",
      transfers: [{ from: "A", to: "B", qty: 72 }],
      score: 0.91,
      cost: "≈ $140 courier",
      leadTime: "Arrives tomorrow",
      status: "pending-approval",
      explanation:
        "Lakeshore will run out on Nov 18 because PO 5521 slipped from Nov 14 to Dec 5. Riverside holds 180 vials and uses 6 a day, so it can send 72 and keep 18 days of supply, above its 14-day floor. One courier run closes the gap with a small buffer and needs no new supplier commitment.",
      citations: ["SE-1042", "INV-A-VASO", "INV-B-VASO", "PO-5521"],
    },
    {
      ...PLAN_DEFAULTS,
      id: "P-312",
      solverRun: "SOLVER-31",
      rank: 2,
      title: "Split transfer: 52 from Riverside, 20 from Northgate",
      transfers: [
        { from: "A", to: "B", qty: 52 },
        { from: "C", to: "B", qty: 20 },
      ],
      score: 0.86,
      cost: "≈ $280 courier",
      leadTime: "Arrives tomorrow",
      explanation:
        "Spreads the draw across two donors so neither drops below 15 days of supply. Costs a second courier run and coordination with Northgate pharmacy.",
      citations: ["SE-1042", "INV-A-VASO", "INV-C-VASO"],
    },
    {
      ...PLAN_DEFAULTS,
      id: "P-313",
      solverRun: "SOLVER-31",
      rank: 3,
      title: "Expedite 80 vials from an alternate supplier",
      transfers: [],
      orders: [{ qty: 80, etaDay: 5, supplier: "Halden Medical", premiumPct: 38 }],
      score: 0.62,
      cost: "≈ $2,900 (+38% per vial)",
      leadTime: "Arrives Nov 17",
      explanation:
        "Leaves the other sites untouched, but costs 38% more per vial and an arrival on Nov 17 leaves about one day of margin before Lakeshore runs out. The supplier has not confirmed stock.",
      citations: ["SE-1042", "INV-B-VASO"],
    },
  ];
}

function replans(): Plan[] {
  const base = { ...PLAN_DEFAULTS, solverRun: "SOLVER-32" };
  return [
    {
      ...base,
      id: "P-321",
      rank: 1,
      title: "Split transfer plus 12 expedited vials",
      transfers: [
        { from: "A", to: "B", qty: 40 },
        { from: "C", to: "B", qty: 22 },
      ],
      orders: [{ qty: 12, etaDay: 5, supplier: "Halden Medical", premiumPct: 38 }],
      score: 0.84,
      cost: "≈ $720 (2 couriers + 12 vials at +38%)",
      leadTime: "Transfers tomorrow, order Nov 17",
      status: "pending-approval",
      explanation:
        "Riverside now uses 10 vials a day, so the approved 72-vial transfer would leave it with 10.8 days, below its 14-day floor. No transfer-only plan keeps every site above its floor: Riverside can spare 40 and Northgate 22, which is 62 against the 64 Lakeshore needs. This plan takes both surpluses and covers the rest with 12 expedited vials arriving Nov 17.",
      citations: ["SE-1043", "SE-1042", "INV-A-VASO", "INV-C-VASO", "SOLVER-32"],
    },
    {
      ...base,
      id: "P-322",
      rank: 2,
      title: "Expedite 80 vials from an alternate supplier",
      transfers: [],
      orders: [{ qty: 80, etaDay: 5, supplier: "Halden Medical", premiumPct: 38 }],
      score: 0.62,
      cost: "≈ $2,900 (+38% per vial)",
      leadTime: "Arrives Nov 17",
      explanation:
        "Unchanged from the first run. Protects Riverside fully but is the most expensive option and depends on unconfirmed supplier stock.",
      citations: ["SE-1042", "INV-B-VASO"],
    },
    {
      ...base,
      id: "P-323",
      rank: 3,
      title: "Split transfer with conservation at Lakeshore",
      transfers: [
        { from: "A", to: "B", qty: 40 },
        { from: "C", to: "B", qty: 22 },
      ],
      conservationUsage: 3.8,
      score: 0.48,
      cost: "≈ $280 courier",
      leadTime: "Arrives tomorrow",
      explanation:
        "Avoids any new purchase, but Lakeshore would need to cut vasopressin use by about 5% until Dec 5. That is a clinical decision, so this is offered only as a fallback if the expedited order falls through.",
      citations: ["SE-1042", "INV-A-VASO", "INV-C-VASO"],
    },
  ];
}

function draftsFor(plan: Plan): Draft[] {
  const drug = drugById(plan.drugId);
  const drafts: Draft[] = plan.transfers.map((t, i) => ({
    id: `D-${plan.id}-T${i + 1}`,
    planId: plan.id,
    kind: "Transfer request",
    to: `${siteName(t.from)} pharmacy`,
    subject: `Transfer request: ${t.qty} ${drug.unit} ${drug.name} to ${siteName(t.to)}`,
    body: `Please prepare ${t.qty} ${drug.unit} of ${drug.name} ${drug.form} for courier pickup to ${siteName(t.to)} pharmacy.\n\nApproved under plan ${plan.id} by ${USERS.pharmacist.name}. Reference supply event ${plan.eventId}.\n\nYour site keeps enough stock to stay above its safety floor after this transfer.`,
    status: "draft",
  }));
  plan.orders.forEach((o, i) =>
    drafts.push({
      id: `D-${plan.id}-O${i + 1}`,
      planId: plan.id,
      kind: "Purchase order",
      to: o.supplier,
      subject: `Expedited order: ${o.qty} ${drug.unit} ${drug.name}`,
      body: `Requesting ${o.qty} ${drug.unit} of ${drug.name} ${drug.form}, delivered to Lakeshore Community by ${dayLabel(o.etaDay)}.\n\nPlease confirm availability and price before shipping. Reference plan ${plan.id}.`,
      status: "draft",
    }),
  );
  return drafts;
}

function approve(state: State, planId: string, note: string): State {
  const plan = state.plans.find((p) => p.id === planId);
  if (!plan || plan.status !== "pending-approval") return state;
  const nextStep = plan.solverRun === "SOLVER-31" && state.step === 2 ? 3 : plan.solverRun === "SOLVER-32" && state.step === 5 ? 6 : state.step;
  let s: State = {
    ...state,
    step: nextStep,
    clockMin: STEP_CLOCK[nextStep] ?? state.clockMin,
    plans: state.plans.map((p) => (p.id === planId ? { ...p, status: "approved", decisionNote: note } : p)),
    drafts: [...draftsFor(plan), ...state.drafts],
  };
  if (nextStep === 6) {
    s.events = s.events.map((e) => (e.id === "SE-1042" || e.id === "SE-1043" ? { ...e, status: "mitigated" } : e));
  }
  s = audit(s, {
    actor: "pharmacist",
    actorName: USERS.pharmacist.name,
    action: "Approved",
    object: plan.id,
    detail: `${plan.title}${note ? `. Note: "${note}"` : ""}`,
    refs: [plan.solverRun, ...plan.citations.slice(0, 2)],
  });
  s = audit(s, {
    actor: "agent",
    actorName: "drafting",
    action: "Drafts created",
    object: plan.id,
    detail: `${plan.transfers.length + plan.orders.length} draft(s) for procurement. Nothing is sent automatically`,
    refs: [plan.id],
  });
  return s;
}

function reducer(state: State, action: Action): State {
  if (action.type === "load") {
    const { data } = action;
    return {
      ...state,
      live: {
        asOf: data.asOf,
        days: data.days,
        daysRun: data.daysRun ?? [],
        inbox: data.inbox ?? [],
        loadedDays: data.loadedDays ?? [],
        dayReports: data.dayReports ?? {},
        followups: data.followups ?? [],
      },
      step: 0,
      clockMin: 0,
      stock: data.stock,
      events: data.events,
      emails: data.emails,
      plans: data.plans,
      audit: data.audit,
      drafts: [],
    };
  }
  // In live mode the story steps don't apply, and decisions go through the API (see StoreProvider).
  if (state.live && action.type !== "role") return state;
  switch (action.type) {
    case "reset":
      return initialState(state.role);
    case "role":
      return { ...state, role: action.role };
    case "approve":
      return approve(state, action.planId, action.note);
    case "reject": {
      const plan = state.plans.find((p) => p.id === action.planId);
      if (!plan) return state;
      const s = { ...state, plans: state.plans.map((p) => (p.id === plan.id ? { ...p, status: "rejected" as const, decisionNote: action.note } : p)) };
      return audit(s, { actor: "pharmacist", actorName: USERS.pharmacist.name, action: "Rejected", object: plan.id, detail: action.note || "No reason given", refs: [plan.solverRun] });
    }
    case "route": {
      const s = {
        ...state,
        plans: state.plans.map((p) =>
          p.id === action.planId
            ? { ...p, status: "pending-approval" as const }
            : p.status === "pending-approval"
              ? { ...p, status: "proposed" as const }
              : p,
        ),
      };
      return audit(s, { actor: "supply", actorName: USERS.supply.name, action: "Routed for approval", object: action.planId, detail: "Sent to Lakeshore site pharmacist", refs: [action.planId] });
    }
    case "markSent": {
      const draft = state.drafts.find((d) => d.id === action.draftId);
      const s = { ...state, drafts: state.drafts.map((d) => (d.id === action.draftId ? { ...d, status: "sent-manually" as const } : d)) };
      return audit(s, { actor: "procurement", actorName: USERS.procurement.name, action: "Marked sent", object: action.draftId, detail: draft ? `${draft.kind} to ${draft.to}, sent outside the platform` : "", refs: draft ? [draft.planId] : [] });
    }
    case "next":
      return advance(state);
  }
}

function advance(state: State): State {
  const step = state.step + 1;
  if (step >= STEPS.length) return state;
  let s: State = { ...state, step, clockMin: STEP_CLOCK[step] };

  if (step === 1) {
    s.emails = [SUPPLIER_EMAIL, ...s.emails];
    s.stock = s.stock.map((l) =>
      l.drugId === "vaso" && l.siteId === "B"
        ? { ...l, orders: l.orders.map((o) => ({ ...o, status: "delayed" as const, originalEtaDay: o.etaDay, etaDay: 23 })) }
        : l,
    );
    s.events = [
      {
        id: "SE-1042",
        drugId: "vaso",
        siteId: "B",
        kind: "delay",
        title: "Vasopressin: PO 5521 delayed to Dec 5",
        summary: "120 vials for Lakeshore slipped 21 days. Lakeshore runs out on Nov 18 without action.",
        source: "supplier-email",
        sourceRef: "EMAIL-0193",
        confidence: 0.92,
        atMin: 14,
        status: "open",
        resupplyDay: 23,
      },
      ...s.events,
    ];
    s = audit(s, { actor: "system", actorName: "mail-ingest", action: "Email received", object: "EMAIL-0193", detail: "From Northbridge Pharma, labelled synthetic", refs: ["EMAIL-0193"] });
    s = audit(s, { actor: "agent", actorName: "notice-extraction", action: "Extracted", object: "SE-1042", detail: "Delivery delay, confidence 0.92 (auto-accept threshold 0.85). Output passed schema validation", refs: ["EMAIL-0193", "PO-5521"] });
    s = audit(s, { actor: "system", actorName: "risk", action: "Risk recomputed", object: "Lakeshore / Vasopressin", detail: "Coverage 36+ days to 6.0 days. Tier green to red", refs: ["INV-B-VASO", "USE-B-VASO", "SE-1042"] });
  }

  if (step === 2) {
    s.plans = firstPlans();
    s = audit(s, { actor: "solver", actorName: "planner", action: "Plans ranked", object: "SOLVER-31", detail: "3 feasible plans. Hard constraints: donor safety floor, no stockout before resupply, approved substitutions only", refs: ["SOLVER-31", "SE-1042"] });
    s = audit(s, { actor: "agent", actorName: "plan-explanation", action: "Explained", object: "SOLVER-31", detail: "Every number in the explanations matches solver output (automated check passed)", refs: ["SOLVER-31"] });
    s = audit(s, { actor: "system", actorName: "router", action: "Routed for approval", object: "P-311", detail: "Transfers need the receiving site pharmacist. Sent to Priya Nair", refs: ["P-311"] });
  }

  if (step === 3) {
    const pending = s.plans.find((p) => p.status === "pending-approval");
    return pending ? approve(state, pending.id, "Confirmed with Riverside by phone") : s;
  }

  if (step === 4) {
    s.stock = s.stock.map((l) => (l.drugId === "vaso" && l.siteId === "A" ? { ...l, dailyUsage: 10 } : l));
    s.events = [
      {
        id: "SE-1043",
        drugId: "vaso",
        siteId: "A",
        kind: "usage-spike",
        title: "Vasopressin: Riverside usage up 67%",
        summary: "ICU census increase. Riverside usage rose from 6 to 10 vials/day.",
        source: "site-report",
        sourceRef: "SITE-RPT-A-77",
        confidence: 1,
        atMin: 200,
        status: "open",
      },
      ...s.events,
    ];
    s.plans = s.plans.map((p) => (p.status === "approved" ? { ...p, status: "broken" } : p));
    s = audit(s, { actor: "system", actorName: "site-report", action: "Usage updated", object: "Riverside / Vasopressin", detail: "6 to 10 vials/day", refs: ["SITE-RPT-A-77"] });
    s = audit(s, { actor: "solver", actorName: "planner", action: "Constraint violated", object: "P-311", detail: "Riverside would keep 10.8 days after the transfer, below its 14-day floor. Plan marked broken before shipment", refs: ["P-311", "SE-1043"] });
  }

  if (step === 5) {
    s.plans = [
      ...replans(),
      ...s.plans.map((p) => (p.status === "broken" || p.status === "proposed" || p.status === "pending-approval" ? { ...p, status: "superseded" as const } : p)),
    ];
    s = audit(s, { actor: "solver", actorName: "planner", action: "Re-planned", object: "SOLVER-32", detail: "No transfer-only plan satisfies every hard constraint. 3 ranked fallbacks returned", refs: ["SOLVER-32", "SE-1043"] });
    s = audit(s, { actor: "agent", actorName: "plan-explanation", action: "Explained", object: "SOLVER-32", detail: "Every number in the explanations matches solver output (automated check passed)", refs: ["SOLVER-32"] });
    s = audit(s, { actor: "system", actorName: "router", action: "Routed for approval", object: "P-321", detail: "Includes a purchase, so procurement is notified on approval", refs: ["P-321"] });
  }

  if (step === 6) {
    const pending = s.plans.find((p) => p.status === "pending-approval");
    return pending ? approve(state, pending.id, "Expedited order confirmed by Halden") : s;
  }

  return s;
}

export interface LiveControls {
  /** "connecting" until the API answers; "offline" means the scripted story is shown. */
  mode: "connecting" | "live" | "offline";
  busy: string | null;
  error: string | null;
  notice: string | null;
  setDay: (asOf: string) => void;
  refresh: () => Promise<void>;
  runEmail: (name: string) => Promise<void>;
  runFollowup: (now: string) => Promise<void>;
  /** Run one simulated day, then move to the next day. */
  runDay: (day: string, planner: Planner) => Promise<boolean>;
  /** Supply manager: plan a response for one drug now. Resolves to the alert to open. */
  planDrug: (din: string, siteId?: string) => Promise<string | null>;
  /** Procurement / supply manager: record what happened to an approved order (placed, received). */
  recordOrder: (orderId: string, status: OrderStatus) => Promise<void>;
  releaseOrder: (orderId: string) => Promise<void>;
  lastDay: DaySummary | null;
  clear: () => void;
}

const StoreContext = createContext<{ state: State; dispatch: (a: Action) => void; live: LiveControls } | null>(null);

/** `?step=4&role=pharmacist` jumps straight to a scenario step, for recorded demos. */
function stateFromUrl(): State {
  const params = new URLSearchParams(window.location.search);
  const role = params.get("role") as Role | null;
  let s = initialState(role && role in USERS ? role : "supply");
  const step = Math.min(Number(params.get("step")) || 0, STEPS.length - 1);
  while (s.step < step) s = advance(s);
  return s;
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [state, baseDispatch] = useReducer(reducer, undefined, stateFromUrl);
  const forceDemo = new URLSearchParams(window.location.search).get("demo") === "1";
  const [mode, setMode] = useState<LiveControls["mode"]>(forceDemo ? "offline" : "connecting");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [lastDay, setLastDay] = useState<DaySummary | null>(null);
  const stateRef = useRef(state);
  stateRef.current = state;

  const load = useCallback(async (asOf?: string) => {
    const data = await api.state(asOf);
    applyLiveData(data);
    baseDispatch({ type: "load", data });
    setMode("live");
  }, []);

  useEffect(() => {
    if (forceDemo) return;
    load().catch(() => setMode("offline")); // no API: keep the scripted story
  }, [forceDemo, load]);

  /** Run an action, then reload the data. reload=false when the action itself loaded it
   *  (e.g. changing the day: reloading with the old day would undo the change). */
  const run = useCallback(async (label: string, work: () => Promise<string | void>, reload = true) => {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const message = await work();
      if (reload) await load(stateRef.current.live?.asOf);
      if (message) setNotice(message);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [load]);

  const dispatch = useCallback((action: Action) => {
    const current = stateRef.current;
    if (!current.live || (action.type !== "approve" && action.type !== "reject")) {
      baseDispatch(action);
      return;
    }
    const by = USERS[current.role].name;
    const asOf = current.live.asOf;
    if (action.type === "approve") {
      void run("Approving", async () => {
        const result = await api.approve(action.planId, { by, role: current.role, note: action.note || undefined, as_of: asOf });
        if (!result.approved) return `Approval refused: the plan is no longer safe. ${(result.problems ?? []).join(" ")}`;
        return "Approved and recorded.";
      });
    } else {
      void run("Rejecting", async () => {
        await api.reject(action.planId, { by, role: current.role, note: action.note, as_of: asOf });
        return "Rejected and recorded.";
      });
    }
  }, [run]);

  const live = useMemo<LiveControls>(() => ({
    mode,
    busy,
    error,
    notice,
    setDay: (asOf) => void run("Loading day", async () => { await load(asOf); }, false),
    refresh: () => run("Refreshing", async () => {}),
    runEmail: (name) => run(`Running the agents on ${name}`, async () => {
      const result = await api.runEmail(name, stateRef.current.live?.asOf);
      return `Agents finished: ${result.status.replace(/_/g, " ")}.`;
    }),
    runFollowup: (now) => run("Running follow-up", async () => {
      const actions = await api.runFollowup(now, stateRef.current.live?.asOf);
      return actions.length ? actions.map((a) => `${a.request_id}: ${a.decision}`).join("; ") : "No approved transfers or orders to follow up.";
    }),
    runDay: async (day, planner) => {
      let ok = false;
      await run(`Running ${day}: inbox, stock watch, planning, follow-up`, async () => {
        const summary = await api.runDay(day, planner);
        setLastDay(summary);
        const days = stateRef.current.live?.days ?? [];
        const next = days[days.indexOf(day) + 1];
        await load(next ?? day); // move the clock on to the next day
        ok = true;
      }, false);
      return ok;
    },
    planDrug: async (din, siteId) => {
      let eventId: string | null = null;
      const current = stateRef.current;
      await run("Planning a response", async () => {
        const result = await api.planDrug(din, {
          by: USERS[current.role].name, role: current.role, as_of: current.live?.asOf ?? "", site_id: siteId,
        });
        eventId = result.event_id;
        if (result.status === "already_open") return "This drug already has an open alert, so here is its plan.";
        return `Plan ready (${result.planner ?? "planner"}): ${result.status.replace(/_/g, " ")}.`;
      });
      return eventId;
    },
    recordOrder: (orderId, status) => run("Recording the order", async () => {
      const current = stateRef.current;
      await api.orderStatus(orderId, { by: USERS[current.role].name, role: current.role, status, as_of: current.live?.asOf });
      return `Order ${orderId} recorded as ${status === "delivered" ? "received" : status}.`;
    }),
    releaseOrder: (orderId) => run("Releasing the order", async () => {
      const current = stateRef.current;
      const result = await api.releaseOrder(orderId, { by: USERS[current.role].name, role: current.role, as_of: current.live?.asOf });
      const last = result.attempts[result.attempts.length - 1];
      return last ? `Order ${orderId}: ${last.note}` : `Order ${orderId} released.`;
    }),
    lastDay,
    clear: () => { setError(null); setNotice(null); },
  }), [mode, busy, error, notice, lastDay, run, load]);

  return <StoreContext.Provider value={{ state, dispatch, live }}>{children}</StoreContext.Provider>;
}

export function useStore() {
  const ctx = useContext(StoreContext);
  if (!ctx) throw new Error("useStore must be used inside StoreProvider");
  return ctx;
}
