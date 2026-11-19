"""Stock watch and the rules planner. No database: CsvData over the progression days."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from services.pipeline.daily_run import event_from_watch, find_at_risk, load_inbox
from services.data.data import CsvData
from services.rules.rules_planner import rules_planner
from services.agents.schemas import SupplyEvent

ROOT = Path(__file__).resolve().parents[1]
DAYS = ROOT / "data" / "synthetic" / "progression" / "days"
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"


def day(d):
    return CsvData(DAYS / f"2026-11-{d}")


def drugs(data):
    return [(r["din"], r["name"], r["criticality"]) for r in data._rows("drugs")]


def test_critical_drugs_are_flagged_two_weeks_ahead():
    data = day(15)

    found = {f["din"]: f for f in find_at_risk(data, None, date(2026, 11, 15), drugs=drugs(data))}

    # Cisplatin at Central General has 13.3 days: too early for a medium drug, but it is critical.
    assert "99000005" in found and found["99000005"]["criticality"] == "critical"
    # Propofol is "high": flagged under 10 days. Saline (medium, plenty) is not flagged.
    assert found["99000012"]["criticality"] == "high"
    assert "99000019" not in found


def test_stock_watch_finds_the_scenario_problems_on_day_one():
    data = day(15)

    found = find_at_risk(data, None, date(2026, 11, 15), urgent_days=7, drugs=drugs(data))

    assert [f["din"] for f in found] == ["99000011", "99000021", "99000012"]   # most urgent first
    epinephrine = found[0]
    assert epinephrine["sites"][0]["site_id"] == "A" and epinephrine["sites"][0]["days_left"] == 2.0
    assert len(found[2]["sites"]) == 6                                           # propofol short everywhere


def test_site_with_delivery_before_it_runs_out_is_not_flagged():
    data = day(15)
    found = {f["din"]: f for f in find_at_risk(data, None, date(2026, 11, 15), urgent_days=7, drugs=drugs(data))}
    # Cyclophosphamide at Central General: 6 days left, but O-1001 arrives in 3.
    assert "99000001" not in found


def test_demand_spike_is_labelled_as_such():
    today, yesterday = day(20), day(19)
    found = find_at_risk(today, yesterday, date(2026, 11, 20), urgent_days=10, drugs=drugs(today))
    harbour = next(f for f in found if f["din"] == "99000001")
    assert harbour["kind"] == "usage_spike"
    assert [s["site_id"] for s in harbour["sites"]] == ["C"]


def test_watch_event_is_a_plan_ready_supply_event():
    data = day(15)
    alert = find_at_risk(data, None, date(2026, 11, 15), drugs=drugs(data))[0]

    event = event_from_watch(alert)

    assert event.next_action == "plan" and event.confidence == 1.0
    assert round(event.delay_weeks * 7) == 14
    assert "Stock watch" in event.reason and "Central General" in event.reason


def test_inbox_schedule_covers_every_day_and_names_real_files():
    inbox = load_inbox()
    assert sorted(inbox) == [f"2026-11-{d}" for d in range(15, 25)]
    for files in inbox.values():
        for name in files:
            assert (ROOT / "data" / "synthetic" / "mock" / "emails" / name).is_file(), name


# ---------------------------------------------------------
# Rules planner
# ---------------------------------------------------------

def plan(shortfall, risk_sites, cover_days, data=CsvData(BASELINE), din="99000001"):
    event = SupplyEvent(drug_din=din, confidence=1.0, next_action="plan", delay_weeks=3, reason="t")
    return rules_planner(event=event, stock_rows=data.stock(din), data=data, delay_days=21,
                         cover_days=cover_days, risk_sites=risk_sites, shortfall=shortfall)


def test_rules_planner_covers_from_the_biggest_donor():
    result = plan({"A": 180}, ["A"], {"A": 24, "B": 21, "C": 21})

    transfer = result.options[0]
    assert transfer.type == "transfer" and not result.no_perfect_plan
    assert [(m.from_site, m.to_site, m.units) for m in transfer.moves] == [("B", "A", 180)]
    assert any(o.type == "substitute" for o in result.options)       # approved 1000 mg record


def test_rules_planner_splits_across_donors_when_needed():
    # Donors keep 21 days + 3 days of approval slack: B can spare 208, C 6. 210 needs both.
    result = plan({"A": 210}, ["A"], {"A": 27, "B": 21, "C": 21})
    assert sorted((m.from_site, m.units) for m in result.options[0].moves) == [("B", 208), ("C", 2)]


def test_rules_planner_escalates_when_nobody_can_spare_enough():
    result = plan({"A": 400}, ["A"], {"A": 46, "B": 21, "C": 21})

    assert result.no_perfect_plan
    assert result.options[-1].type == "escalate"
    assert all(o.type != "transfer" for o in result.options)
