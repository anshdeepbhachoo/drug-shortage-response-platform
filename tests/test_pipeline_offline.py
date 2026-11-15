import json
import types

from services.agents import agent
from services.agents.schemas import SupplyEvent


class Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


def call_item(name, args, call_id):
    return Item(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def message(text):
    return types.SimpleNamespace(output=[Item(type="message")], output_text=text)


class FakeResponses:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.script.pop(0)


def use_fake(monkeypatch, script):
    fake = FakeResponses(script)
    monkeypatch.setattr(agent, "_client", types.SimpleNamespace(responses=fake))
    return fake


def test_cover_days_is_order_date_plus_delay():
    event = SupplyEvent(drug_din="99000001", sites_at_risk=["A"], delay_weeks=3,
                        confidence=0.9, next_action="plan", reason="x")
    assert agent.cover_days_for(event) == {"A": 24}


def test_repeated_identical_call_is_stopped(monkeypatch):
    fake = use_fake(monkeypatch, [
        types.SimpleNamespace(output=[call_item("search_drug_catalogue", {"query": "cyclo"}, "c1")], output_text=""),
        types.SimpleNamespace(output=[call_item("search_drug_catalogue", {"query": "cyclo"}, "c2")], output_text=""),
        message('{"next_action": "needs_review"}'),
    ])
    text = agent.run_agent(agent.SIGNAL_GOAL, "x", [agent.SEARCH])
    assert text is not None
    outputs = [i for i in fake.calls[2]["input"] if i.get("type") == "function_call_output"]
    assert "repeated call" in outputs[-1]["output"]


def test_bad_plan_is_rejected_end_to_end(monkeypatch):
    signal = message(json.dumps({
        "drug_din": "99000001", "drug_name": "Cyclophosphamide 500 mg vial",
        "delay_weeks": 3, "sites_at_risk": ["A"], "confidence": 0.9,
        "next_action": "plan", "reason": "test",
    }))
    plan = message(json.dumps({
        "options": [
            {"type": "transfer", "description": "too much from B",
             "moves": [{"from_site": "B", "to_site": "A", "units": 300}], "reasoning": "x"},
            {"type": "substitute", "description": "swap", "moves": [], "reasoning": "x"},
        ],
        "no_perfect_plan": False,
    }))
    use_fake(monkeypatch, [signal, plan])
    res = agent.process_email("The 500 mg vial is delayed 3 weeks.")
    assert res["status"] == "no_verified_plan"
    assert res["accepted"] == []
    assert any("B would drop" in r for r in res["rejected"]["0"])
    assert "not checked by code" in res["rejected"]["1"][0]