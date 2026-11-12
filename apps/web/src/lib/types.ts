export type Role = "supply" | "pharmacist" | "procurement" | "auditor";
export type Actor = Role | "system" | "agent" | "solver";
export type Tier = "green" | "amber" | "red";

export interface Site {
  id: string;
  name: string;
  short: string;
}

export interface Drug {
  id: string;
  name: string;
  form: string;
  unit: string;
  criticalList: boolean;
  dpdRef: string;
}

export interface Order {
  id: string;
  qty: number;
  etaDay: number;
  supplier: string;
  status: "on-time" | "delayed" | "expedited";
  originalEtaDay?: number;
}

export interface StockLine {
  siteId: string;
  drugId: string;
  onHand: number;
  dailyUsage: number;
  safetyDays: number;
  orders: Order[];
  inventoryRef: string;
  usageRef: string;
}

export interface SupplyEvent {
  id: string;
  drugId: string;
  siteId?: string;
  kind: "delay" | "shortage" | "usage-spike";
  title: string;
  summary: string;
  source: "supplier-email" | "hc-shortage-api" | "site-report";
  sourceRef: string;
  confidence: number;
  atMin: number;
  status: "open" | "mitigated" | "monitoring";
  resupplyDay?: number;
}

export interface Transfer {
  from: string;
  to: string;
  qty: number;
}

export interface PlanOrder {
  qty: number;
  etaDay: number;
  supplier: string;
  premiumPct: number;
}

export interface ConstraintCheck {
  label: string;
  ok: boolean;
  hard: boolean;
  detail: string;
}

export type PlanStatus =
  | "proposed"
  | "pending-approval"
  | "approved"
  | "rejected"
  | "broken"
  | "superseded";

export interface Plan {
  id: string;
  eventId: string;
  drugId: string;
  solverRun: string;
  rank: number;
  title: string;
  transfers: Transfer[];
  orders: PlanOrder[];
  conservationUsage?: number;
  score: number;
  cost: string;
  leadTime: string;
  explanation: string;
  citations: string[];
  status: PlanStatus;
  decisionNote?: string;
}

export interface Extraction {
  eventType: string;
  drug: string;
  drugMatch: string;
  poRef?: string;
  qty?: number;
  originalEta?: string;
  newEta?: string;
  site?: string;
  confidence: number;
  excerpt: string;
  flags: string[];
}

export interface Email {
  id: string;
  from: string;
  subject: string;
  atMin: number;
  body: string;
  outcome: "accepted" | "quarantined";
  extraction: Extraction;
}

export interface Draft {
  id: string;
  planId: string;
  kind: "Transfer request" | "Purchase order" | "Site notice";
  to: string;
  subject: string;
  body: string;
  status: "draft" | "sent-manually";
}

export interface AuditEntry {
  id: string;
  atMin: number;
  actor: Actor;
  actorName: string;
  action: string;
  object: string;
  detail: string;
  refs: string[];
}

export interface SourceRecord {
  id: string;
  kind: string;
  label: string;
  detail: string;
  origin: "real (cached)" | "synthetic" | "platform";
  table?: string;
  sha256?: string;
}
