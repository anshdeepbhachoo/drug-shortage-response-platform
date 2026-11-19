
import csv
import re
from pathlib import Path


class CsvData:
    REQUIRED_FILES = (
        "drugs",
        "sites",
        "usage",
        "inventory",
        "open_orders",
        "substitutions",
    )

    def __init__(self, folder):
        self.folder = Path(folder)
        self._cache = {}

    def _rows(self, name):
        if name not in self.REQUIRED_FILES:
            raise ValueError(f"Unknown CSV dataset: {name}")

        if name not in self._cache:
            path = self.folder / f"{name}.csv"

            if not path.exists():
                raise FileNotFoundError(f"Missing data file: {path}")

            with path.open(newline="", encoding="utf-8-sig") as file:
                self._cache[name] = list(csv.DictReader(file))

        return self._cache[name]

    @staticmethod
    def _number(value, field, row):
        try:
            result = float(value)
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid numeric value for {field}: {value!r}; row={row}"
            )

        return result

    def search_drugs(self, query):
        query = query.strip().lower()

        if not query:
            return []

        return [
            drug
            for drug in self._rows("drugs")
            if query in drug["name"].lower()
        ]

    def stock(self, din):
        sites = {
            site["site_id"]: site["name"]
            for site in self._rows("sites")
        }

        usage = {}

        for row in self._rows("usage"):
            if row["din"] == din:
                usage[row["site_id"]] = self._number(
                    row["avg_daily_use"], "avg_daily_use", row
                )

        results = []

        for row in self._rows("inventory"):
            if row["din"] != din:
                continue

            site_id = row["site_id"]

            if site_id not in sites:
                raise ValueError(f"Unknown site in inventory: {site_id}")

            if site_id not in usage:
                raise ValueError(
                    f"Missing daily usage for DIN {din}, site {site_id}"
                )

            on_hand = int(row["on_hand"])
            daily_use = usage[site_id]

            if on_hand < 0 or daily_use < 0:
                raise ValueError(
                    f"Negative inventory or usage for site {site_id}"
                )

            days_supply = (
                on_hand / daily_use if daily_use > 0 else float("inf")
            )

            results.append({
                "site_id": site_id,
                "site": sites[site_id],
                "on_hand": on_hand,
                "daily_use": daily_use,
                "days_of_supply": round(days_supply, 1)
                if daily_use > 0 else None,
            })

        return results

    def orders(self, din):
        return [
            order
            for order in self._rows("open_orders")
            if order["din"] == din
        ]

    def suppliers(self, din):
        """Who has supplied this drug, and to which site: one row per (supplier, site)."""

        seen, result = set(), []
        for order in self._rows("open_orders"):
            name = (order.get("supplier") or "").strip()
            key = (name, order["site_id"])
            if order["din"] != din or not name or key in seen:
                continue
            seen.add(key)
            result.append({"supplier_id": re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
                           "supplier": name, "site_id": order["site_id"]})
        return result

    def substitutions(self, din):
        return [
            substitution
            for substitution in self._rows("substitutions")
            if substitution["din"] == din
        ]

    def drug(self, din):
        return next(
            (drug for drug in self._rows("drugs") if drug["din"] == din),
            None,
        )
