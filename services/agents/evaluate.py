import argparse
import collections
import contextlib
import csv
import io
from pathlib import Path

from services.agents.agent import process_email

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()

    out_dir = ROOT / "evaluation"
    out_dir.mkdir(exist_ok=True)
    rows = []

    for path in sorted((ROOT / "mock" / "emails").glob("*.txt")):
        text = path.read_text()
        for run in range(1, args.runs + 1):
            with contextlib.redirect_stdout(io.StringIO()):
                res = process_email(text)
            ev = res.get("event", {})
            rows.append({
                "email": path.name,
                "run": run,
                "status": res["status"],
                "drug_din": ev.get("drug_din"),
                "sites_at_risk": "|".join(ev.get("sites_at_risk") or []),
                "delay_weeks": ev.get("delay_weeks"),
                "verified_plans": len(res.get("accepted", [])),
                "error": res.get("error", ""),
            })

    with open(out_dir / "results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"{'email':<16}{'status (count)':<40}{'sites (count)':<30}{'plans verified':<16}")
    for name in sorted({r["email"] for r in rows}):
        mine = [r for r in rows if r["email"] == name]
        statuses = collections.Counter(r["status"] for r in mine)
        sites = collections.Counter(r["sites_at_risk"] for r in mine)
        verified = sum(1 for r in mine if r["verified_plans"] > 0)
        print(
            f"{name:<16}"
            f"{', '.join(f'{k} {v}' for k, v in statuses.items()):<40}"
            f"{', '.join(f'{k or chr(45)} {v}' for k, v in sites.items()):<30}"
            f"{verified}/{len(mine):<15}"
        )
    print(f"\nFull results: {out_dir / 'results.csv'}")


if __name__ == "__main__":
    main()