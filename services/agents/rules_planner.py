"""A planner made of rules, for when no model is available (or for a fast, repeatable demo).

Same contract as the planning agent: it returns a PlanSet, and its options go
through exactly the same code checks (checks.filter_options) before anyone
sees them. It never invents anything:

1. Transfer: take spare stock from the donors with the most units above their
   reserve, until every short site is covered. Offered only if it covers the
   whole shortfall.
2. Substitute: an existing approved substitution record, for pharmacist review.
3. Escalate: when no transfer can cover the gap, say so and hand it to a person.
"""

from __future__ import annotations

import math

from services.agents.schemas import Move, PlanOption, PlanSet

MIN_RESERVE_DAYS = 14  # same floor as checks.check_transfer
# Donors keep a few days above their floor so the plan stays safe while it waits
# for approval; at exactly the floor it would break after one more day of use.
APPROVAL_SLACK_DAYS = 3


def rules_planner(*, event, stock_rows, data, delay_days, cover_days, risk_sites, shortfall) -> PlanSet:
    stock = {row["site_id"]: row for row in stock_rows}
    spare = {}
    for site_id, row in stock.items():
        if site_id in risk_sites or row["daily_use"] <= 0:
            continue
        keep_days = max(MIN_RESERVE_DAYS, cover_days.get(site_id, 0)) + APPROVAL_SLACK_DAYS
        spare[site_id] = max(0, math.floor(row["on_hand"] - row["daily_use"] * keep_days))

    options: list[PlanOption] = []
    total_need = sum(shortfall.values())
    if sum(spare.values()) >= total_need:
        moves, left = [], dict(spare)
        for site_id in sorted(shortfall, key=shortfall.get, reverse=True):  # biggest gap first
            need = shortfall[site_id]
            for donor in sorted(left, key=left.get, reverse=True):         # most spare first
                if need <= 0:
                    break
                units = min(need, left[donor])
                if units > 0:
                    moves.append(Move(from_site=donor, to_site=site_id, units=units))
                    left[donor] -= units
                    need -= units
        donors = sorted({m.from_site for m in moves})
        options.append(PlanOption(
            type="transfer",
            description="Transfer " + ", ".join(f"{m.units} from {m.from_site} to {m.to_site}" for m in moves),
            moves=moves,
            reasoning=(f"Rules planner: {total_need} units needed across {', '.join(sorted(shortfall))}; "
                       f"taken from the donors with the most spare stock above their reserve ({', '.join(donors)})."),
        ))

    for record in data.substitutions(event.drug_din):
        if str(record.get("approved", "")).lower() in {"yes", "true", "1"}:
            options.append(PlanOption(
                type="substitute",
                description=f"Consider approved substitute DIN {record['substitute_din']}",
                moves=[],
                reasoning=f"Rules planner: approved substitution record: {record.get('note') or 'no note'}. Needs a pharmacist.",
            ))
            break

    covered = bool(options) and options[0].type == "transfer"
    if not covered:
        options.append(PlanOption(
            type="escalate",
            description="No hospital can spare enough: escalate to a manager",
            moves=[],
            reasoning=(f"Rules planner: {total_need} units needed, but donors can spare only "
                       f"{sum(spare.values())} while keeping their reserve. A person must decide "
                       "(emergency order, conservation, or clinical alternatives)."),
        ))

    return PlanSet(options=options[:3], no_perfect_plan=not covered)
