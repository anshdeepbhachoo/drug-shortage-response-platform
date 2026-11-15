import json
import types

from services.agents import agent


class Item:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def model_dump(self, exclude_none=True):
        return dict(self.__dict__)


def call_item(name, args, call_id):
    return Item(type="function_call", name=name, arguments=json.dumps(args), call_id=call_id)


def message(text):
    return types.SimpleNamespace(output=[Item(type="message")], output_text=text)


def empty_reasoning():
    return types.SimpleNamespace(output=[Item(type="reasoning")], output_text="")


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


def test_tools_return_expected_numbers():
    stock = {s["site_id"]: s for s in agent.get_site_stock("99000001")}
    assert stock["A"]["days_of_supply"] == 6.0
    assert agent.units_needed("99000001", "A", "24") == 180
    assert agent.spare_units("99000001", "B") == 288


def test_unknown_tool_is_refused(monkeypatch):
    fake = use_fake(monkeypatch, [
        types.SimpleNamespace(output=[call_item("delete_everything", {}, "c1")], output_text=""),
        message('{"next_action": "ignore"}'),
    ])
    text = agent.run_agent(agent.SIGNAL_GOAL, "x", [agent.SEARCH])
    assert text is not None
    sent_back = fake.calls[1]["input"]
    outputs = [i for i in sent_back if i.get("type") == "function_call_output"]
    assert outputs and "tool not allowed" in outputs[0]["output"]


def test_signal_flow_reaches_plan(monkeypatch):
    use_fake(monkeypatch, [
        types.SimpleNamespace(output=[call_item("search_drug_catalogue", {"query": "cyclophosphamide 500"}, "c1")], output_text=""),
        types.SimpleNamespace(output=[call_item("get_site_stock", {"din": "99000001"}, "c2")], output_text=""),
        message('{"next_action": "plan", "sites_at_risk": ["A"]}'),
    ])
    text = agent.run_agent(agent.SIGNAL_GOAL, "x", [agent.SEARCH, agent.STOCK])
    assert agent.as_json(text)["next_action"] == "plan"


def test_empty_reply_is_nudged_not_failed(monkeypatch):
    use_fake(monkeypatch, [
        empty_reasoning(),
        message('{"next_action": "needs_review"}'),
    ])
    text = agent.run_agent(agent.SIGNAL_GOAL, "x", [agent.SEARCH])
    assert agent.as_json(text)["next_action"] == "needs_review"