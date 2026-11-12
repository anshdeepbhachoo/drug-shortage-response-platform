import { Badge, Card } from "../components/ui";
import { INGESTION_RUNS, QUARANTINE, SOURCE_CATALOG } from "../data/mock";

export function DataSources() {
  return (
    <div className="view">
      <Card title="Sources" actions={<span className="muted small">What is real, what is simulated</span>}>
        <table className="table">
          <thead>
            <tr>
              <th>Source</th>
              <th>Origin</th>
              <th>Status</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody>
            {SOURCE_CATALOG.map((s) => (
              <tr key={s.name}>
                <td>
                  <strong>{s.name}</strong>
                </td>
                <td>
                  <Badge tone={s.origin === "Real" ? "good" : "synthetic"}>{s.origin}</Badge>
                </td>
                <td>{s.status}</td>
                <td className="muted">{s.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>

      <div className="split even">
        <Card title="Ingestion runs" actions={<span className="mono small muted">ingestion_run</span>}>
          <table className="table">
            <thead>
              <tr>
                <th>Run</th>
                <th>Source</th>
                <th>Started</th>
                <th>State</th>
                <th className="num">In</th>
                <th className="num">OK</th>
                <th className="num">Quarantined</th>
              </tr>
            </thead>
            <tbody>
              {INGESTION_RUNS.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.id}</td>
                  <td className="mono">{r.source}</td>
                  <td className="nowrap">{r.started}</td>
                  <td>
                    <Badge tone={r.state === "succeeded" ? "good" : "bad"}>{r.state}</Badge>
                    {r.error && <div className="muted small">{r.error}</div>}
                  </td>
                  <td className="num">{r.rowsIn.toLocaleString()}</td>
                  <td className="num">{r.rowsOk.toLocaleString()}</td>
                  <td className="num">{r.quarantined}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
        <Card title="Quarantined records" actions={<span className="mono small muted">quarantine</span>}>
          <table className="table">
            <thead>
              <tr>
                <th>ID</th>
                <th>Snapshot</th>
                <th>Error</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {QUARANTINE.map((q) => (
                <tr key={q.id}>
                  <td className="mono">{q.id}</td>
                  <td className="mono">{q.snapshot}</td>
                  <td>
                    <Badge tone="warn">{q.code}</Badge>
                  </td>
                  <td>{q.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Records that fail schema checks are kept with their snapshot ID instead of being dropped, so every rejection can be reviewed.</p>
        </Card>
      </div>
    </div>
  );
}
