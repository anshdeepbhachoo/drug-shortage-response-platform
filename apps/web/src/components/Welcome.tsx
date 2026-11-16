import { useEffect, useRef, useState } from "react";
import { USERS } from "../data/mock";
import type { Role } from "../lib/types";

/** What each person does here, in one plain sentence. */
const ROLE_JOBS: Record<Role, string> = {
  supply: "Watch stock at all three hospitals and line up plans when a drug runs low. Sees every page.",
  pharmacist: "Decide whether to approve plans that move stock into Lakeshore.",
  procurement: "Send the transfer requests and rush orders that approved plans call for.",
  auditor: "Check every decision and trace any number back to where it came from.",
};

const HOW = [
  { n: "1", title: "Spot it early", body: "The platform tracks how many days each drug will last and reads supplier emails for delays." },
  { n: "2", title: "Plan a fix", body: "It proposes ways to cover the gap, like moving stock between hospitals, each one checked for safety." },
  { n: "3", title: "A person decides", body: "A pharmacist approves or rejects. Nothing is sent and no stock moves without a person." },
];

/**
 * First-visit welcome: what the platform is, how it works, and who you want to be.
 * Ends by starting the guided tour or letting the visitor explore on their own.
 */
export function Welcome({ role, onDone }: { role: Role; onDone: (role: Role, tour: boolean) => void }) {
  const [pick, setPick] = useState<Role>(role);
  const startRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    startRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDone(pick, false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pick, onDone]);

  return (
    <div className="modal-backdrop welcome-backdrop">
      <div className="modal welcome" role="dialog" aria-modal="true" aria-labelledby="welcome-title">
        <div>
          <span className="label">Welcome</span>
          <h2 id="welcome-title">Shortage Response helps hospitals avoid running out of critical drugs.</h2>
          <p className="ink-2">
            This is a demo with three hospitals and ten drugs. Everything you see is simulated, so feel free to click around.
          </p>
        </div>

        <ol className="welcome-how">
          {HOW.map((h) => (
            <li key={h.n}>
              <span className="welcome-n" aria-hidden>
                {h.n}
              </span>
              <div>
                <b>{h.title}</b>
                <p className="small ink-2">{h.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <fieldset className="welcome-roles">
          <legend>Who do you want to be?</legend>
          <p className="small muted">You can switch at any time from the menu.</p>
          <div className="welcome-grid">
            {(Object.keys(USERS) as Role[]).map((r) => (
              <label key={r} className={`welcome-role${pick === r ? " on" : ""}`}>
                <input type="radio" name="welcome-role" value={r} checked={pick === r} onChange={() => setPick(r)} />
                <span className="welcome-role-title">{USERS[r].title}</span>
                <span className="small ink-2">{ROLE_JOBS[r]}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <div className="welcome-actions">
          <button className="btn" onClick={() => onDone(pick, false)}>
            Explore on my own
          </button>
          <button ref={startRef} className="btn primary" onClick={() => onDone(pick, true)}>
            Show me around (1 min)
          </button>
        </div>
      </div>
    </div>
  );
}
