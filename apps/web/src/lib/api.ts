/** Client for the platform API (apps/api). Vite proxies /api to it in development. */
import type { LiveCatalog } from "../data/mock";
import type { AuditEntry, Email, Plan, Role, StockLine, SupplyEvent } from "./types";

export interface LiveState extends LiveCatalog {
  asOf: string;
  days: string[];
  stock: StockLine[];
  events: SupplyEvent[];
  plans: Plan[];
  emails: Email[];
  audit: AuditEntry[];
  daysRun: string[];
  inbox: string[];
  loadedDays: string[];
  dayReports?: Record<string, DaySummary>;
  followups?: FollowUp[];
}

/** One approved transfer and the follow-up agent's latest check on it. */
export type OrderStatus = "approved" | "placed" | "in_transit" | "delayed" | "delivered" | "cancelled";

export interface FollowUp {
  id: string;
  /** A transfer between hospitals, or an order from a supplier (from = the supplier). */
  kind?: "transfer" | "order";
  drugId: string;
  drug: string;
  from: string;
  to: string;
  units: number;
  approvedAt: string;
  /** When follow-up expects the transfer to have arrived. */
  expectedBy?: string;
  decision: "wait" | "nudge" | "escalate" | "replan" | "close" | null;
  status: string;
  confirmed?: number | null;
  hoursOverdue?: number | null;
  checkedAt?: string | null;
  decidedBy?: "agent" | "rule" | "fallback" | null;
  reason: string;
  /** Orders only. */
  orderStatus?: OrderStatus;
  orderStatusText?: string;
  messageTo?: string | null;
  messageDraft?: string | null;
  /** The order agent's request to the supplier, for a person to send. */
  request?: { to: string; subject: string; body: string; decidedBy: "agent" | "rule" } | null;
  /** Orders only: supplier replies the order agent has read, oldest first. */
  replies?: SupplierReply[];
}

export interface SupplierReply {
  from: string;
  receivedAt: string;
  /** Untrusted supplier text, shown as is. */
  text: string;
  /** What the reader extracted, after code checks; null when the reply was flagged or could not be read. */
  kind: "confirmed" | "partial" | "delayed" | "backordered" | "cancelled" | "question" | "unrelated" | null;
  summary: string | null;
  readBy?: "agent" | "rule" | null;
  /** Why a person must read it: security screen, failed checks. Empty when the reading was accepted. */
  problems: string[];
}

export interface DaySummary {
  day: string;
  planner: string;
  ingested: number;
  emails: { email: string; status: string }[];
  stock_watch: { alerts: { drug: string; sites: string[]; kind: string; status: string }[]; waiting: number };
  shortage_reports: { reports: number } & Record<string, number>;
  followup: { transfer: string; decision: string }[];
  waiting_for_approval: number;
}

export type Planner = "auto" | "model" | "rules";

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* not JSON: keep the status text */
    }
    throw new ApiError(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  state: (asOf?: string) => call<LiveState>(`/state${asOf ? `?as_of=${asOf}` : ""}`),
  emails: () => call<string[]>("/emails"),
  approve: (planId: string, body: { by: string; role: Role; note?: string; as_of?: string }) =>
    call<{ approved: boolean; status: string; problems?: string[] }>(`/plans/${encodeURIComponent(planId)}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  reject: (planId: string, body: { by: string; role: Role; note: string; as_of?: string }) =>
    call<{ status: string }>(`/plans/${encodeURIComponent(planId)}/reject`, { method: "POST", body: JSON.stringify(body) }),
  runEmail: (email: string, asOf?: string) =>
    call<{ status: string; reason: unknown }>("/agent/run", { method: "POST", body: JSON.stringify({ email, as_of: asOf }) }),
  runDay: (day: string, planner: Planner) =>
    call<DaySummary>("/day/run", { method: "POST", body: JSON.stringify({ day, planner }) }),
  planDrug: (din: string, body: { by: string; role: Role; as_of: string; site_id?: string; planner?: Planner }) =>
    call<{ status: string; event_id: string | null; plan_id?: string | null; planner?: string }>(
      `/drugs/${encodeURIComponent(din)}/plan`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  orderStatus: (orderId: string, body: { by: string; role: Role; status: OrderStatus; expected_date?: string; as_of?: string }) =>
    call<{ status: string }>(`/orders/${encodeURIComponent(orderId)}/status`, { method: "POST", body: JSON.stringify(body) }),
  runFollowup: (now: string, asOf?: string) =>
    call<{ request_id: string; decision: string; reason: string }[]>("/followup/run", {
      method: "POST",
      body: JSON.stringify({ now, as_of: asOf }),
    }),
};
