import { useEffect, useLayoutEffect, useRef, useState } from "react";

/** One stop on the guided tour: a CSS selector to highlight and a short tip. */
export type TourStep = { sel: string; title: string; body: string };

function find(sel: string): HTMLElement | null {
  const el = document.querySelector<HTMLElement>(sel);
  if (!el) return null;
  const r = el.getBoundingClientRect();
  return r.width > 0 && r.height > 0 ? el : null;
}

/**
 * Guided tour: dims the page, outlines one element at a time and shows a tip
 * beside it. Steps whose element is not on the page right now are skipped.
 */
export function Tour({ steps, onClose }: { steps: TourStep[]; onClose: () => void }) {
  // Look for the targets after the page has rendered, not during this render.
  const [list, setList] = useState<TourStep[] | null>(null);
  const [i, setI] = useState(0);
  const [rect, setRect] = useState<DOMRect | null>(null);
  const [popH, setPopH] = useState(180);
  const popRef = useRef<HTMLDivElement>(null);
  const nextRef = useRef<HTMLButtonElement>(null);
  const step = list?.[i];
  const count = list?.length ?? 0;
  const last = i === count - 1;

  useLayoutEffect(() => {
    setList(steps.filter((s) => find(s.sel)));
  }, [steps]);

  useEffect(() => {
    if (list && list.length === 0) onClose();
  }, [list, onClose]);

  useLayoutEffect(() => {
    if (!step) return;
    const el = find(step.sel);
    if (!el) return;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    const tall = el.getBoundingClientRect().height > window.innerHeight * 0.7;
    el.scrollIntoView({ block: tall ? "start" : "center", behavior: reduce ? "auto" : "smooth" });
    const update = () => setRect(el.getBoundingClientRect());
    update();
    const settle = window.setTimeout(update, 420);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => {
      window.clearTimeout(settle);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
    };
  }, [step]);

  useLayoutEffect(() => {
    if (popRef.current) setPopH(popRef.current.offsetHeight);
    nextRef.current?.focus({ preventScroll: true });
  }, [i, rect === null]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") setI((n) => Math.min(n + 1, count - 1));
      if (e.key === "ArrowLeft") setI((n) => Math.max(n - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, onClose]);

  if (!step || !rect) return null;

  const gap = 12;
  const vw = window.innerWidth;
  const vh = window.innerHeight;
  const popW = Math.min(340, vw - 32);
  const left = Math.min(Math.max(rect.left, 16), vw - popW - 16);
  let top: number;
  if (rect.bottom + gap + popH <= vh - 16) top = rect.bottom + gap;
  else if (rect.top - gap - popH >= 16) top = rect.top - gap - popH;
  else top = vh - popH - 16; // target fills the screen: pin the tip to the bottom

  const pad = 6;
  return (
    <>
      <div
        className="tour-spot"
        aria-hidden
        style={{ top: rect.top - pad, left: rect.left - pad, width: rect.width + pad * 2, height: rect.height + pad * 2 }}
      />
      <div ref={popRef} className="tour-pop" role="dialog" aria-modal="false" aria-labelledby="tour-title" style={{ top, left, width: popW }}>
        <span className="label">
          Tip {i + 1} of {count}
        </span>
        <h4 id="tour-title">{step.title}</h4>
        <p>{step.body}</p>
        <div className="tour-actions">
          <button className="link small" onClick={onClose}>
            {last ? "Close" : "Skip tour"}
          </button>
          <span className="row">
            {i > 0 && (
              <button className="btn" onClick={() => setI(i - 1)}>
                Back
              </button>
            )}
            <button ref={nextRef} className="btn primary" onClick={() => (last ? onClose() : setI(i + 1))}>
              {last ? "Done" : "Next"}
            </button>
          </span>
        </div>
      </div>
    </>
  );
}

/** Tips shown on every page: the demo story and the app's own controls. */
export const SHELL_TIPS: TourStep[] = [
  { sel: ".story", title: "Follow the demo story", body: "This panel tells the demo one event at a time. Read the headline, then look at the page below it." },
  { sel: ".story-actions .btn.primary", title: "Move the story on", body: "Click Next to play the next event, like a late delivery or a busier ICU. Start over resets everything." },
  { sel: ".tabs", title: "Pages", body: "Each page covers one part of the job. A number beside a page means something is waiting there." },
  { sel: ".who", title: "See it as someone else", body: "Switch person to see what each role sees. For example, only the hospital pharmacist can approve a plan." },
];

/** Tips for each page, keyed by view id. */
export const PAGE_TIPS: Record<string, TourStep[]> = {
  board: [
    { sel: ".stats", title: "Today at a glance", body: "How many drugs are critical or getting close, plus open alerts and plans waiting for a decision. Click a card to go there." },
    { sel: ".attention", title: "Needs attention", body: "The drugs most at risk, soonest to run out first. Click one to see why." },
    { sel: ".grid-2 > :first-child", title: "Days of stock", body: "One row per drug, one bar per hospital. Green is fine, amber is under 14 days, red is under 7. Click any bar for details." },
    { sel: ".grid-2 > :last-child", title: "The drug you picked", body: "How much stock is left on each of the next 30 days, and the counts and orders behind it. Hover the chart to read any day." },
    { sel: ".guide", title: "Need a reminder?", body: "Open How to read this chart on any chart for a plain-language key." },
  ],
  events: [
    { sel: ".mail-list", title: "Incoming emails", body: "Supplier emails the AI has read. Click one to see what it understood from it." },
    { sel: ".letter", title: "The original email", body: "The highlighted sentence is the part the AI based its answer on." },
    { sel: ".read-out", title: "What the AI understood", body: "The facts it pulled out: what happened, which drug, which order. It can't change any data itself." },
    { sel: ".meter", title: "How sure the AI is", body: "Past the line, the alert is accepted automatically. Below it, a person checks it first." },
  ],
  plans: [
    { sel: ".table", title: "Compare the options", body: "Every option side by side: what it costs, when stock arrives, and where it is in the approval process." },
    { sel: ".plan", title: "One option in detail", body: "What moves where, the safety checks done in code, and how many days of stock each hospital would have afterwards." },
    { sel: "details.why", title: "Why this plan", body: "The AI's plain-language explanation. Every number in it is checked against the calculation first." },
  ],
  approvals: [
    { sel: ".plan", title: "The plan to decide on", body: "The full plan with its safety checks, so you can decide without leaving this page." },
    { sel: ".page textarea, .page input.input", title: "Leave a note", body: "Add a short reason for your decision. It goes on the permanent record." },
    { sel: ".page .stack .btn.primary", title: "Approve or reject", body: "Only the receiving hospital's pharmacist can decide. Approving writes the messages but sends nothing." },
  ],
  drafts: [
    { sel: ".letter", title: "A ready-to-send message", body: "The AI drafts each message from the approved plan. Copy it and send it the usual way." },
    { sel: ".mail-list", title: "All messages", body: "Every draft from approved plans. Mark one as sent once it has gone out, so it goes on the record." },
  ],
  audit: [
    { sel: ".timeline", title: "Everything, in order", body: "Every action by people, the AI, the planner and the system, newest first. Entries can only be added, never changed." },
    { sel: ".filters", title: "Filter by who did it", body: "Show only what people decided, or only what the AI did." },
    { sel: ".trace-chain", title: "Trace a number", body: "Pick a drug and hospital to follow its days of stock back to the email, report or stock count it came from." },
  ],
  data: [
    { sel: ".grid-3", title: "Where the data comes from", body: "Which sources are real Health Canada data and which are simulated for this demo." },
    { sel: ".grid-even > :first-child", title: "Nightly imports", body: "How many rows each import loaded, and how many failed a check and were set aside for a person." },
    { sel: ".grid-even > :last-child", title: "Rows set aside", body: "The rows that failed, and why. Nothing is dropped silently." },
  ],
};
