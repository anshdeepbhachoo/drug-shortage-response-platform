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
  notifications?: Notification[];
}

/** Something that needs a person, addressed to the role that acts on it (code chose the role). */
export interface Notification {
  id: string;
  toRole: Role;
  /** Who raised it: "Order agent", "Follow-up agent", "Order orchestrator", or the person's name. */
  from: string;
  title: string;
  body: string;
  objectId: string;
  kind: "order" | "transfer";
  drugId: string;
  about: string;
  at: string | null;
  read: boolean;
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
  /** Orders only: every step the order orchestrator agent took to place it, oldest first. */
  submissions?: OrderSubmission[];
  /** Orders only: the orchestrator agent's own summary (or the rules', when they finished the job). */
  orchestration?: { decided_by: "agent" | "rule"; summary: string; outcome: string | null } | null;
}

export interface OrderSubmission {
  channel: string;
  /** The system it went to, e.g. "Hospital ERP: SAP S/4HANA". */
  target: string;
  /** simulate: built and checked, the answer simulated; nothing left the machine. */
  mode: "simulate" | "live";
  status: "accepted" | "rejected" | "failed" | "held" | "ready_to_send" | "escalated";
  externalRef: string | null;
  note: string | null;
  at: string;
  /** The exact payload built for that system (SAP JSON, EDI segments, portal JSON, email). */
  payload: unknown;
  /** Who sent the order this way: the orchestrator agent, or the rules (no model, or the agent failed). */
  chosenBy: "agent" | "rule";
  /** The orchestrator's brief to the sub-agent: why this channel. */
  reason: string | null;
  /** The channel sub-agent's report: how it read the system's answer. */
  report: { outcome: string; summary: string; read_by: "agent" | "rule"; steps: string[]; flags: string[] } | null;
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
  releaseOrder: (orderId: string, body: { by: string; role: Role; as_of?: string }) =>
    call<{ attempts: { target: string; status: string; external_ref: string | null; note: string }[] }>(
      `/orders/${encodeURIComponent(orderId)}/release`,
      { method: "POST", body: JSON.stringify(body) },
    ),
  escalate: (objectId: string, body: { by: string; role: Role; message: string; as_of?: string }) =>
    call<{ to_role: string }>(`/escalations/${encodeURIComponent(objectId)}`, { method: "POST", body: JSON.stringify(body) }),
  readNotification: (id: string, body: { by: string; role: Role; as_of?: string }) =>
    call<{ read: string }>(`/notifications/${encodeURIComponent(id)}/read`, { method: "POST", body: JSON.stringify(body) }),
  runFollowup: (now: string, asOf?: string) =>
    call<{ request_id: string; decision: string; reason: string }[]>("/followup/run", {
      method: "POST",
      body: JSON.stringify({ now, as_of: asOf }),
    }),
};
