"""Shortage reports as signals, process_event, tools and the data-source switch. No database."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from services.agents.signals.process import process_event
from services.data.data import CsvData
from services.data.data_source import default_data
from services.agents.schemas import SupplyEvent
from services.pipeline.shortage_signals import DEFAULT_SHORTAGE_DAYS, event_from_report
from services.agents.tools import build_tool_registries
from tests.agents.test_agent import FakeModel, transfer

ROOT = Path(__file__).resolve().parents[2]
BASELINE = ROOT / "tests" / "fixtures" / "baseline_three_site"
TODAY = date(2026, 11, 15)


def report(**overrides):
    value = {"report_id": 501, "din": "99000001", "drug_name": "Cyclophosphamide 500 mg vial",
             "status": "active_confirmed", "tier3": True, "shortage_reason": "Manufacturing delay",
             "estimated_end_date": datetime(2026, 12, 6, tzinfo=timezone.utc)}
    value.update(overrides)
    return value


# ---------------------------------------------------------
# Report -> event (code, no model)
# ---------------------------------------------------------

def test_event_covers_until_estimated_end():
    event = event_from_report(report(), TODAY)

    assert event.drug_din == "99000001"
    assert event.next_action == "plan"
    assert event.confidence == 1.0
    assert round(event.delay_weeks * 7) == 21          # Nov 15 -> Dec 6
    assert "Tier 3" in event.reason and "501" in event.reason


@pytest.mark.parametrize("end", [None, datetime(2026, 11, 1, tzinfo=timezone.utc)])
def test_unknown_or_past_end_date_uses_default_horizon(end):
    event = event_from_report(report(estimated_end_date=end), TODAY)
    assert round(event.delay_weeks * 7) == DEFAULT_SHORTAGE_DAYS


@pytest.mark.parametrize("days", range(1, 40))
def test_delay_days_survive_round_trip(days):
    # process_event uses ceil(weeks * 7); it must give back exactly the same days.
    import math
    end = datetime(2026, 11, 15, tzinfo=timezone.utc)
    from datetime import timedelta
    event = event_from_report(report(estimated_end_date=end + timedelta(days=days)), TODAY)
    assert math.ceil(event.delay_weeks * 7) == days


# ---------------------------------------------------------
# process_event: planning without an email
# ---------------------------------------------------------

def test_structured_event_is_planned_without_assessment():
    model = FakeModel(plans=[transfer("B", 180)])

    result = process_event(event_from_report(report(), TODAY), data=CsvData(BASELINE), llm=model, today=TODAY)

    assert result["status"] == "plan_generated"
    assert result["shortfall"] == {"A": 180}
    assert model.count("assessment") == 0 and model.count("planning") == 1
    assert result["execution_performed"] is False


def test_event_not_marked_plan_is_not_planned():
    event = SupplyEvent(drug_din="99000001", confidence=1.0, next_action="needs_review", reason="x")
    model = FakeModel()

    assert process_event(event, data=CsvData(BASELINE), llm=model)["status"] == "needs_review"
    assert model.calls == []


def test_model_failure_becomes_error_result():
    model = FakeModel(plans=[RuntimeError("Bedrock down")])
    result = process_event(event_from_report(report(), TODAY), data=CsvData(BASELINE), llm=model, today=TODAY)
    assert result["status"] == "error" and "Bedrock down" in result["reason"]


# ---------------------------------------------------------
# Tools and data source
# ---------------------------------------------------------

def test_csv_mode_keeps_exactly_the_old_tools():
    assessment, planning = build_tool_registries(CsvData(BASELINE))
    assert assessment.names == {"search_drug_catalogue", "get_site_stock", "get_open_orders"}
    assert planning.names == {"get_site_stock", "get_open_orders", "get_approved_substitutions", "get_spare_units"}


def test_reference_tools_appear_when_data_has_them():
    class WithReference(CsvData):
        def shortage_reports(self, din):
            return [{"report_id": 1, "status": "active_confirmed"}]

        def drug_product(self, din):
            return {"din": din, "brand_name": "X"}

    assessment, planning = build_tool_registries(WithReference(BASELINE))

    for registry in (assessment, planning):
        assert {"get_shortage_reports", "get_drug_product"} <= registry.names
    assert planning.execute("get_shortage_reports", {"din": "99000001"})[0]["report_id"] == 1


def test_default_data_is_csv_unless_switched(monkeypatch):
    monkeypatch.delenv("AGENT_DATA_SOURCE", raising=False)
    assert isinstance(default_data(), CsvData)

    monkeypatch.setenv("AGENT_DATA_SOURCE", "sqlite")
    with pytest.raises(RuntimeError, match="csv' or 'postgres"):
        default_data()
