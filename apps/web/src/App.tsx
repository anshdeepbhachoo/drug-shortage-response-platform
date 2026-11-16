import { useCallback, useEffect, useMemo, useState } from "react";
import { PAGE_TIPS, SHELL_TIPS, Tour } from "./components/Tour";
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

export type ViewId = "board" | "events" | "plans" | "approvals" | "drafts" | "audit" | "data";

const NAV: { id: ViewId; label: string; roles: Role[] }[] = [
  { id: "board", label: "Stock", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "events", label: "Alerts", roles: ["supply", "pharmacist", "auditor"] },
  { id: "plans", label: "Plans", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "approvals", label: "Approvals", roles: ["supply", "pharmacist"] },
  { id: "drafts", label: "Messages", roles: ["supply", "procurement"] },
  { id: "audit", label: "History", roles: ["supply", "pharmacist", "procurement", "auditor"] },
  { id: "data", label: "Data", roles: ["supply", "auditor"] },
];

const HOME: Record<Role, ViewId> = { supply: "board", pharmacist: "approvals", procurement: "drafts", auditor: "audit" };
const STEP_VIEW: ViewId[] = ["board", "events", "plans", "approvals", "board", "plans", "audit"];

/** Plain-English narration for each demo step: what happened, why it matters, where to look. */
const STORY: { headline: string; why: string; look: string }[] = [
  {
    headline: "A normal morning. Every drug at every site has enough stock.",
    why: "Three hospitals share ten critical drugs. The platform watches how many days each one will last.",
    look: "the stock chart. Every bar is green and well past the 14-day line.",
  },
  {
    headline: "A supplier says Lakeshore's vasopressin order is three weeks late.",
    why: "The AI read the email and turned it into an alert. Without that order, Lakeshore runs out on Nov 18. A second, fake email tried to trick the AI and was blocked.",
    look: "the two emails, and what the AI understood from each one.",
  },
  {
    headline: "The platform proposes three ways to cover the gap, best first.",
    why: "Each option is checked in code: no donor hospital may drop below 14 days. The top option goes to Lakeshore's pharmacist.",
    look: "how many days each site would have after each plan.",
  },
  {
    headline: "The pharmacist approves moving 72 vials from Riverside.",
    why: "A person makes the decision. The platform records it and writes the transfer request, but sends nothing itself.",
    look: "the decision and the note left for the record.",
  },
  {
    headline: "Riverside's ICU gets busier, so the approved move is no longer safe.",
    why: "Riverside now uses 10 vials a day instead of 6. Sending 72 would leave it under its safety floor, so the transfer was stopped before it shipped.",
    look: "the warning, and Riverside's shorter bar.",
  },
  {
    headline: "No perfect option exists any more, so the platform shows the best compromises.",
    why: "No hospital can spare enough on its own. The top plan combines two transfers with a small rush order, and lists the trade-offs.",
    look: "the cost and the trade-offs of each option.",
  },
  {
    headline: "The new plan is approved. Every step is on the record.",
    why: "Anyone can follow a number back to the email, report or stock count it came from.",
    look: "the history timeline, and the trace panel on the right.",
  },
];

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const set = document.documentElement.dataset.theme;
    if (set === "light" || set === "dark") return set;
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("theme", theme);
    } catch {
      /* storage unavailable: theme still applies for this visit */
    }
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "dark" ? "light" : "dark"))] as const;
}

export function App() {
  const { state, dispatch } = useStore();
  const [theme, toggleTheme] = useTheme();
  const [storyOpen, setStoryOpen] = useState(() => {
    try {
      return localStorage.getItem("story-open") !== "0";
    } catch {
      return true;
    }
  });
  const [touring, setTouring] = useState(() => {
    try {
      return localStorage.getItem("tour-seen") !== "1";
    } catch {
      return false;
    }
  });
  const endTour = useCallback(() => {
    setTouring(false);
    try {
      localStorage.setItem("tour-seen", "1");
    } catch {
      /* storage unavailable: the tour may show again next visit */
    }
  }, []);
  const toggleStory = () =>
    setStoryOpen((o) => {
      try {
        localStorage.setItem("story-open", o ? "0" : "1");
      } catch {
        /* storage unavailable: toggle still works for this visit */
      }
      return !o;
    });
  const [view, setView] = useState<ViewId>(() => {
    const requested = new URLSearchParams(window.location.search).get("view");
    return NAV.some((n) => n.id === requested) ? (requested as ViewId) : HOME[state.role];
  });

  const counts: Partial<Record<ViewId, number>> = {
    events: state.events.filter((e) => e.status === "open").length,
    approvals: state.plans.filter((p) => p.status === "pending-approval").length,
    drafts: state.drafts.filter((d) => d.status === "draft").length,
  };

  const go = (v: ViewId) => {
    setTouring(false);
    setView(v);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const switchRole = (role: Role) => {
    dispatch({ type: "role", role });
    go(HOME[role]);
  };

  const next = () => {
    dispatch({ type: "next" });
    const target = STEP_VIEW[Math.min(state.step + 1, STEPS.length - 1)];
    go(NAV.find((n) => n.id === target)?.roles.includes(state.role) ? target : HOME[state.role]);
  };

  const reset = () => {
    dispatch({ type: "reset" });
    go(HOME[state.role]);
  };

  const atEnd = state.step === STEPS.length - 1;
  const story = STORY[state.step];
  const visibleNav = NAV.filter((n) => n.roles.includes(state.role));
  const activeView = visibleNav.some((n) => n.id === view) ? view : HOME[state.role];
  const tourSteps = useMemo(() => [...SHELL_TIPS, ...(PAGE_TIPS[activeView] ?? [])], [activeView]);

  return (
    <SourceProvider>
      <div className="shell">
      <aside className="sidebar">
          <div className="brand">
            <span className="brand-mark" aria-hidden>
              <i style={{ height: 7 }} />
              <i style={{ height: 12 }} />
              <i style={{ height: 18 }} />
            </span>
            Shortage Response
          </div>
          <nav className="tabs" aria-label="Pages">
            {visibleNav.map((n) => (
              <button key={n.id} className={n.id === activeView ? "active" : ""} onClick={() => go(n.id)} aria-current={n.id === activeView ? "page" : undefined}>
                {n.label}
                {!!counts[n.id] && <span className="count">{counts[n.id]}</span>}
              </button>
            ))}
          </nav>
          <button className="tour-btn" onClick={() => setTouring(true)} aria-label="Show me around" title="Show me around">
            <span className="q" aria-hidden>
              ?
            </span>
            <span className="tour-label">Show me around</span>
          </button>
          <div className="who">
            <span className="label">Viewing as</span>
            <div className="who-row">
            <select value={state.role} onChange={(e) => switchRole(e.target.value as Role)} aria-label="Viewing as">
              {(Object.keys(USERS) as Role[]).map((r) => (
                <option key={r} value={r}>
                  {USERS[r].name} · {USERS[r].title}
                </option>
              ))}
            </select>
            <button className="icon-btn" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Switch theme">
              {theme === "dark" ? "☀" : "☾"}
            </button>
            </div>
          </div>
      </aside>

      <main className="page">
        <section className="story" aria-label="Demo story">
          <div className="story-top">
            <div>
              <div className="story-tag">
                <span className="badge-accent">
                  Step {state.step + 1} of {STEPS.length}
                </span>
                <span className="meta">{timeLabel(state.clockMin)} · simulated clock</span>
              </div>
              <h2>{story.headline}</h2>
              {storyOpen && (
                <>
                  <p>{story.why}</p>
                  <p className="story-look">
                    <b>Look at:</b> {story.look}
                  </p>
                </>
              )}
              <button className="link small story-toggle" onClick={toggleStory} aria-expanded={storyOpen}>
                {storyOpen ? "Hide details" : "Why this matters"}
              </button>
            </div>
            <div className="story-actions">
              <button className="btn" onClick={reset}>
                Start over
              </button>
              <button className="btn primary" onClick={next} disabled={atEnd}>
                {atEnd ? "Story complete" : `Next: ${STEPS[state.step + 1].title} →`}
              </button>
            </div>
          </div>
          <ol className="track">
            {STEPS.map((s, i) => (
              <li key={s.title} className={i < state.step ? "done" : i === state.step ? "current" : ""}>
                <span className="label">{String(i + 1).padStart(2, "0")}</span>
                <span className="t">{s.title}</span>
              </li>
            ))}
          </ol>
        </section>

        {activeView === "board" && <RiskBoard go={(v) => go(v as ViewId)} />}
        {activeView === "events" && <Events />}
        {activeView === "plans" && <Plans />}
        {activeView === "approvals" && <Approvals />}
        {activeView === "drafts" && <Drafts />}
        {activeView === "audit" && <Audit />}
        {activeView === "data" && <DataSources />}

        {touring && <Tour key={activeView} steps={tourSteps} onClose={endTour} />}

        <p className="foot-note">Prototype. Stock, usage, orders, emails and people are simulated.</p>
      </main>
      </div>
    </SourceProvider>
  );
}
