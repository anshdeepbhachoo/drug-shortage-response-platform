import csv
from pathlib import Path


class CsvData:
    def __init__(self, folder):
        self.folder = Path(folder)
        self._cache = {}

    def _rows(self, name):
        if name not in self._cache:
            with open(self.folder / f"{name}.csv", newline="") as f:
                self._cache[name] = list(csv.DictReader(f))
        return self._cache[name]

    def search_drugs(self, query):
        q = query.lower()
        return [d for d in self._rows("drugs") if q in d["name"].lower()]

    def stock(self, din):
        sites = {s["site_id"]: s["name"] for s in self._rows("sites")}
        usage = {u["site_id"]: float(u["avg_daily_use"]) for u in self._rows("usage") if u["din"] == din}
        out = []
        for r in self._rows("inventory"):
            if r["din"] == din:
                on_hand = int(r["on_hand"])
                daily = usage[r["site_id"]]
                out.append({
                    "site_id": r["site_id"],
                    "site": sites[r["site_id"]],
                    "on_hand": on_hand,
                    "daily_use": daily,
                    "days_of_supply": round(on_hand / daily, 1),
                })
        return out

    def orders(self, din):
        return [o for o in self._rows("open_orders") if o["din"] == din]

    def substitutions(self, din):
        return [s for s in self._rows("substitutions") if s["din"] == din]