import { useSyncExternalStore } from "react";
import data from "../data/orders.json";
import type { Role } from "./types";

/**
 * Orders page data.
 * - Live: the platform API (apps/api, GET /api/orders) reads Postgres for the chosen day.
 *   Approve / reject go through the order agent, so its checks and audit log apply.
 *   The agent itself runs as part of "Run day" (services/agents/daily_run.py).
 * - Fallback: orders.json, written by the order agent itself
 *     python -m services.agents.order_demo --export
 *   Decisions made then are kept in this browser only.
 * Nothing reaches a real supplier either way.
 */

export type OrderStatus = "pending_approval" | "approved" | "placed" | "in_transit" | "delayed" | "delivered" | "cancelled";

export interface PurchaseOrder {
  id: string;
  kind: "routine" | "shortage";
  drug: string;
  din: string;
  site: string;
  siteName: string;
  supplier: string;
  supplierName: string;
  qty: number;
  confirmedQty: number;
  receivedQty: number;
  unitCost: number;
  totalCost: number;
  status: OrderStatus;
  neededBy: string | null;
  expected: string | null;
  approvedBy: string | null;
  approvedRole: string | null;
  replaces: string | null;
  planId: string | null;
  reason: string | null;
  urgent: boolean;
  daysLeft: number | null;
  chases: number;
  escalated: boolean;
  /** Supplier lead time in days (estimates arrival for orders not sent yet). */
  leadDays?: number | null;
}

export interface OrderEvent {
  at: string;
  order: string;
  who: string; // human | rule | agent | fallback
  action: string;
  text: string;
  by?: string;
  statusBefore?: string;
  statusAfter?: string;
  to?: string | null;
  message?: string | null;
  sentVia?: string | null;
  flags?: string[];
  newOrder?: string | null;
  event?: string | null;
  needsHuman?: boolean;
}

export interface OrderData {
  source: string;
  mode: "live" | "offline" | "database";
  asOf: string;
  lastCheck: string | null;
  procurementLimit: number;
  sites: Record<string, string>;
  orders: PurchaseOrder[];
  events: OrderEvent[];
  outbox: number;
  /** false: no supplier catalog in the database yet (order_db load-catalog). */
  catalog?: boolean;
}

/** The saved export, used when the orders API isn't running. */
export const FILE_DATA = data as unknown as OrderData;

export interface LocalDecision {
  result: "approved" | "rejected";
  by: string;
  role: Role;
  note: string;
  at: string;
}

/* ---------- one shared store: where the data comes from, plus decisions ---------- */

export interface OrdersState {
  /** "database": live from the orders API. "file": the saved export (decisions stay in this browser). */
  backend: "loading" | "database" | "file";
  data: OrderData;
  /** Why we fell back to the file, if we did. */
  offlineReason: string | null;
  decisions: Record<string, LocalDecision>;
  busy: boolean;
}

const KEY = "order-decisions";
let state: OrdersState = { backend: "loading", data: FILE_DATA, offlineReason: null, decisions: loadLocal(), busy: false };
const listeners = new Set<() => void>();

function set(patch: Partial<OrdersState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

function loadLocal(): Record<string, LocalDecision> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "{}");
  } catch {
    return {};
  }
}

function saveLocal(next: Record<string, LocalDecision>) {
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    /* storage unavailable: decisions last for this visit only */
  }
  set({ decisions: next });
}

async function api<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/api${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const type = res.headers.get("content-type") ?? "";
  const json = type.includes("application/json") ? await res.json() : null;
  if (!res.ok || json === null) throw new Error(typeof json?.detail === "string" ? json.detail : "The platform API is not running.");
  return json as T;
}

let lastAsOf: string | undefined;

/** Load from the database through the API (for the chosen day); fall back to the saved file if it isn't running. */
export async function refreshOrders(asOf: string | undefined = lastAsOf) {
  lastAsOf = asOf;
  try {
    const live = await api<OrderData>(`/orders${asOf ? `?as_of=${asOf}` : ""}`);
    set({ backend: "database", data: live, offlineReason: null });
  } catch (error) {
    set({ backend: "file", data: FILE_DATA, offlineReason: error instanceof Error ? error.message : String(error) });
  }
}

/** Approve or reject. Returns an error message, or null when it worked. */
export async function decide(id: string, d: LocalDecision): Promise<string | null> {
  if (state.backend !== "database") {
    saveLocal({ ...state.decisions, [id]: d });
    return null;
  }
  set({ busy: true });
  try {
    await api(`/orders/${encodeURIComponent(id)}/${d.result === "approved" ? "approve" : "reject"}`, {
      by: d.by,
      role: d.role === "supply" ? "supply_manager" : d.role,
      note: d.note,
      as_of: lastAsOf,
    });
    await refreshOrders();
    return null;
  } catch (error) {
    return error instanceof Error ? error.message : String(error);
  } finally {
    set({ busy: false });
  }
}

export function clearDecisions() {
  saveLocal({});
}

export function useOrders(): OrdersState {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => state,
  );
}

/** The order as it stands once this browser's decisions are applied (file mode only). */
export function withDecision(o: PurchaseOrder, d?: LocalDecision): PurchaseOrder {
  if (!d || o.status !== "pending_approval") return o;
  return d.result === "approved"
    ? { ...o, status: "approved", approvedBy: d.by, approvedRole: d.role === "supply" ? "supply_manager" : d.role }
    : { ...o, status: "cancelled" };
}

/** Same rule as order_rules.approval_problems: who may approve how much. */
export function approvalBlock(o: PurchaseOrder, role: Role, limit: number): string | null {
  if (role === "supply") return null;
  if (role !== "procurement") return "Only procurement or the supply manager can approve orders.";
  if (o.totalCost > limit) return `Over $${limit.toLocaleString()}: the supply manager must approve this one.`;
  return null;
}

export function pendingCount(s: OrdersState): number {
  const local = s.backend === "file" ? s.decisions : {};
  return s.data.orders.filter((o) => o.status === "pending_approval" && !local[o.id]).length;
}

/* ---------- display helpers ---------- */

export type Group = "waiting" | "moving" | "person" | "done";

export function needsPerson(o: PurchaseOrder): boolean {
  return o.escalated && o.status !== "delivered" && o.status !== "cancelled";
}

export function groupOf(o: PurchaseOrder): Group {
  if (o.status === "pending_approval") return "waiting";
  if (needsPerson(o)) return "person";
  if (o.status === "delivered" || o.status === "cancelled") return "done";
  return "moving";
}

export const STATUS_WORDS: Record<OrderStatus, string> = {
  pending_approval: "Waiting for approval",
  approved: "Approved, not sent yet",
  placed: "Sent to supplier",
  in_transit: "On the way",
  delayed: "Delayed",
  delivered: "Delivered",
  cancelled: "Cancelled",
};

export const ACTION_WORDS: Record<string, string> = {
  drafted: "Drafted the order",
  created: "Created it from an approved plan",
  approved: "Approved",
  rejected: "Rejected",
  place: "Sent the order to the supplier",
  accept: "Recorded the supplier's update",
  chase: "Sent the supplier a reminder",
  record_delay: "Recorded a delay",
  switch_supplier: "Switched to another supplier",
  escalate: "Passed it to a person",
  close: "Marked it delivered",
};

export const WHO_WORDS: Record<string, string> = {
  human: "Person",
  rule: "Fixed rule",
  agent: "AI agent",
  fallback: "Backup rule",
};

export function when(iso: string): string {
  const d = new Date(iso);
  const day = d.toLocaleDateString("en-CA", { month: "short", day: "numeric" });
  const time = d.toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} ${time}`;
}

export function dateLabel(iso: string | null): string {
  if (!iso) return "—";
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("en-CA", { month: "short", day: "numeric" });
}

export const money = (n: number) => `$${n.toLocaleString("en-CA", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
