import math
from datetime import date


def cover_days_by_site(stock_rows, orders, delay_days, today, restore=False):
    """
    Days each site must last: days until its earliest open order was due
    (never negative), plus the supplier delay. Sites with no open order
    fall back to the delay alone.

    restore=True is for alerts with no supplier delay (stock watch, a person
    asking): each site must last delay_days, or until its next delivery if
    that comes later. Nothing is added on top of the delivery date.
    """

    earliest_due = {}

    for order in orders:
        due = date.fromisoformat(order["expected_date"])
        site_id = order["site_id"]

        if site_id not in earliest_due or due < earliest_due[site_id]:
            earliest_due[site_id] = due

    cover = {}

    for row in stock_rows:
        site_id = row["site_id"]
        days_to_due = 0

        if site_id in earliest_due:
            days_to_due = max(0, (earliest_due[site_id] - today).days)

        cover[site_id] = max(days_to_due, delay_days) if restore else days_to_due + delay_days

    return cover


def calculate_site_risk(stock_rows, cover_days):
    """
    Identify sites whose current stock will not last cover_days[site].

    Returns:
        at_risk: list of affected site IDs
        shortfall: units each affected site needs
    """

    at_risk = []
    shortfall = {}

    for row in stock_rows:
        site_id = row["site_id"]
        on_hand = row["on_hand"]
        daily_use = row["daily_use"]

        if daily_use < 0 or on_hand < 0:
            raise ValueError(f"Invalid stock data for {site_id}.")

        days = cover_days[site_id]

        if days <= 0:
            raise ValueError(f"cover_days for {site_id} must be positive.")

        units_needed = max(0, math.ceil(daily_use * days) - on_hand)

        if units_needed > 0:
            at_risk.append(site_id)
            shortfall[site_id] = units_needed

    return at_risk, shortfall