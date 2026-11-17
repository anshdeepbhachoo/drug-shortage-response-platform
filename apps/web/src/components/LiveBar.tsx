import { useEffect, useState } from "react";
import { dayLabel } from "../data/mock";
import { api, type DaySummary, type FollowUp, type Planner } from "../lib/api";
import { useStore } from "../lib/store";

const PLANNERS: { id: Planner; label: string }[] = [
  { id: "auto", label: "Auto (AI if available)" },
  { id: "model", label: "AI planning agent" },
  { id: "rules", label: "Rules planning (emails still need AI)" },
];

/** The simulated clock: runs the whole platform one day at a time, like a real daily schedule. */
export function LiveBar() {
  const { state, live } = useStore();
  const asOf = state.live?.asOf ?? "";
  const days = state.live?.days ?? [];
  const daysRun = state.live?.daysRun ?? [];
  const [planner, setPlanner] = useState<Planner>("auto");
  const [auto, setAuto] = useState(false);
  const busy = live.busy !== null;
  const alreadyRun = daysRun.includes(asOf);
  const isLast = days.indexOf(asOf) === days.length - 1;
  const report = latestReport(state.live?.dayReports ?? {}, daysRun, asOf, live.lastDay);

  // Auto-run: keep running the selected day and moving on until every day has run.
  useEffect(() => {
    if (!auto || busy) return;
    if (live.error || alreadyRun) {
      setAuto(false);
      return;
    }
    void live.runDay(asOf, planner).then((ok) => {
      if (!ok) setAuto(false);
    });
  }, [auto, busy, asOf, alreadyRun, live, planner]);

  return (
    <section className="story" aria-label="Simulated clock">
      <div className="story-top">
        <div>
          <div className="story-tag">
            <span className="badge-accent">Live</span>
            <span className="meta">
              Simulated day {days.indexOf(asOf) + 1} of {days.length} · {dayLabel(0)}
            </span>
          </div>
          <h2>The platform runs on its own, one day at a time.</h2>
          <p>
            Each day it loads the hospitals' data, reads the supplier emails that arrived, scans every drug at every site, plans a
            response to each new problem and follows up on approved transfers. People only approve.
          </p>
        </div>
        <div className="story-actions">
          <button className="btn" onClick={() => setAuto((a) => !a)} disabled={(!auto && busy) || (alreadyRun && isLast)}>
            {auto ? "Stop auto-run" : "Auto-run"}
          </button>
          <button className="btn primary" onClick={() => void live.runDay(asOf, planner)} disabled={busy || alreadyRun}>
            {alreadyRun ? `${dayLabel(0)} done` : `Run ${dayLabel(0)} ▶`}
          </button>
        </div>
      </div>

      <ol className="track" aria-label="Days">
        {days.map((day, i) => (
          <li key={day} className={daysRun.includes(day) ? "done" : day === asOf ? "current" : ""}>
            <button className="link" onClick={() => live.setDay(day)} disabled={busy} style={{ textAlign: "left" }}>
              <span className="label">{String(i + 1).padStart(2, "0")}</span>
              <span className="t">
                {day.slice(5)} {daysRun.includes(day) ? "✓" : ""}
              </span>
            </button>
          </li>
        ))}
      </ol>

      <div className="live-controls">
        <label className="live-field">
          <span className="label">Planner</span>
          <select className="input" value={planner} onChange={(e) => setPlanner(e.target.value as Planner)} disabled={busy}>
            {PLANNERS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <div className="live-field">
          <span className="label">Hospital data for {dayLabel(0)}</span>
          <span className="small ink-2">
            {state.live?.loadedDays.includes(asOf)
              ? "Arrived"
              : state.live?.loadedDays.length
                ? `Not arrived yet: showing the latest earlier day (${state.live.loadedDays.filter((d) => d < asOf).at(-1) ?? "none"})`
                : "Nothing has arrived yet. Running the day loads it first."}
          </span>
        </div>
        <div className="live-field">
          <span className="label">Arriving in the inbox on {dayLabel(0)}</span>
          <span className="small ink-2">{state.live?.inbox.length ? state.live.inbox.join(", ") : "No supplier emails scheduled"}</span>
        </div>
        <Advanced />
      </div>

      {(live.busy || live.notice || live.error || report) && (
        <div className="live-status" role="status" aria-live="polite">
          {live.busy && <span className="meta">{live.busy}… with the AI this takes a minute or two per day.</span>}
          {report && !live.busy && <DayReport summary={report} />}
          {live.notice && (
            <div className="callout good">
              <span className="ico" aria-hidden>
                ✓
              </span>
              <div style={{ flex: 1 }}>{live.notice}</div>
              <button className="link small" onClick={live.clear}>
                Dismiss
              </button>
            </div>
          )}
          {live.error && (
            <div className="callout crit">
              <span className="ico" aria-hidden>
                ✕
              </span>
              <div style={{ flex: 1 }}>
                <strong>Not done.</strong> <span className="ink-2">{live.error}</span>
              </div>
              <button className="link small" onClick={live.clear}>
                Dismiss
              </button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

/** The report of the latest day run up to the one shown (the run just made wins). */
export function latestReport(reports: Record<string, DaySummary>, daysRun: string[], asOf: string, justRun: DaySummary | null) {
  if (justRun) return justRun;
  const day = daysRun.filter((d) => d <= asOf).at(-1);
  return day ? reports[day] ?? null : null;
}

const DECISION_WORDS: Record<string, string> = {
  wait: "on track, not due yet",
  nudge: "nudged, no confirmation yet",
  escalate: "escalated to a person",
  replan: "not happening, replanning",
  close: "done",
};

/** "Epinephrine: Northside → Central General, 132 units: escalated to a person". decision: that day's, else the latest. */
export function followupLine(f: FollowUp, decision?: string) {
  const word = decision ? DECISION_WORDS[decision] ?? decision : f.status.toLowerCase();
  return `${f.drug.split(" ")[0]}: ${f.from} → ${f.to}, ${f.units} units: ${word}`;
}

/** Compact clock for every page but Home: the day, and running it. */
export function DayControl({ openHome }: { openHome: () => void }) {
  const { state, live } = useStore();
  const asOf = state.live?.asOf ?? "";
  const alreadyRun = (state.live?.daysRun ?? []).includes(asOf);
  return (
    <div className="day-control" role="region" aria-label="Simulated clock">
      <span className="badge-accent">Live</span>
      <span className="small">
        Simulated day <b>{dayLabel(0)}</b> · {alreadyRun ? "already run" : "not run yet"}
      </span>
      {live.busy && <span className="meta">{live.busy}…</span>}
      {live.error && <span className="small" style={{ color: "var(--crit-text)" }}>Not done: {live.error}</span>}
      <span style={{ flex: 1 }} />
      <button className="link small" onClick={openHome}>
        Day report on Home
      </button>
      <button className="btn primary" onClick={() => void live.runDay(asOf, "auto")} disabled={live.busy !== null || alreadyRun}>
        {alreadyRun ? `${dayLabel(0)} done` : `Run ${dayLabel(0)} ▶`}
      </button>
    </div>
  );
}

/** What the last simulated day did, in plain words. */
function DayReport({ summary }: { summary: DaySummary }) {
  const { state } = useStore();
  const byId = new Map((state.live?.followups ?? []).map((f) => [f.id, f]));
  const watch = summary.stock_watch;
  return (
    <div className="callout info">
      <span className="ico" aria-hidden>
        i
      </span>
      <div style={{ flex: 1 }}>
        <strong>
          {summary.day}: done ({summary.planner}). {summary.waiting_for_approval} plan{summary.waiting_for_approval === 1 ? "" : "s"} waiting for
          approval.
        </strong>
        <ul>
          <li>
            Inbox:{" "}
            {summary.emails.length
              ? summary.emails.map((e) => `${e.email} → ${e.status.replace(/_/g, " ")}`).join("; ")
              : "nothing arrived"}
          </li>
          <li>
            Stock watch:{" "}
            {watch.alerts.length
              ? watch.alerts.map((a) => `${a.drug} at ${a.sites.join(", ")} (${a.kind.replace("_", " ")}) → ${a.status.replace(/_/g, " ")}`).join("; ")
              : "no new problems"}
            {watch.waiting > 0 && `; ${watch.waiting} more waiting for tomorrow`}
          </li>
          <li>Health Canada reports: {summary.shortage_reports.reports ? `${summary.shortage_reports.reports} planned` : "nothing new for our drugs"}</li>
          <li>
            Follow-up:{" "}
            {summary.followup.length
              ? summary.followup.map((f) => (byId.has(f.transfer) ? followupLine(byId.get(f.transfer)!, f.decision) : `${f.transfer}: ${f.decision}`)).join("; ")
              : "no approved transfers to check"}
          </li>
        </ul>
      </div>
    </div>
  );
}

/** Manual controls kept for testing one piece at a time. */
function Advanced() {
  const { state, live } = useStore();
  const [emails, setEmails] = useState<string[]>([]);
  const [email, setEmail] = useState("clear.txt");
  const asOf = state.live?.asOf ?? "";
  const [followupAt, setFollowupAt] = useState(`${asOf}T18:00`);
  const disabled = live.busy !== null;

  useEffect(() => {
    api.emails().then(setEmails).catch(() => setEmails([]));
  }, []);
  useEffect(() => setFollowupAt(`${asOf}T18:00`), [asOf]);

  return (
    <details className="live-field">
      <summary className="label" style={{ cursor: "pointer" }}>
        Advanced: run one piece by hand
      </summary>
      <div className="row" style={{ marginTop: 8 }}>
        <select className="input" value={email} onChange={(e) => setEmail(e.target.value)} disabled={disabled} aria-label="Supplier email">
          {emails.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <button className="btn" onClick={() => void live.runEmail(email)} disabled={disabled || !emails.length}>
          Process email
        </button>
      </div>
      <div className="row" style={{ marginTop: 8 }}>
        <input
          className="input"
          type="datetime-local"
          value={followupAt}
          onChange={(e) => setFollowupAt(e.target.value)}
          disabled={disabled}
          aria-label="Follow-up check time"
        />
        <button className="btn" onClick={() => void live.runFollowup(followupAt)} disabled={disabled || !followupAt}>
          Run follow-up
        </button>
      </div>
    </details>
  );
}
