import { createContext, useContext, useId, useState, type ReactNode } from "react";
import { SOURCES, dayLabel } from "../data/mock";
import type { Tier } from "../lib/types";

/* ---------------------------------------------------------------
   Status: colour is never used alone - always an icon and a label.
---------------------------------------------------------------- */
export type Tone = "good" | "warn" | "crit" | "neutral";

const ICON: Record<Tone, string> = { good: "✓", warn: "!", crit: "✕", neutral: "•" };

export function Status({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={`status ${tone}`}>
      <span className="ico" aria-hidden>
        {ICON[tone]}
      </span>
      {children}
    </span>
  );
}

export const TIER_TONE: Record<Tier, Tone> = { red: "crit", amber: "warn", green: "good" };
export const TIER_WORDS: Record<Tier, string> = { red: "Critical", amber: "Watch", green: "OK" };

export function TierStatus({ tier, children }: { tier: Tier; children?: ReactNode }) {
  return <Status tone={TIER_TONE[tier]}>{children ?? TIER_WORDS[tier]}</Status>;
}

/** Kept for any older imports. */
export function TierPill({ tier, children }: { tier: Tier; children?: ReactNode }) {
  return <TierStatus tier={tier}>{children}</TierStatus>;
}

export function Tag({ children, solid }: { children: ReactNode; solid?: boolean }) {
  return <span className={`tag ${solid ? "solid" : ""}`}>{children}</span>;
}

/* ---------------------------------------------------------------
   Layout pieces
---------------------------------------------------------------- */
export function PageHead({ title, lead, label }: { title: string; lead: ReactNode; label?: string }) {
  return (
    <header className="page-head">
      {label && <span className="label">{label}</span>}
      <h1>{title}</h1>
      <p className="lead">{lead}</p>
    </header>
  );
}

export function Explainers({ items }: { items: { title: string; text: ReactNode }[] }) {
  return (
    <div className="grid-3">
      {items.map((it, i) => (
        <div key={it.title} className="explain">
          <span className="label">{String(i + 1).padStart(2, "0")}</span>
          <h4>{it.title}</h4>
          <p>{it.text}</p>
        </div>
      ))}
    </div>
  );
}

export function Card({
  title,
  label,
  actions,
  children,
  footer,
  className = "",
}: {
  title?: ReactNode;
  label?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || label || actions) && (
        <header className="card-head">
          <div>
            {label && <div className="label" style={{ marginBottom: 4 }}>{label}</div>}
            {title && <h3>{title}</h3>}
          </div>
          {actions}
        </header>
      )}
      <div className="card-body">{children}</div>
      {footer && <footer className="card-foot">{footer}</footer>}
    </section>
  );
}

export function Empty({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <div className="empty">
      {title && <strong>{title}</strong>}
      <span>{children}</span>
    </div>
  );
}

export function Change({ was, now }: { was: ReactNode; now: ReactNode }) {
  return (
    <span className="change">
      <span className="was">{was}</span>
      <span className="arrow">→</span>
      <span className="now">{now}</span>
    </span>
  );
}

export function formatDays(days: number): string {
  return days >= 60 ? "60+" : days.toFixed(1);
}

/** A bar with a threshold mark, e.g. AI confidence vs the auto-accept line. */
export function Meter({ value, threshold, thresholdLabel }: { value: number; threshold: number; thresholdLabel: string }) {
  const ok = value >= threshold;
  return (
    <div className="meter" role="img" aria-label={`${Math.round(value * 100)}%, threshold ${Math.round(threshold * 100)}%`}>
      <div className={`meter-fill ${ok ? "good" : "crit"}`} style={{ width: `${value * 100}%` }} />
      <div className="meter-mark" style={{ left: `${threshold * 100}%` }}>
        <span>{thresholdLabel}</span>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------
   Source records: every number can be traced
---------------------------------------------------------------- */
const SourceContext = createContext<(id: string) => void>(() => {});

export function SourceProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const src = open ? SOURCES[open] : null;
  return (
    <SourceContext.Provider value={setOpen}>
      {children}
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(null)}>
          <div className="modal" role="dialog" aria-label="Source record" onClick={(e) => e.stopPropagation()}>
            <div className="row-between">
              <div>
                <div className="label">Source record</div>
                <h3 style={{ fontSize: 18, fontWeight: 600 }}>{src?.label ?? open}</h3>
              </div>
              <button className="btn" onClick={() => setOpen(null)}>
                Close
              </button>
            </div>
            {src ? (
              <>
                <p className="ink-2">{src.detail}</p>
                <dl className="facts">
                  <dt>Record ID</dt>
                  <dd className="mono">{src.id}</dd>
                  <dt>Type</dt>
                  <dd>{src.kind}</dd>
                  <dt>Real or simulated</dt>
                  <dd>
                    <Status tone={src.origin === "synthetic" ? "neutral" : "good"}>{src.origin === "synthetic" ? "Simulated (demo data)" : src.origin === "platform" ? "Created by the platform" : "Real (cached copy)"}</Status>
                  </dd>
                  {src.table && (
                    <>
                      <dt>Stored in</dt>
                      <dd className="mono">{src.table}</dd>
                    </>
                  )}
                  {src.sha256 && (
                    <>
                      <dt>Fingerprint</dt>
                      <dd className="mono small">{src.sha256}</dd>
                    </>
                  )}
                </dl>
              </>
            ) : (
              <p className="ink-2">Platform record {open}. In the full system this opens the stored record.</p>
            )}
          </div>
        </div>
      )}
    </SourceContext.Provider>
  );
}

export function SourceChip({ id }: { id: string }) {
  const open = useContext(SourceContext);
  return (
    <button className="chip" onClick={() => open(id)} title="Show source record">
      {id}
    </button>
  );
}

/** Collapsed list of source records, so IDs don't crowd the main text. */
export function Sources({ ids, label = "Where these numbers come from" }: { ids: string[]; label?: string }) {
  if (ids.length === 0) return null;
  return (
    <details className="sources">
      <summary>
        {label} ({ids.length})
      </summary>
      <div className="chips">
        {ids.map((id) => (
          <SourceChip key={id} id={id} />
        ))}
      </div>
    </details>
  );
}

/* ---------------------------------------------------------------
   Stock projection chart: line per scenario, safety floor, run-out
   marker, and a hover crosshair with the exact day and stock.
---------------------------------------------------------------- */
export interface Series {
  label: string;
  points: number[];
  color: string;
  dashed?: boolean;
}

export function ProjectionChart({
  series,
  floor,
  unit,
  marks = [],
  height = 210,
}: {
  series: Series[];
  floor?: number;
  unit: string;
  marks?: { day: number; label: string }[];
  height?: number;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const gradId = useId();
  const width = 420;
  const pad = { l: 40, r: 14, t: 18, b: 26 };
  const days = Math.max(...series.map((s) => s.points.length)) - 1;
  const rawMax = Math.max(1, floor ?? 0, ...series.flatMap((s) => s.points));
  const step = rawMax > 200 ? 100 : rawMax > 80 ? 50 : 20;
  const max = Math.ceil((rawMax * 1.05) / step) * step;
  const x = (d: number) => pad.l + (d / days) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  const ticks = Array.from({ length: max / step + 1 }, (_, i) => i * step);

  const runOut = series.map((s) => s.points.findIndex((v, i) => i > 0 && v <= 0));
  // Days with no stock at all, from the run-out day until the next delivery (or the chart's end).
  const gaps = series.map((s, i) => {
    if (runOut[i] <= 0) return null;
    const back = s.points.findIndex((v, d) => d > runOut[i] && v > 0);
    return { from: runOut[i], to: back === -1 ? days : back, open: back === -1 };
  });
  const clampX = (v: number, half = 70) => Math.min(Math.max(v, pad.l + half), width - pad.r - half);

  const dayAt = (clientX: number, el: SVGSVGElement) => {
    const rect = el.getBoundingClientRect();
    const px = ((clientX - rect.left) / rect.width) * width;
    const d = Math.round(((px - pad.l) / (width - pad.l - pad.r)) * days);
    return d >= 0 && d <= days ? d : null;
  };
  const onPointer = (e: React.PointerEvent<SVGSVGElement>) => setHover(dayAt(e.clientX, e.currentTarget));
  const onKey = (e: React.KeyboardEvent<SVGSVGElement>) => {
    const moves: Record<string, (d: number) => number> = {
      ArrowRight: (d) => Math.min(d + 1, days),
      ArrowLeft: (d) => Math.max(d - 1, 0),
      Home: () => 0,
      End: () => days,
    };
    if (!moves[e.key]) return;
    e.preventDefault();
    setHover((h) => moves[e.key](h ?? 0));
  };

  const first = series[0];
  const summary =
    runOut[0] > 0
      ? `Stock of ${first.points[0]} ${unit} runs out ${dayLabel(runOut[0])}${gaps[0] && !gaps[0].open ? ` and is empty for ${gaps[0].to - gaps[0].from} days until the next delivery` : ""}.`
      : `Stock of ${first.points[0]} ${unit} lasts the full ${days} days.`;
  const tipEdge = hover === null ? 0 : hover / days;
  const tipShift = tipEdge < 0.15 ? "-12%" : tipEdge > 0.85 ? "-88%" : "-50%";

  return (
    <div className="chart-wrap">
      {series.length > 1 && (
        <div className="legend" style={{ marginBottom: 8 }}>
          {series.map((s) => (
            <span key={s.label}>
              <i className="line" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
          {floor !== undefined && (
            <span>
              <i className="line" style={{ background: "var(--crit)" }} />
              Safety floor
            </span>
          )}
        </div>
      )}
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="chart"
        role="img"
        tabIndex={0}
        aria-label={`Projected stock over the next ${days} days. ${summary} Use the arrow keys to read each day.`}
        onPointerMove={onPointer}
        onPointerDown={onPointer}
        onPointerLeave={(e) => e.pointerType === "mouse" && setHover(null)}
        onKeyDown={onKey}
        onFocus={() => setHover((h) => h ?? 0)}
        onBlur={() => setHover(null)}
      >
        <defs>
          <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor={first.color} stopOpacity={0.22} />
            <stop offset="100%" stopColor={first.color} stopOpacity={0} />
          </linearGradient>
        </defs>
        {floor !== undefined && floor > 0 && (
          <rect x={pad.l} y={y(floor)} width={width - pad.l - pad.r} height={y(0) - y(floor)} className="danger-zone" />
        )}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} className={t === 0 ? "baseline" : "grid"} />
            <text x={pad.l - 8} y={y(t) + 3.5} textAnchor="end" className="axis">
              {t}
            </text>
          </g>
        ))}
        {[0, 7, 14, 21, 28].filter((d) => d <= days).map((d) => (
          <text key={d} x={x(d)} y={height - 8} textAnchor="middle" className="axis">
            {d === 0 ? "Today" : dayLabel(d)}
          </text>
        ))}
        {floor !== undefined && (
          <>
            <line x1={pad.l} x2={width - pad.r} y1={y(floor)} y2={y(floor)} className="floor" />
            {series.length === 1 && (
              <text x={width - pad.r} y={y(floor) - 6} textAnchor="end" className="floor-label">
                Safety floor ({floor} {unit})
              </text>
            )}
          </>
        )}
        {marks.map((m) => (
          <g key={m.label}>
            <line x1={x(m.day)} x2={x(m.day)} y1={pad.t} y2={height - pad.b} className="grid" strokeDasharray="2 3" />
            <text x={x(m.day) + 4} y={pad.t + 2} className="annot">
              {m.label}
            </text>
          </g>
        ))}
        {series.length === 1 && (
          <polygon fill={`url(#${gradId})`} points={`${x(0)},${y(0)} ${first.points.map((v, i) => `${x(i)},${y(v)}`).join(" ")} ${x(days)},${y(0)}`} />
        )}
        {gaps.map((g, i) =>
          g ? <line key={`gap-${series[i].label}`} x1={x(g.from)} x2={x(g.to)} y1={y(0)} y2={y(0)} className="gap" /> : null,
        )}
        {series.map((s) => (
          <polyline
            key={s.label}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            strokeDasharray={s.dashed ? "6 4" : undefined}
            points={s.points.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
          />
        ))}
        {series.map((s, i) =>
          runOut[i] > 0 ? (
            <g key={`ro-${s.label}`}>
              <circle cx={x(runOut[i])} cy={y(0)} r={4.5} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              <text x={clampX(x(runOut[i]))} y={y(0) - 10 - i * 15} textAnchor="middle" className="annot-strong">
                Runs out {dayLabel(runOut[i])}
                {gaps[i] && !gaps[i]!.open && <tspan className="annot-crit"> · {gaps[i]!.to - gaps[i]!.from} days empty</tspan>}
              </text>
            </g>
          ) : null,
        )}
        {hover !== null && (
          <g>
            <line x1={x(hover)} x2={x(hover)} y1={pad.t} y2={height - pad.b} className="cross" />
            {series.map((s) => (
              <circle key={s.label} cx={x(hover)} cy={y(s.points[hover] ?? 0)} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
            ))}
          </g>
        )}
        <rect x={pad.l} y={pad.t} width={width - pad.l - pad.r} height={height - pad.t - pad.b} fill="transparent" />
      </svg>
      {hover !== null && (
        <div
          className="tooltip"
          style={{
            left: `${(x(hover) / width) * 100}%`,
            top: `${(y(Math.max(...series.map((s) => s.points[hover] ?? 0))) / height) * 100}%`,
            transform: `translate(${tipShift}, -110%)`,
          }}
        >
          <b>{hover === 0 ? "Today" : dayLabel(hover)}</b>
          {series.map((s) => {
            const v = Math.round(s.points[hover] ?? 0);
            const note = v <= 0 ? "out of stock" : floor !== undefined && v < floor ? "below floor" : null;
            return (
              <div key={s.label} className="tip-row">
                {series.length > 1 && <i style={{ background: s.color }} aria-hidden />}
                {series.length > 1 ? `${s.label}: ` : ""}
                {v} {unit}
                {note && <span className="tip-flag">{note}</span>}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* ---------------------------------------------------------------
   ChartGuide: a plain-language "how to read this" key for a chart.
   Each row pairs a small picture of the mark with what it means,
   and an optional example reads the chart out loud using live data.
---------------------------------------------------------------- */
export type KeyMark = "bar-good" | "bar-warn" | "bar-crit" | "zone" | "floor" | "after" | "gain" | "line-1" | "line-2" | "dot" | "drop" | "gap" | "danger" | "tick" | "cost" | "ok" | "bad";

export function ChartGuide({ items, example, open = false }: { items: { mark: KeyMark; text: ReactNode }[]; example?: ReactNode; open?: boolean }) {
  return (
    <details className="guide" open={open}>
      <summary>How to read this chart</summary>
      <ul>
        {items.map((it, i) => (
          <li key={i}>
            <span className={`gk gk-${it.mark}`} aria-hidden />
            <span>{it.text}</span>
          </li>
        ))}
      </ul>
      {example && (
        <p className="guide-example">
          <span className="label">Example</span> {example}
        </p>
      )}
    </details>
  );
}
