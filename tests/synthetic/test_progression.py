"""Phase 2 progression: accounting, reproducibility, CsvData compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.synthetic.generate_progression import (
    FOCUS_DIN,
    NUM_DAYS,
    START,
    apply_delivery,
    load_world,
    prepare_days_dir,
    run_progression,
)
from services.data.data import CsvData
from services.rules.risk import calculate_site_risk
from services.agents.tools import build_tool_registries

ROOT = Path(__file__).resolve().parents[2]
MOCK = ROOT / "data" / "synthetic" / "mock"
OUT = ROOT / "data" / "synthetic" / "progression"


@pytest.fixture(scope="module")
def progression():
    world = run_progression(MOCK, OUT)
    return world


def test_progression_writes_ten_days(progression):
    days = sorted((OUT / "days").iterdir())
    assert len(days) == NUM_DAYS
    assert days[0].name == START.isoformat()
    assert (OUT / "ledger.json").exists()
    assert (OUT / "REPORT.md").exists()


def test_day1_matches_phase1_focus_stock(progression):
    day1 = CsvData(OUT / "days" / START.isoformat())
    mock = CsvData(MOCK)
    assert day1.stock(FOCUS_DIN) == mock.stock(FOCUS_DIN)


def test_no_negative_inventory_any_day(progression):
    for day_dir in (OUT / "days").iterdir():
        data = CsvData(day_dir)
        for drug in data._rows("drugs"):
            for row in data.stock(drug["din"]):
                assert row["on_hand"] >= 0
                assert row["daily_use"] >= 0


def test_transfer_received_balances(progression):
    ledger = json.loads((OUT / "ledger.json").read_text(encoding="utf-8"))
    received = [
        event
        for event in ledger
        if event["type"] == "transfer_received" and event["din"] == FOCUS_DIN
    ]
    assert received
    event = received[0]
    before = CsvData(OUT / "days" / "2026-11-18")  # end of day 4
    after = CsvData(OUT / "days" / "2026-11-19")  # end of day 5 (consume + transfer)

    before_map = {row["site_id"]: row for row in before.stock(FOCUS_DIN)}
    after_map = {row["site_id"]: row for row in after.stock(FOCUS_DIN)}
    units = event["units"]

    # Day 5 also consumes before the transfer. Reconstruct from ledger order:
    # consumption happens first, then transfer.
    daily_a = before_map["A"]["daily_use"]
    daily_b = before_map["B"]["daily_use"]
    expected_a = before_map["A"]["on_hand"] - int(daily_a) + units
    expected_b = before_map["B"]["on_hand"] - int(daily_b) - units
    assert after_map["A"]["on_hand"] == expected_a
    assert after_map["B"]["on_hand"] == expected_b


def test_rejected_transfer_does_not_move_stock(progression):
    day7 = CsvData(OUT / "days" / "2026-11-21")
    day6 = CsvData(OUT / "days" / "2026-11-20")
    # After day 7 consume, D and A change only by consumption / other non-D-transfer events.
    # There is a rejected D→A proposal: stock delta should equal consumption only for D.
    before = {row["site_id"]: row for row in day6.stock(FOCUS_DIN)}
    after = {row["site_id"]: row for row in day7.stock(FOCUS_DIN)}
    assert after["D"]["on_hand"] == before["D"]["on_hand"] - int(before["D"]["daily_use"])


def test_reproducible_ledger(progression):
    first = (OUT / "ledger.json").read_text(encoding="utf-8")
    run_progression(MOCK, OUT)
    second = (OUT / "ledger.json").read_text(encoding="utf-8")
    assert first == second


def test_risk_worsens_then_improves_for_site_a(progression):
    series = json.loads((OUT / "daily_risk.json").read_text(encoding="utf-8"))
    a_dos = [day["sites"]["A"]["days_of_supply"] for day in series]
    # Early decline after baseline, later recovery after partial transfer/delivery.
    assert a_dos[2] < a_dos[0]
    assert a_dos[-1] > a_dos[4]


def test_each_day_works_with_tool_registry(progression):
    data = CsvData(OUT / "days" / "2026-11-19")
    _, planning = build_tool_registries(data)
    spare = planning.execute(
        "get_spare_units",
        {"din": FOCUS_DIN, "site_id": "B", "keep_days": 14},
    )
    assert spare["spare_units"] >= 0
    stock = data.stock(FOCUS_DIN)
    # Shared horizon until delayed O-1001 (risk.py requires cover_days > 0).
    cover = {row["site_id"]: 18 for row in stock}
    at_risk, shortfall = calculate_site_risk(stock, cover)
    assert isinstance(at_risk, list)
    assert isinstance(shortfall, dict)
    assert "A" in {row["site_id"] for row in stock}


def test_source_mock_unchanged_by_generator(progression):
    # Spot-check Phase 1 baseline values remain in data/synthetic/mock/.
    world = load_world(MOCK)
    assert world.inventory[("A", FOCUS_DIN)] == 60
    assert world.inventory[("B", FOCUS_DIN)] == 400


def test_same_day_demand_spike_applies_before_consume(progression):
    day5 = {row["site_id"]: row for row in CsvData(OUT / "days" / "2026-11-19").stock(FOCUS_DIN)}
    day6 = {row["site_id"]: row for row in CsvData(OUT / "days" / "2026-11-20").stock(FOCUS_DIN)}
    assert day6["C"]["daily_use"] == 12
    assert day6["C"]["on_hand"] == day5["C"]["on_hand"] - 12


def test_delivery_rejects_wrong_site_or_din():
    world = load_world(MOCK)
    with pytest.raises(ValueError, match="does not match order"):
        apply_delivery(
            world,
            day=1,
            as_of=START,
            order_id="O-1001",
            site_id="B",
            din=FOCUS_DIN,
            units=10,
        )
    with pytest.raises(ValueError, match="does not match order"):
        apply_delivery(
            world,
            day=1,
            as_of=START,
            order_id="O-1001",
            site_id="A",
            din="99000002",
            units=10,
        )
    with pytest.raises(ValueError, match="positive"):
        apply_delivery(
            world,
            day=1,
            as_of=START,
            order_id="O-1001",
            site_id="A",
            din=FOCUS_DIN,
            units=0,
        )


def test_prepare_days_dir_refuses_unsafe_contents(tmp_path: Path):
    days = tmp_path / "days"
    days.mkdir()
    (days / "notes.txt").write_text("not a snapshot", encoding="utf-8")
    with pytest.raises(ValueError, match="Refusing to delete"):
        prepare_days_dir(tmp_path)


def test_prepare_days_dir_allows_date_snapshots(tmp_path: Path):
    days = tmp_path / "days"
    (days / "2026-11-15").mkdir(parents=True)
    prepared = prepare_days_dir(tmp_path)
    assert prepared.exists()
    assert list(prepared.iterdir()) == []


def test_report_documents_shared_horizon_and_scripted_boundary(progression):
    report = (OUT / "REPORT.md").read_text(encoding="utf-8")
    assert "shared disruption" in report.lower()
    assert "scripted" in report.lower()
    assert "not" in report.lower() and "bedrock" in report.lower()
