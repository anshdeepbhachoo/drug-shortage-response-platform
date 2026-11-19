# MediFlow — project overview

MediFlow is an agentic AI platform for hospital drug-shortage detection, coordination, allocation planning, and auditable response (Cognizant BrAInstorm Challenge 2026).

This document describes the **implemented** product on `main`. For setup and day-to-day commands, see the root [README.md](../README.md). For contribution rules, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## 1. Summary

MediFlow takes a multi-site hospital network from a drug shortage signal to an approved, auditable response. It ingests public Health Canada shortage and drug-product data, loads synthetic hospital stock and supplier notices, calculates days of supply in code, proposes checked response options (transfers, orders, combined options, substitutions from an approved table), and routes each plan to the right human role. AI agents handle language-heavy steps when Amazon Bedrock is configured; deterministic rules cover planning when it is not. Humans approve every action. Real outbound email and live hospital/supplier system submission are intentionally out of scope for the MVP.

## 2. Problem

Shortages of critical hospital drugs recur in Canada, and coordination after a warning is still mostly manual: email, spreadsheets, and phone calls across sites.

MediFlow targets the step after the warning—gathering evidence, proposing feasible transfers and orders, getting role-appropriate approvals, following up, and leaving an audit trail—without claiming to replace clinical judgment or production pharmacy systems.

## 3. What is implemented today

| Capability | Implementation notes |
|---|---|
| Health Canada DPD + shortage API ingestion | Real HTTP clients, lineage, quarantine, Alembic migrations |
| Synthetic hospital network | **6 sites**, **22 synthetic DINs** (`9900xxxx`), inventory, usage, open orders, supplier emails |
| 10-day shortage progression | Deterministic snapshots under `scenarios/progression/` (Nov 15–24, 2026) |
| Daily simulated run | Ingest → inbox → stock watch → shortage signals → plan → follow-up |
| Signal assessment + planning | Bedrock optional; rules planner fallback |
| Approvals | Role rules, stock re-check, one decision per plan |
| Transfer follow-up | Evening checks: wait / nudge / escalate / replan / close |
| Orders | Order options, replies, follow-up, multi-channel **orchestrator with simulated submission** |
| Escalations / notifications | Role-targeted; shown on Overview |
| API + React console | FastAPI (`apps/api`) and Vite/React UI (`apps/web`) |
| LangGraph orchestration | Alert pipeline and daily schedule graphs; classic orchestrator still available |

## 4. Users

| Role | Needs | Primary views |
|---|---|---|
| Supply manager | Network risk and plans | Overview, Shortage Alerts, Stock, Approvals |
| Site pharmacist | Trustworthy approval of clinical options | Approvals, Signal Review |
| Procurement | Orders and supplier-facing follow-through | Messages, Overview notifications, order release |
| Auditor / reviewer | Traceability | History, Data |

## 5. Data sources

| Source | Status |
|---|---|
| Health Product Shortages Canada API | Real (account credentials in `.env`) |
| Health Canada Drug Product Database API | Real |
| Synthetic inventory, usage, orders, emails | Generated fixtures; clearly labelled |
| Substitution table | Hand-built demo rows; review-required, not clinical approvals |

No real patient data is used.

## 6. Architecture (as built)

```
Sources (HC APIs + synthetic CSVs/emails)
  → ingestion / validation / lineage (Postgres)
  → daily run + LangGraph alert pipeline
  → risk & checks (code) + agents (optional LLM)
  → approvals / orders / follow-up / escalations
  → FastAPI → React console
```

Details: root [README.md](../README.md) (LangGraph diagrams), [services/agents/README.md](../services/agents/README.md), [docs/API.md](API.md).

## 7. Real vs simulated vs not built

| Real | Simulated / synthetic | Not built (by design for MVP) |
|---|---|---|
| HC shortage + DPD ingestion | Hospital stock, usage, orders, emails | Live SAP/EDI/portal submission |
| Constraint checks, risk math, audit log | Order channel payloads and replies | Sending email to real suppliers |
| Approvals and role rules | Multi-site demo network | EHR / pharmacy system integration |
| FastAPI + React UI | Progression story ledger events | Production SSO / compliance claims |

Order placement defaults to simulation when `ORDER_SUBMISSION` is unset or set to `simulate` (code default in `services/agents/ordering/orchestrator.py`). In that mode MediFlow builds and audits order payloads but **does not submit a live supplier order**. Health Canada API calls and optional Amazon Bedrock requests are separate integrations and are unaffected by this setting. The variable is optional and is not required in `.env.example`.

## 8. Scope notes for reviewers

**In scope for the demo:** end-to-end shortage response on synthetic hospital data with optional Bedrock, rules fallback, approvals, follow-up, simulated ordering, and auditability.

**Out of scope:** real PHI, autonomous clinical substitutions, and production hosting claims.

## 9. Related docs

- [README.md](../README.md) — setup, UI map, tests
- [docs/SYNTHETIC_DATA.md](SYNTHETIC_DATA.md) — mock and progression datasets
- [docs/FOLLOW_UP_AGENT.md](FOLLOW_UP_AGENT.md) — transfer follow-up design
- [docs/TEAM_AND_WORKFLOW.md](TEAM_AND_WORKFLOW.md) — current pointers + historical planning notes
- [docs/DEMO.md](DEMO.md) — 40-second epinephrine demo runbook

## 10. References

- CSHP Advocacy in Action, March 2026 and January 2026: cshp.ca (shortage updates)
- Health Canada Critical and Vulnerable Drug List: canada.ca, drug shortages section
- Blakes, "Health Canada proposes regulations to mitigate drug and medical device shortages": blakes.com
- Health Canada shortage site API documentation: healthproductshortages.ca/blog/61
- Drug Product Database API guide: health-products.canada.ca/api/documentation/dpd-documentation-en.html
- MaaTRx coverage: CTV News, "MaaTRx: a platform that predicts when your medication is about to run out"
- Sierra Leone allocation pilot: Penn Today, "Transforming healthcare logistics with low-cost AI"
