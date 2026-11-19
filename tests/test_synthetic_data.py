"""Integrity and agent-compatibility checks for the expanded synthetic dataset."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from services.agents.rules.checks import check_transfer
from services.agents.data.data import CsvData
from services.agents.rules.risk import calculate_site_risk, cover_days_by_site
from services.agents.agents.schemas import Move, PlanOption
from services.agents.agents.tools import build_tool_registries

ROOT = Path(__file__).resolve().parents[1]
MOCK = ROOT / "mock"
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
TODAY = date(2026, 11, 15)
KEEP = 21  # days used for donor spare checks under a 3-week delay


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def data() -> CsvData:
    return CsvData(MOCK)


@pytest.fixture(scope="module")
def tools(data: CsvData):
    return build_tool_registries(data)


# ---------------------------------------------------------
# Data integrity
# ---------------------------------------------------------

def test_expanded_counts():
    assert len(_csv_rows(MOCK / "sites.csv")) == 6
    assert len(_csv_rows(MOCK / "drugs.csv")) == 22
    assert len(_csv_rows(MOCK / "inventory.csv")) == 132
    assert len(_csv_rows(MOCK / "usage.csv")) == 132
    assert 15 <= len(_csv_rows(MOCK / "open_orders.csv")) <= 25


def test_unique_sites_and_dins():
    sites = _csv_rows(MOCK / "sites.csv")
    drugs = _csv_rows(MOCK / "drugs.csv")
    assert len({row["site_id"] for row in sites}) == len(sites)
    assert len({row["din"] for row in drugs}) == len(drugs)
    for row in drugs:
        assert row["din"].isdigit() and len(row["din"]) == 8
        assert row["din"].startswith("9900")


def test_inventory_usage_references_and_nonnegative():
    site_ids = {row["site_id"] for row in _csv_rows(MOCK / "sites.csv")}
    dins = {row["din"] for row in _csv_rows(MOCK / "drugs.csv")}
    inv = _csv_rows(MOCK / "inventory.csv")
    usage = _csv_rows(MOCK / "usage.csv")

    inv_keys = {(row["site_id"], row["din"]) for row in inv}
    usage_keys = {(row["site_id"], row["din"]) for row in usage}
    assert inv_keys == usage_keys
    assert len(inv_keys) == len(inv)

    for row in inv:
        assert row["site_id"] in site_ids
        assert row["din"] in dins
        assert int(row["on_hand"]) >= 0
        assert row["as_of"] == "2026-11-15"

    for row in usage:
        assert float(row["avg_daily_use"]) >= 0


def test_orders_reference_known_entities_and_are_open():
    site_ids = {row["site_id"] for row in _csv_rows(MOCK / "sites.csv")}
    dins = {row["din"] for row in _csv_rows(MOCK / "drugs.csv")}
    for row in _csv_rows(MOCK / "open_orders.csv"):
        assert row["site_id"] in site_ids
        assert row["din"] in dins
        assert row["status"] == "open"
        assert int(row["qty"]) > 0
        date.fromisoformat(row["expected_date"])


def test_protected_emails_and_followup_still_present():
    for name in ("clear.txt", "ambiguous.txt", "hostile.txt", "sneaky.txt"):
        assert (MOCK / "emails" / name).exists()
    assert (MOCK / "followup" / "transfer_requests.csv").exists()
    assert (MOCK / "followup" / "replies.json").exists()


def test_original_abc_demo_numbers_preserved():
    """Expanded mock must keep the classic A/B/C cyclophosphamide demo intact."""
    inv = {
        (row["site_id"], row["din"]): int(row["on_hand"])
        for row in _csv_rows(MOCK / "inventory.csv")
    }
    usage = {
        (row["site_id"], row["din"]): float(row["avg_daily_use"])
        for row in _csv_rows(MOCK / "usage.csv")
    }
    assert inv[("A", "99000001")] == 60
    assert inv[("B", "99000001")] == 400
    assert inv[("C", "99000001")] == 150
    assert usage[("A", "99000001")] == 10
    assert usage[("B", "99000001")] == 8
    assert usage[("C", "99000001")] == 6


# ---------------------------------------------------------
# Agent loader + deterministic risk / spare tools
# ---------------------------------------------------------

def test_csvdata_loads_all_six_sites(data: CsvData):
    stock = data.stock("99000001")
    assert {row["site_id"] for row in stock} == {"A", "B", "C", "D", "E", "F"}


def test_scenario_a_critical_shortage_with_donor(data: CsvData, tools):
    _, planning = tools
    stock = data.stock("99000001")
    orders = data.orders("99000001")
    cover = cover_days_by_site(stock, orders, delay_days=21, today=TODAY)
    at_risk, shortfall = calculate_site_risk(stock, cover)

    assert cover["A"] == 24
    assert cover["B"] == 21
    assert cover["C"] == 21
    assert at_risk == ["A"]
    assert shortfall["A"] == 180

    spare_b = planning.execute(
        "get_spare_units",
        {"din": "99000001", "site_id": "B", "keep_days": cover["B"]},
    )
    assert spare_b["spare_units"] >= 180


def test_scenario_b_requires_multiple_donors(data: CsvData, tools):
    _, planning = tools
    stock = data.stock("99000011")
    cover = cover_days_by_site(stock, orders=[], delay_days=21, today=TODAY)
    at_risk, shortfall = calculate_site_risk(stock, cover)
    assert "A" in at_risk
    needed = shortfall["A"]
    assert needed > 100

    spare_b = planning.execute(
        "get_spare_units",
        {"din": "99000011", "site_id": "B", "keep_days": KEEP},
    )["spare_units"]
    spare_c = planning.execute(
        "get_spare_units",
        {"din": "99000011", "site_id": "C", "keep_days": KEEP},
    )["spare_units"]

    assert spare_b < needed
    assert spare_c < needed
    assert spare_b + spare_c >= needed

    stock_map = {
        row["site_id"]: {"on_hand": row["on_hand"], "daily_use": row["daily_use"]}
        for row in stock
    }
    single = PlanOption(
        type="transfer",
        description="single donor insufficient",
        moves=[Move(from_site="B", to_site="A", units=needed)],
        reasoning="test",
    )
    assert check_transfer(single, stock_map, {"A": needed}, floor_days=KEEP, reserve_days=cover)

    multi = PlanOption(
        type="transfer",
        description="two donors",
        moves=[
            Move(from_site="B", to_site="A", units=spare_b),
            Move(from_site="C", to_site="A", units=needed - spare_b),
        ],
        reasoning="test",
    )
    assert check_transfer(multi, stock_map, {"A": needed}, floor_days=KEEP, reserve_days=cover) == []


def test_scenario_c_no_feasible_donor(data: CsvData, tools):
    _, planning = tools
    stock = data.stock("99000012")
    cover = cover_days_by_site(stock, orders=[], delay_days=21, today=TODAY)
    at_risk, shortfall = calculate_site_risk(stock, cover)
    assert len(at_risk) >= 3
    assert sum(shortfall.values()) > 0

    for site_id in ("A", "B", "C", "D", "E", "F"):
        spare = planning.execute(
            "get_spare_units",
            {"din": "99000012", "site_id": site_id, "keep_days": KEEP},
        )["spare_units"]
        assert spare == 0


def test_scenario_d_competing_shortages(data: CsvData):
    stock = data.stock("99000017")
    orders = data.orders("99000017")
    cover = cover_days_by_site(stock, orders, delay_days=14, today=TODAY)
    at_risk, shortfall = calculate_site_risk(stock, cover)
    assert "A" in at_risk and "D" in at_risk
    assert shortfall["A"] > 0 and shortfall["D"] > 0


def test_scenario_f_substitution_record(data: CsvData, tools):
    _, planning = tools
    rows = planning.execute("get_approved_substitutions", {"din": "99000001"})
    assert any(row["substitute_din"] == "99000002" for row in rows)


def test_scenario_h_scarce_network(data: CsvData, tools):
    _, planning = tools
    stock = data.stock("99000021")
    cover = cover_days_by_site(stock, orders=[], delay_days=21, today=TODAY)
    at_risk, _ = calculate_site_risk(stock, cover)
    assert len(at_risk) >= 4
    total_spare = 0
    for site_id in ("A", "B", "C", "D", "E", "F"):
        total_spare += planning.execute(
            "get_spare_units",
            {"din": "99000021", "site_id": site_id, "keep_days": KEEP},
        )["spare_units"]
    assert total_spare == 0


def test_baseline_fixture_still_three_sites_only():
    baseline = CsvData(BASELINE)
    stock = baseline.stock("99000001")
    assert {row["site_id"] for row in stock} == {"A", "B", "C"}
    orders = baseline.orders("99000001")
    cover = cover_days_by_site(stock, orders, delay_days=21, today=TODAY)
    assert cover == {"A": 24, "B": 21, "C": 21}
    at_risk, shortfall = calculate_site_risk(stock, cover)
    assert at_risk == ["A"]
    assert shortfall == {"A": 180}


def test_restore_cover_waits_for_the_delivery_but_adds_no_delay():
    stock = [{"site_id": "A", "on_hand": 20, "daily_use": 10.0}, {"site_id": "B", "on_hand": 90, "daily_use": 4.0}]
    orders = [{"site_id": "A", "expected_date": "2026-12-05"}, {"site_id": "B", "expected_date": "2026-11-18"}]

    delayed = cover_days_by_site(stock, orders, delay_days=14, today=TODAY)
    restored = cover_days_by_site(stock, orders, delay_days=14, today=TODAY, restore=True)

    assert delayed == {"A": 34, "B": 17}            # a supplier delay pushes every delivery back
    assert restored == {"A": 20, "B": 14}           # restore: until the next delivery, at least 14 days


def test_every_option_type_is_checked_and_failures_are_dropped():
    from services.agents.rules.checks import check_options

    stock = {"A": {"on_hand": 20, "daily_use": 10}, "B": {"on_hand": 400, "daily_use": 8}}
    options = [
        PlanOption(type="transfer", description="t", moves=[Move(from_site="B", to_site="A", units=180)], reasoning="r"),
        PlanOption(type="transfer", description="too much", moves=[Move(from_site="B", to_site="A", units=390)], reasoning="r"),
        PlanOption(type="substitute", description="Use DIN 99000008", reasoning="approved"),
        PlanOption(type="substitute", description="Use DIN 12345678", reasoning="not in the table"),
        PlanOption(type="conserve", description="Cut use at A", reasoning="r"),
        PlanOption(type="escalate", description="Hand to a manager", reasoning="r"),
    ]

    passed, failed = check_options(options, stock, {"A": 180}, drug_din="99000011", approved_substitutes={"99000008"})

    assert [o.description for o in passed] == ["t", "Use DIN 99000008", "Cut use at A", "Hand to a manager"]
    assert sorted(failed) == [1, 3] and "not an approved substitute" in failed[3][0]
