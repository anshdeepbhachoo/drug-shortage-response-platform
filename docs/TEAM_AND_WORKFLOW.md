# Team and workflow

Who owns what, how we work, and the two-week plan. Project description is in `PROJECT_OVERVIEW.md`. Items in [brackets] are placeholders.

## 1. Roles and responsibilities (team of four)

| Role | Owner | Owns | First deliverable | Depends on |
|---|---|---|---|---|
| Agentic AI lead | [Name] | Extraction, explanation and drafting agents, tool definitions, prompt-injection defenses, LLM module (caching and replay), agent evaluation set, synthetic supplier emails | One agent running end to end against stubbed data | Schemas, solver output format |
| Backend and data lead | [Name] | Database schema and migrations, ingestion connectors, validation and quarantine, FastAPI, auth and roles, audit log, Docker Compose, CI, secret scanning | Docker Compose stack, schema, first real ingestion | Contract agreed in week 1 |
| Planning and risk lead | [Name] | Days-of-supply calculation, solver (hard constraints), plan ranking, drug matching rules, synthetic inventory generator and scripted scenario | Generator plus first feasible plan | Schema |
| Frontend and demo lead | [Name] | Risk board, approval queue, plan comparison view, audit view, demo script, pitch deck, rehearsals | Screens running against a mocked API | OpenAPI spec |

**Tech lead for tiebreaks:** [Name]. Decisions that stall for more than a day go to the tech lead.

**Backups (pairs):** agentic lead and planning lead cover each other (they share the solver-to-explanation interface). Backend lead and frontend lead cover each other (they share the API contract).

**Cross-cutting duties with four people**

| Duty | Owner |
|---|---|
| Tests | Each person tests their own area. Pull requests without tests for new logic are not merged |
| CI and Docker | Backend lead |
| Security checks (secrets, dependencies) | Backend lead, with the agentic lead owning the agent-layer threat tests |
| Test fixtures (saved API responses) | Backend lead creates them, everyone uses them |
| Demo script and rehearsals | Frontend and demo lead, whole team attends |
| Pitch and Q&A preparation | Shared. Each person prepares answers for their own area |

**Load warning:** the backend role is the heaviest. While the agents wait on the contract in week 1, consider moving the Health Canada shortage API connector to the agentic lead. Decide this at kickoff.

## 2. Interfaces between roles (the contract)

Agree these in week 1 and change them only through a pull request that tags every affected owner.

- **Data schema:** entities for drug, site, inventory snapshot, usage, order, supply event, plan and decision. Every record carries a source and a timestamp.
- **API spec:** OpenAPI generated from the FastAPI models, used by the frontend from day one.
- **Agent input and output formats:** each agent returns a validated object, never free text, and never writes to the database directly.
- **Solver interface:** input is current state plus constraints, output is a ranked list of plans with the constraints that were checked.

## 3. Repository layout

```
/apps/web          Frontend
/apps/api          FastAPI backend
/services/agents   Agents and tool definitions
/services/planner  Risk calculation and solver
/packages/schemas  Shared models and OpenAPI spec
/data              Synthetic data generator and fixtures
/infra             Docker Compose, infrastructure as code
/docs              Overview, architecture, ADRs, demo script
```

## 4. Working agreements

- Trunk-based: short-lived branches (`feat/`, `fix/`), merged within a day or two.
- `main` is protected: pull request required, one approval, CI must pass.
- Small pull requests (about 300 lines or fewer) using the PR template.
- Conventional commits (`feat:`, `fix:`, `docs:`).
- CODEOWNERS per folder.
- CI: lint, tests and build in under 5 minutes.
- Secrets: pre-commit secret scanning, `.env` ignored, rotate anything that leaks. The Health Canada account uses a dedicated login, never a personal one.
- Task board in Notion: Backlog, Ready, In Progress, In Review, Done. Daily 10-minute standup.

## 5. Two-week plan

**Week 1: foundation**
- Contract agreed (schema, API spec, agent formats)
- Docker Compose stack, database and migrations
- First real ingestion from public APIs, with cached snapshots
- Synthetic data generator and scripted scenario
- Walking skeleton: a dummy plan flows from backend to UI and an approval is logged

**Week 2: depth and hardening**
- Real solver, extraction agent, explanation agent
- The hard case: re-plan when no perfect solution exists
- Audit log view, quality metrics, security checks
- Optional: deploy to cloud (Canadian region), backup and restore test
- Feature freeze about two days before the deadline, then bug fixing, a recorded backup demo and pitch rehearsals

**Team of four:** QA, CI and the demo are folded into the roles above (see section 1). Re-check the load at the end of week 1 and move tasks if one role is behind.

## 6. Risks

| Risk | Mitigation |
|---|---|
| Health Canada API account unavailable or terms unclear | Use CSV download or cached fixtures, confirm early |
| Drug identifiers do not match across sources | Test matching rules on real samples in week 1 |
| LLM or network fails during demo | Cached and recorded runs, local fallback |
| Scope creep | Cut features, not quality. Core demo first |
| Security mistake in a public repo | Secret scanning, no credentials in code or issues |
| Overclaiming | State what is built, simulated and planned |