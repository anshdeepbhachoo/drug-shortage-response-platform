from services.agents.schemas import PlanOption


def check_transfer(
    option: PlanOption,
    stock: dict,
    shortfall: dict,
    floor_days: float = 14,
    reserve_days: dict | None = None,
) -> list[str]:
    """
    stock:        {"B": {"on_hand": 100, "daily_use": 2.5}}
    shortfall:    {"A": 30}
    reserve_days: optional per-site minimum days a donor must keep.
                  The floor used is max(floor_days, reserve_days[site]).

    Returns an empty list only when the transfer passes every check.
    """

    problems = []
    reserve_days = reserve_days or {}

    if option.type != "transfer":
        return ["Only transfer options can be verified by this function."]

    if not option.moves:
        return ["Transfer option contains no moves."]

    outgoing = {}
    incoming = {}

    for move in option.moves:
        if move.from_site == move.to_site:
            problems.append(f"{move.from_site} cannot transfer stock to itself.")
            continue

        if move.from_site not in stock:
            problems.append(f"Unknown donor site: {move.from_site}")
            continue

        if move.to_site not in stock:
            problems.append(f"Unknown receiving site: {move.to_site}")
            continue

        if move.from_site in shortfall:
            problems.append(
                f"{move.from_site} is itself short of stock and cannot donate."
            )

        if move.to_site not in shortfall:
            problems.append(f"{move.to_site} is not listed as requiring stock.")

        outgoing[move.from_site] = outgoing.get(move.from_site, 0) + move.units
        incoming[move.to_site] = incoming.get(move.to_site, 0) + move.units

    for site_id, units in outgoing.items():
        site = stock[site_id]
        on_hand = site["on_hand"]
        daily_use = site["daily_use"]
        minimum_days = max(floor_days, reserve_days.get(site_id, 0))

        remaining = on_hand - units

        if remaining < 0:
            problems.append(
                f"{site_id} would send {units} units but only has {on_hand}."
            )
            continue

        if daily_use > 0:
            remaining_days = remaining / daily_use

            if remaining_days < minimum_days:
                problems.append(
                    f"{site_id} would have {remaining_days:.1f} days of supply "
                    f"remaining; minimum is {minimum_days}."
                )

    for site_id, needed in shortfall.items():
        received = incoming.get(site_id, 0)

        if received < needed:
            problems.append(
                f"{site_id} receives {received} units but needs {needed}."
            )

    return problems


def filter_options(
    options: list[PlanOption],
    stock: dict,
    shortfall: dict,
    reserve_days: dict | None = None,
) -> tuple[list[PlanOption], dict]:
    """Keep only transfer options that pass deterministic checks."""

    verified = []
    rejected = {}

    for index, option in enumerate(options):
        if option.type != "transfer":
            rejected[index] = [
                f"{option.type} requires a separate verification "
                "rule and human review."
            ]
            continue

        problems = check_transfer(
            option, stock, shortfall, reserve_days=reserve_days
        )

        if problems:
            rejected[index] = problems
        else:
            verified.append(option)

    return verified, rejected