import { createContext, useContext, useState, type ReactNode } from "react";
import { SOURCES } from "../data/mock";
import type { Tier } from "../lib/types";

export function TierPill({ tier, children }: { tier: Tier; children?: ReactNode }) {
  return <span className={`pill pill-${tier}`}>{children ?? tier}</span>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "info" | "warn" | "bad" | "good" | "synthetic"; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          <h3>{title}</h3>
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function formatDays(days: number): string {
  return days >= 60 ? "60+" : days.toFixed(1);
}

const SourceContext = createContext<(id: string) => void>(() => {});

export function SourceProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<string | null>(null);
  const src = open ? SOURCES[open] : null;
  return (
    <SourceContext.Provider value={setOpen}>
      {children}
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="card-head">
              <h3>{src?.label ?? open}</h3>
              <button className="btn ghost" onClick={() => setOpen(null)}>
                Close
              </button>
            </header>
            {src ? (
              <dl className="kv">
                <dt>Source ID</dt>
                <dd className="mono">{src.id}</dd>
                <dt>Type</dt>
                <dd>{src.kind}</dd>
                <dt>Origin</dt>
                <dd>
                  <Badge tone={src.origin === "synthetic" ? "synthetic" : src.origin === "platform" ? "info" : "good"}>{src.origin}</Badge>
                </dd>
                {src.table && (
                  <>
                    <dt>Stored in</dt>
                    <dd className="mono">{src.table}</dd>
                  </>
                )}
                {src.sha256 && (
                  <>
                    <dt>Payload SHA-256</dt>
                    <dd className="mono small">{src.sha256}</dd>
                  </>
                )}
                <dt>Detail</dt>
                <dd>{src.detail}</dd>
              </dl>
            ) : (
              <p className="muted">Platform record {open}. Full lineage lookup comes from the API.</p>
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

export function ProjectionChart({
  series,
  floor,
  height = 140,
}: {
  series: { label: string; points: number[]; color: string; dashed?: boolean }[];
  floor?: number;
  height?: number;
}) {
  const width = 460;
  const pad = { l: 36, r: 10, t: 10, b: 22 };
  const days = Math.max(...series.map((s) => s.points.length)) - 1;
  const max = Math.max(1, floor ?? 0, ...series.flatMap((s) => s.points)) * 1.1;
  const x = (d: number) => pad.l + (d / days) * (width - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - v / max) * (height - pad.t - pad.b);
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} className="chart" role="img" aria-label="Projected stock">
        {[0, 0.5, 1].map((f) => (
          <g key={f}>
            <line x1={pad.l} x2={width - pad.r} y1={y(max * f / 1.1)} y2={y(max * f / 1.1)} className="grid" />
            <text x={pad.l - 6} y={y(max * f / 1.1) + 4} textAnchor="end" className="axis">
              {Math.round(max * f / 1.1)}
            </text>
          </g>
        ))}
        {[0, 7, 14, 21, 28].filter((d) => d <= days).map((d) => (
          <text key={d} x={x(d)} y={height - 6} textAnchor="middle" className="axis">
            {d === 0 ? "today" : `+${d}d`}
          </text>
        ))}
        {floor !== undefined && (
          <>
            <line x1={pad.l} x2={width - pad.r} y1={y(floor)} y2={y(floor)} className="floor" />
            <text x={width - pad.r} y={y(floor) - 4} textAnchor="end" className="axis floor-label">
              safety floor
            </text>
          </>
        )}
        {series.map((s) => (
          <polyline
            key={s.label}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeDasharray={s.dashed ? "5 4" : undefined}
            points={s.points.map((v, i) => `${x(i)},${y(v)}`).join(" ")}
          />
        ))}
      </svg>
      <div className="legend">
        {series.map((s) => (
          <span key={s.label}>
            <i style={{ background: s.color }} /> {s.label}
          </span>
        ))}
      </div>
    </div>
  );
}
