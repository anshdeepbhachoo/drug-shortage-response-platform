import { useState } from "react";
import { SourceProvider } from "./components/ui";
import { USERS, timeLabel } from "./data/mock";
import { STEPS, useStore } from "./lib/store";
import type { Role } from "./lib/types";
import { Approvals } from "./views/Approvals";
import { Audit } from "./views/Audit";
import { DataSources } from "./views/DataSources";
import { Drafts } from "./views/Drafts";
import { Events } from "./views/Events";
import { Plans } from "./views/Plans";
import { RiskBoard } from "./views/RiskBoard";

type ViewId = "board" | "events" | "plans" | "approvals" | "drafts" | "audit" | "data";

const NAV: { id: ViewId; label: string; roles: Role[] }[] = [
  { id: "board", label: "Risk board", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "events", label: "Supply events", roles: ["supply", "pharmacist", "auditor"] },
  { id: "plans", label: "Response plans", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "approvals", label: "Approvals", roles: ["supply", "pharmacist"] },
  { id: "drafts", label: "Drafts", roles: ["supply", "procurement"] },
  { id: "audit", label: "Audit log", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "data", label: "Data sources", roles: ["supply", "auditor"] },
];

const HOME: Record<Role, ViewId> = { supply: "board", pharmacist: "approvals", procurement: "drafts", auditor: "audit" };
const STEP_VIEW: ViewId[] = ["board", "events", "plans", "approvals", "board", "plans", "audit"];

export function App() {
  const { state, dispatch } = useStore();
  const [view, setView] = useState<ViewId>(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return NAV.some((n) => n.id === requested) ? (requested as ViewId) : HOME[state.role];
  });

  const counts: Partial<Record<ViewId, number>> = {
    events: state.events.filter((e) => e.status === "open").length,
    approvals: state.plans.filter((p) => p.status === "pending-approval").length,
    drafts: state.drafts.filter((d) => d.status === "draft").length,
  };

  const switchRole = (role: Role) => {
    dispatch({ type: "role", role });
    setView(HOME[role]);
  };

  const next = () => {
    dispatch({ type: "next" });
    setView(STEP_VIEW[Math.min(state.step + 1, STEPS.length - 1)]);
  };

  const reset = () => {
    dispatch({ type: "reset" });
    setView(HOME[state.role]);
  };

  const title = NAV.find((n) => n.id === view)?.label;
  const atEnd = state.step === STEPS.length - 1;

  return (
    <SourceProvider>
      <div className="app">
        <aside className="sidebar">
          <div className="brand">
            <div className="logo">Rx</div>
            <div>
              <strong>Shortage Response</strong>
              <span>Hospital network</span>
            </div>
          </div>

          <div className="role-switch">
            <label>Viewing as</label>
            {(Object.keys(USERS) as Role[]).map((r) => (
              <button key={r} className={r === state.role ? "active" : ""} onClick={() => switchRole(r)}>
                <span className="avatar">{USERS[r].name.split(" ").map((p) => p[0]).join("")}</span>
                <span>
                  <strong>{USERS[r].name}</strong>
                  <em>{USERS[r].title}</em>
                </span>
              </button>
            ))}
          </div>

          <nav>
            {NAV.filter((n) => n.roles.includes(state.role)).map((n) => (
              <button key={n.id} className={n.id === view ? "active" : ""} onClick={() => setView(n.id)}>
                {n.label}
                {!!counts[n.id] && <span className="count">{counts[n.id]}</span>}
              </button>
            ))}
          </nav>

          <div className="sidebar-foot">Prototype. Inventory, usage, orders, emails and users are synthetic.</div>
        </aside>

        <main>
          <div className="scenario">
            <div className="scenario-head">
              <span className="scenario-label">Demo scenario</span>
              <span className="clock">{timeLabel(state.clockMin)}</span>
            </div>
            <ol className="steps">
              {STEPS.map((s, i) => (
                <li key={s.title} className={i < state.step ? "done" : i === state.step ? "current" : ""}>
                  <span className="dot">{i < state.step ? "✓" : i + 1}</span>
                  {s.title}
                </li>
              ))}
            </ol>
            <div className="scenario-foot">
              <p>{STEPS[state.step].detail}</p>
              <div className="row gap">
                <button className="btn ghost" onClick={reset}>
                  Reset
                </button>
                <button className="btn primary" onClick={next} disabled={atEnd}>
                  {atEnd ? "Scenario complete" : `Next: ${STEPS[state.step + 1].title}`}
                </button>
              </div>
            </div>
          </div>

          <header className="page-head">
            <h1>{title}</h1>
          </header>

          {view === "board" && <RiskBoard go={(v) => setView(v as ViewId)} />}
          {view === "events" && <Events />}
          {view === "plans" && <Plans />}
          {view === "approvals" && <Approvals />}
          {view === "drafts" && <Drafts />}
          {view === "audit" && <Audit />}
          {view === "data" && <DataSources />}
        </main>
      </div>
    </SourceProvider>
  );
}
