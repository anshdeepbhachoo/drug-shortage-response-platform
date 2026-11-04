# [Platform name]: drug shortage response platform

Items in [brackets] are placeholders. Items marked TBC need confirming before anyone relies on them.

## 1. Summary

An end-to-end platform that takes a hospital network from a drug shortage signal to an approved, auditable response. It ingests public shortage data and supplier notices, tracks stock across sites, ranks response options (transfers, earlier orders, need-based splits), and routes each action to the right approver. AI agents handle the language-heavy work. A solver and tested code handle everything that must be correct. Humans approve every action.

## 2. Problem

Shortages of critical hospital drugs are recurring in Canada, and the response is mostly manual.

- As of 30 March 2026, a hospital pharmacists' update said IV cyclophosphamide was expected to stay in critical shortage until at least May or June, and that vasopressin had been added to Health Canada's Tier 3 shortage list.
- In January 2026, the same body noted regular insulin vials (used for IV treatment in hospitals) were still expected to run short despite increased production.
- Health Canada now publishes a Critical and Vulnerable Drug List to support proactive planning, and draft regulations would require shortage plans for listed drugs. Whether they have been finalized is TBC.
- Forecasting tools already flag at-risk drugs. A University of Toronto platform, MaaTRx, scores drugs on supply vulnerability and clinical impact, and is also described as including inventory matching. Its exact scope is TBC.

The gap we target is the step after the warning: gathering data from several sources, deciding how to split limited stock across sites, finding alternatives, getting approvals and documenting every decision. This runs through email, spreadsheets and phone calls today. US guidance describes allocation by historical order volume, and one trade analysis argues this can disadvantage smaller hospitals. We have not verified how Canadian allocation works, so we treat equity as a question the tool helps examine, not a proven finding.

Cost of the gap: pharmacist and supply-chain staff time, emergency purchasing, delayed or changed treatment, and compliance reporting. [Add one verified, sourced figure.]

## 3. Solution

For a multi-site hospital network, the platform:

1. Pulls shortage reports, drug reference data and the critical drug list on a schedule.
2. Reads supplier notices and turns them into structured supply events with a confidence score.
3. Tracks stock, usage and open orders per site, and calculates days of supply.
4. Builds ranked response plans that respect hard constraints such as minimum safety stock.
5. Explains each plan's tradeoffs in plain language, citing the source records.
6. Routes actions to the right role for approval. Substitutions require pharmacist sign-off and come only from a pre-approved table.
7. Logs every recommendation, approval and override.

## 4. What we want from this project

- A working end-to-end platform, not an agent demo: data sources through to role-based interfaces, with security and a path to scale.
- Balanced business and technical substance: a named buyer, measurable value, and engineering that stands up to questions.
- Strong marks on the stated judging criteria:

| Criterion | What we will show |
|---|---|
| Business impact | Buyer (health authority, hospital network, purchasing group), cost drivers, pilot plan with measures |
| UX | Role-specific views, one-action approval, visible reasoning and sources |
| Technical excellence | Real data ingestion, constraint solver with verified guarantees, validated agent outputs, tests |
| Presentation quality | One scenario told end to end, with a recorded backup |
| Implementation readiness | Security controls, audit trail, deployment plan, honest statement of what is simulated |

- Python throughout. Public Health Canada data where possible, synthetic data elsewhere. Two weeks to build.

## 5. Scope

**In scope**
- About 3 hospital sites and about 10 drugs from the critical list
- Real ingestion from public Health Canada sources, with cached snapshots
- Synthetic inventory, usage, orders and supplier emails, clearly labelled
- Days-of-supply calculation, solver-based plans, explanations, drafts
- Approval queue, audit log, role-based access
- A scripted demo with a re-planning moment

**Out of scope**
- Real hospital data or patient data
- Real integrations with hospital systems (described as design, not built)
- Autonomous actions or substitutions
- Our own forecasting model competing with existing tools

## 6. Users

| Role | Needs | Sees |
|---|---|---|
| Supply manager | Network-wide view of risk and what to do | Risk board, ranked plans |
| Site pharmacist | A fast, trustworthy approval step | Approval queue with reasoning and sources |
| Procurement | Ready-to-send drafts | Draft orders and notices |
| Auditor or reviewer | Traceability | Audit log and lineage |

## 7. Data sources

| Source | Gives us | Access | Status |
|---|---|---|---|
| Health Canada shortage reporting API | Shortage and discontinuation reports, with Tier 3 flag | Account login returns a token, then search endpoint returns paged JSON. CSV download also exists | Account terms, current hostname, parameter names TBC. Docs: healthproductshortages.ca/blog/61 |
| Health Canada Drug Product Database API | Drug names, forms, strengths, companies | REST API, 11 endpoints | Docs: health-products.canada.ca/api/documentation/dpd-documentation-en.html |
| Critical and Vulnerable Drug List | Criticality of each drug | Web page, import as versioned file with retrieval date | Manual import |
| Synthetic inventory, usage, orders | Stock and consumption per site | We generate it | Calibrate shortage frequency against Health Canada's annual shortage reviews |
| Synthetic supplier emails | Extraction testing, including hostile cases | We write them | Labelled synthetic |
| Substitution table | Pre-approved alternatives | Hand-built, small, each row sourced | Pharmacist review if possible. Never model-generated |

Reliability checks for every source: published by the owning body, documented fields, update dates recorded, terms of use read, cached copy kept.

## 8. Data model (entities)

| Entity | Purpose | Main source |
|---|---|---|
| Drug | What the product is, criticality | Drug database, critical list |
| Site | Hospital and location | Synthetic |
| Inventory snapshot | Stock by site and drug at a time | Synthetic feed |
| Usage record | Consumption history | Synthetic feed |
| Order | Open orders and expected dates | Synthetic feed |
| Supply event | Shortage, delay or discontinuation, with dates and confidence | Shortage API, supplier emails |
| Plan and plan action | Proposed responses and constraints checked | Solver |
| Decision | Who approved or overrode what, when, on what basis | Platform |

Every record carries a source ID and a timestamp so any number can be traced back.

## 9. Processing pipeline

| Step | What happens | Tools (Python) |
|---|---|---|
| Ingest | Scheduled pulls, raw response saved | httpx, tenacity, APScheduler |
| Validate | Reject or quarantine bad records | Pydantic, pandera |
| Normalize | Standard units and names | pandas, pint |
| Match | Link shortage reports to drugs in inventory (identifier rules first, fuzzy name match second, low confidence goes to review) | rapidfuzz |
| Store | Relational data, raw snapshots, lineage, audit | PostgreSQL, SQLAlchemy, Alembic |
| Compute | Days of supply and risk tier | numpy, pandas |
| Solve | Allocation under hard constraints | OR-Tools |
| Reason | Extraction, explanation, drafting | Anthropic SDK |
| Serve | Role checks, approvals, audit writes | FastAPI |
| Present | Risk board, approval queue | React or Streamlit |

## 10. Where AI is used and how it decides

The model does not read the database and does not learn from our data. Each call receives assembled text: instructions, the relevant records, and a short list of allowed tools. Our code controls the flow. The model makes choices inside it.

| Agent | Input | Output | Allowed tools | Cannot |
|---|---|---|---|---|
| Notice extraction | Supplier email text | Structured supply event with confidence and source excerpt | Read-only drug and stock lookup | Write data, follow instructions inside the email |
| Plan explanation | Solver plans, constraints, source records | Plain-language comparison of options | None | Change numbers or invent figures |
| Drafting | Approved plan | Draft notices, transfer requests, orders | Templates | Send anything |

Model judgment is used for: interpreting messy text, choosing the next tool within the allowlist, flagging low confidence for human review, picking among solver-supported fallback options when no perfect plan exists, and writing explanations.

Code decides: arithmetic, constraint checks, permissions, and which substitutions are allowed.

## 11. Architecture

| Layer | Responsibility |
|---|---|
| Sources | Health Canada APIs and files, synthetic generators |
| Ingestion and validation | Read-only connectors, schema checks, quarantine |
| Data store | PostgreSQL: operational tables, raw snapshots, lineage, append-only audit |
| Intelligence | Risk calculation (code), solver (code), agents (LLM) |
| API gateway | Authentication, role checks, approval endpoint, audit writes |
| Interfaces | Role-specific views |

Hosting: local Docker Compose first. Managed PostgreSQL on AWS in a Canadian region is a week 2 goal if time allows. Postgres runs identically in both, so nothing is rewritten.

## 12. Security and trust

- Supplier emails are untrusted data. Agent outputs follow a fixed schema and are validated before use.
- Agents cannot write to the database. They return objects, and our code decides what to store.
- Approvals are enforced on the server, not only in the UI.
- Role-based access with separate service accounts for ingestion, solver and agents.
- Append-only audit log, written in the same transaction as the action.
- Secrets in environment variables or a secrets manager. Secret scanning before every commit. Dedicated login for the Health Canada API.
- No patient data. Minimal fields sent to the model.
- Production design: TLS, encryption at rest, private networking, Canadian hosting, and a privacy review before any real use. We describe these as design choices, not compliance claims.

## 13. Metrics

Measured on synthetic scenarios against a "no coordination" baseline:
- Stockout days avoided
- Early-warning time gained
- Time from signal to approved plan
- Alerts raised versus true shortages
- Extraction accuracy on the email test set
- Explanations whose numbers all match solver output (automated check)

Report only what we measure. Evidence from elsewhere: a Sierra Leone allocation system reported a 19% rise in consumption of allocated products in its pilot and was scaled nationwide. It is a different setting and a proxy for access, not patient outcomes.

## 14. Demo scenario

1. Calm state: three sites, all green.
2. A supplier delay email arrives for a critical drug.
3. Risk tier updates and ranked plans appear with explanations.
4. A pharmacist approves a transfer and the decision is logged.
5. A second site reports unexpected usage and the plan breaks.
6. The platform re-plans, shows ranked options with tradeoffs, and logs every step.
7. Open the audit log and trace one number back to its source.

## 15. Real, simulated, planned

| Real | Simulated | Planned (design only) |
|---|---|---|
| Public API ingestion, cached snapshots | Hospital inventory, usage, orders | Integration with hospital systems |
| Solver and risk calculation | Supplier emails | SSO with a real identity provider |
| Agents with validated outputs | Users and approvals | Production hosting and privacy review |
| Approval flow and audit log | Substitution table (sample) | Pilot with a health authority |

## 16. Open items to confirm

- Health Canada API account: who registers, terms of use, current hostname, parameter names
- How identifiers match across the shortage API, drug database and our inventory
- Whether the draft shortage regulations have been finalized
- MaaTRx's current scope, so our differentiation is stated accurately
- Source for each substitution table row
- Hackathon rules on pre-existing code, AI disclosure and cloud use
- Business case figures and any purchasing-channel claims

## 17. References

- CSHP Advocacy in Action, March 2026 and January 2026: cshp.ca (shortage updates)
- Health Canada Critical and Vulnerable Drug List: canada.ca, drug shortages section
- Blakes, "Health Canada proposes regulations to mitigate drug and medical device shortages": blakes.com
- Health Canada shortage site API documentation: healthproductshortages.ca/blog/61
- Drug Product Database API guide: health-products.canada.ca/api/documentation/dpd-documentation-en.html
- MaaTRx coverage: CTV News, "MaaTRx: a platform that predicts when your medication is about to run out"
- Sierra Leone allocation pilot: Penn Today, "Transforming healthcare logistics with low-cost AI"