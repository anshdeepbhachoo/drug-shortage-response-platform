from services.agents.schemas import PlanOption


def check_transfer(option: PlanOption, stock: dict, shortfall: dict, floor_days: float = 14) -> list[str]:
    """stock: {site_id: {"on_hand": int, "daily_use": float}}
    shortfall: {site_id: units the at-risk site needs}
    Returns a list of problems. An empty list means the transfer is acceptable."""
    problems = []
    outgoing = {}
    incoming = {}

    for m in option.moves:
        if m.from_site == m.to_site:
            problems.append(f"move from {m.from_site} to itself")
        for site in (m.from_site, m.to_site):
            if site not in stock:
                problems.append(f"unknown site {site}")
        outgoing[m.from_site] = outgoing.get(m.from_site, 0) + m.units
        incoming[m.to_site] = incoming.get(m.to_site, 0) + m.units

    for site, units in outgoing.items():
        if site not in stock:
            continue
        left = stock[site]["on_hand"] - units
        days_left = left / stock[site]["daily_use"] if stock[site]["daily_use"] else float("inf")
        if left < 0:
            problems.append(f"{site} gives {units} but only has {stock[site]['on_hand']}")
        elif days_left < floor_days:
            problems.append(f"{site} would drop to {days_left:.1f} days (floor {floor_days})")

    for site, needed in shortfall.items():
        if incoming.get(site, 0) < needed:
            problems.append(f"{site} receives {incoming.get(site, 0)} of the {needed} it needs")

    return problems


def filter_options(options: list[PlanOption], stock: dict, shortfall: dict) -> tuple[list[PlanOption], dict]:
    """Keep transfers that pass the checks. Return (kept, rejected_with_reasons)."""
    kept, rejected = [], {}
    for i, opt in enumerate(options):
        if opt.type != "transfer":
            rejected[i] = [f"{opt.type} options are not checked by code yet"]
            continue
        problems = check_transfer(opt, stock, shortfall)
        if problems:
            rejected[i] = problems
        else:
            kept.append(opt)
    return kept, rejected