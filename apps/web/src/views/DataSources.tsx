import { Card, ChartGuide, PageHead, Status } from "../components/ui";
import { INGESTION_RUNS, QUARANTINE, SOURCE_CATALOG } from "../data/mock";

const SOURCE_NAMES: Record<string, string> = {
  dpd: "Drug database",
  hc_shortage_csv: "Shortage reports",
  critical_drug_list: "Critical drug list",
};

export function DataSources() {
  const real = SOURCE_CATALOG.filter((s) => s.origin === "Real").length;
  const maxRows = Math.max(...INGESTION_RUNS.map((r) => r.rowsIn), 1);

  return (
    <>
      <PageHead
        label="Data"
        title="What's real and what's simulated"
        lead={`${real} of ${SOURCE_CATALOG.length} sources are real Health Canada data. Hospital stock, emails and substitutions are simulated for the demo. Bad rows are set aside for review, never silently dropped.`}
      />

      <div className="grid-3">
        {SOURCE_CATALOG.map((s) => (
          <div key={s.name} className="explain">
            <span className="label">{s.status}</span>
            <h4>{s.name}</h4>
            <p>{s.note}</p>
            <div style={{ marginTop: 4 }}>
              <Status tone={s.origin === "Real" ? "good" : "neutral"}>{s.origin === "Real" ? "Real data" : "Simulated"}</Status>
            </div>
          </div>
        ))}
      </div>

      <div className="grid-even">
        <Card
          label="Nightly imports"
          title="Rows loaded vs set aside"
          footer={
            <div className="legend">
              <span>
                <i style={{ background: "var(--good)" }} />
                Loaded
              </span>
              <span>
                <i style={{ background: "var(--warn)" }} />
                Set aside for review
              </span>
            </div>
          }
        >
          <ChartGuide
            open={false}
            items={[
              { mark: "ok", text: <>Each bar is one nightly import. <b>Longer bar = more rows</b> came in.</> },
              { mark: "bad", text: <>The <b>amber piece</b> at the end is rows that failed a check and were set aside for a person to look at (shown slightly bigger so it's visible).</> },
            ]}
            example={<>The numbers on the right are <b>rows loaded · rows set aside</b>.</>}
          />
          {INGESTION_RUNS.map((r) => (
            <div key={r.id} className="ingest-row" title={`Run ${r.id}: ${r.rowsOk} loaded, ${r.quarantined} set aside`}>
              <div>
                <b>{SOURCE_NAMES[r.source] ?? r.source}</b>
                <div className="meta">{r.started}</div>
              </div>
              {r.state === "failed" ? (
                <Status tone="crit">Failed: {r.error}</Status>
              ) : (
                <div className="stackbar" style={{ width: `${(r.rowsIn / maxRows) * 100}%` }}>
                  <span className="ok" style={{ flex: r.rowsOk }} />
                  {r.quarantined > 0 && <span className="bad" style={{ flex: Math.max(r.quarantined, r.rowsIn * 0.012) }} />}
                </div>
              )}
              <span className="num small ink-2" style={{ textAlign: "right" }}>
                {r.state === "failed" ? "retry next night" : `${r.rowsOk.toLocaleString()} · ${r.quarantined}`}
              </span>
            </div>
          ))}
        </Card>

        <Card label="Set aside" title="Rows that failed checks">
          <div className="stack" style={{ gap: 12 }}>
            {QUARANTINE.map((q) => (
              <div key={q.id}>
                <div className="row">
                  <Status tone="warn">{q.code.replace(/_/g, " ").toLowerCase()}</Status>
                  <span className="meta">snapshot {q.snapshot}</span>
                </div>
                <div className="small ink-2" style={{ marginTop: 2 }}>
                  {q.detail}
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>
    </>
  );
}
