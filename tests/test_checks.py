from services.agents.checks import check_transfer
from services.agents.schemas import Move, PlanOption, PlanSet, SupplyEvent

STOCK = {
    "A": {"on_hand": 60, "daily_use": 10.0},
    "B": {"on_hand": 400, "daily_use": 8.0},
    "C": {"on_hand": 150, "daily_use": 6.0},
}
SHORTFALL = {"A": 180}


def transfer(*moves):
    return PlanOption(
        type="transfer",
        description="t",
        moves=[Move(from_site=f, to_site=t, units=u) for f, t, u in moves],
        reasoning="r",
    )


def test_valid_transfer_passes():
    assert check_transfer(transfer(("B", "A", 180)), STOCK, SHORTFALL) == []


def test_donor_below_floor_fails():
    problems = check_transfer(transfer(("B", "A", 300)), STOCK, SHORTFALL)
    assert any("B would drop" in p for p in problems)


def test_under_coverage_fails():
    problems = check_transfer(transfer(("B", "A", 100)), STOCK, SHORTFALL)
    assert any("A receives 100 of the 180" in p for p in problems)


def test_self_move_fails():
    problems = check_transfer(transfer(("A", "A", 10)), STOCK, SHORTFALL)
    assert any("to itself" in p for p in problems)


def test_split_transfer_passes():
    assert check_transfer(transfer(("B", "A", 114), ("C", "A", 66)), STOCK, SHORTFALL) == []


def test_event_plan_requires_drug_and_sites():
    try:
        SupplyEvent(confidence=0.9, next_action="plan", reason="x")
        raise AssertionError("should have failed")
    except ValueError:
        pass


def test_planset_rejects_too_many_options():
    opt = transfer(("B", "A", 180))
    try:
        PlanSet(options=[opt, opt, opt, opt], no_perfect_plan=False)
        raise AssertionError("should have failed")
    except ValueError:
        pass