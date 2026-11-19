"""Deterministic rules for the follow-up agent.

Everything numeric lives here: lateness, projected cover, which decisions are
allowed, the hard rules that bypass the model, the fallback policy used when
the model is unavailable, and a deterministic replanner.

The model never computes these numbers. It only chooses among the decisions
this module allows and drafts the wording of messages.
"""

import math
from datetime import datetime

from services.agents.checks import filter_options
from services.agents.followup_schemas import ReplyReading, TransferRequest
from services.agents.schemas import Move, PlanOption

# ---------------------------------------------------------
# Thresholds (demo defaults; agree real values with pharmacy)
# ---------------------------------------------------------

ESCALATE_COVER_DAYS = 3      # receiving site below this many days -> escalate, no model
REVIEW_COVER_DAYS = 4        # below this, the model may choose to escalate early
MAX_NUDGES = 2               # after this many reminders, stop nudging
NUDGE_COOLDOWN_HOURS = 12    # don't nudge the same site twice within this window
DONOR_KEEP_DAYS = 14         # replan donors must keep this many days (matches checks.py)
MIN_READING_CONFIDENCE = 0.7 # below this, a reply reading is not trusted


# ---------------------------------------------------------
# Facts
# ---------------------------------------------------------

def project_stock(stock_rows, snapshot: datetime, now: datetime):
    """Project each site's on-hand units forward from the snapshot using daily use."""

    elapsed_days = max(0.0, (now - snapshot).total_seconds() / 86400)

    return {
        row["site_id"]: {
            "on_hand": max(0.0, row["on_hand"] - row["daily_use"] * elapsed_days),
            "daily_use": row["daily_use"],
        }
        for row in stock_rows
    }


def compute_facts(
    request: TransferRequest,
    stock_rows,
    snapshot: datetime,
    now: datetime,
    nudges_sent: int,
):
    """Numbers the decision is based on. Confirmed units are assumed to arrive."""

    projected = project_stock(stock_rows, snapshot, now)
    receiver = projected[request.to_site]

    receiver_on_hand = receiver["on_hand"] + request.units_confirmed
    daily_use = receiver["daily_use"]
    cover_days = (
        round(receiver_on_hand / daily_use, 2) if daily_use > 0 else math.inf
    )

    hours_overdue = (now - request.deadline).total_seconds() / 3600

    hours_since_nudge = None
    if request.last_nudged_at is not None:
        hours_since_nudge = round(
            (now - request.last_nudged_at).total_seconds() / 3600, 1
        )

    return {
        "now": now.isoformat(),
        "deadline": request.deadline.isoformat(),
        "overdue": hours_overdue > 0,
        "hours_overdue": round(max(0.0, hours_overdue), 1),
        "units_requested": request.units,
        "units_confirmed": request.units_confirmed,
        "units_outstanding": max(0, request.units - request.units_confirmed),
        "receiving_site": request.to_site,
        "receiving_on_hand": round(receiver_on_hand, 1),
        "receiving_daily_use": daily_use,
        "receiving_cover_days": cover_days,
        "nudges_sent": nudges_sent,
        "hours_since_last_nudge": hours_since_nudge,
    }


# ---------------------------------------------------------
# Reply checks
# ---------------------------------------------------------

def check_reading(reading: ReplyReading, request: TransferRequest):
    """Code checks on what the reader agent extracted. Returns (reading, problems)."""

    problems = []

    if reading.confidence < MIN_READING_CONFIDENCE:
        problems.append(
            f"Reader confidence {reading.confidence} is below {MIN_READING_CONFIDENCE}."
        )

    if reading.confirmed_units is not None and reading.confirmed_units > request.units:
        problems.append(
            f"Reply confirms {reading.confirmed_units} units but only "
            f"{request.units} were requested."
        )

    if reading.kind == "full_confirmation":
        if reading.confirmed_units is None:
            reading = reading.model_copy(update={"confirmed_units": request.units})
        elif reading.confirmed_units < request.units:
            # The model called it "full" but the numbers say partial.
            reading = reading.model_copy(update={"kind": "partial_confirmation"})

    if reading.kind == "partial_confirmation" and reading.confirmed_units is None:
        problems.append("Partial confirmation without a unit count.")

    return reading, problems


# ---------------------------------------------------------
# Decision rules
# ---------------------------------------------------------

def hard_rule(request: TransferRequest, facts: dict, reply_flags: list[str]):
    """Return (decision, reason) when the outcome is not a judgement call, else None."""

    if request.approval_status == "rejected":
        return "close", "Transfer was rejected by the approver; nothing to follow up."

    if request.approval_status == "pending":
        return "wait", "Transfer is still awaiting human approval."

    if reply_flags:
        return "escalate", "Site reply failed safety checks: " + "; ".join(reply_flags)

    if facts["units_outstanding"] == 0:
        return "close", "All requested units are confirmed."

    if facts["receiving_cover_days"] < ESCALATE_COVER_DAYS:
        return (
            "escalate",
            f"Receiving site {facts['receiving_site']} has "
            f"{facts['receiving_cover_days']} days of cover, below the "
            f"{ESCALATE_COVER_DAYS}-day limit.",
        )

    return None


def allowed_decisions(facts: dict, reading: ReplyReading | None):
    """Decisions the model may choose from in a grey zone."""

    allowed = {"nudge", "replan"}
    kind = reading.kind if reading is not None else None

    nudge_too_soon = (
        facts["hours_since_last_nudge"] is not None
        and facts["hours_since_last_nudge"] < NUDGE_COOLDOWN_HOURS
    )
    nudges_used_up = facts["nudges_sent"] >= MAX_NUDGES
    if nudges_used_up or nudge_too_soon:
        allowed.discard("nudge")

    # A site that said no, or said it is keeping the rest, won't answer a reminder.
    if kind in {"refusal", "partial_confirmation"}:
        allowed.discard("nudge")

    # Escalating pulls in a manager, so only offer it when there is a reason:
    # cover is getting short, reminders have run out, or the site asked
    # something / refused and a person has to respond.
    if (
        facts["receiving_cover_days"] < REVIEW_COVER_DAYS
        or nudges_used_up
        or kind in {"refusal", "question"}
    ):
        allowed.add("escalate")

    # Waiting is only safe before the deadline, with no bad news.
    if not facts["overdue"] and kind not in {"partial_confirmation", "refusal"}:
        allowed.add("wait")

    return allowed


def needs_attention(facts: dict, reading: ReplyReading | None):
    """True when something happened that deserves a decision."""

    return facts["overdue"] or reading is not None


def fallback_decision(facts: dict, reading: ReplyReading | None, allowed: set[str]):
    """Rule-based choice used when the model is unavailable or misbehaves."""

    if reading is not None and reading.kind in {"partial_confirmation", "refusal"}:
        return "replan", "Site cannot send the full amount; find the rest elsewhere."

    if "wait" in allowed:
        return "wait", "Deadline has not passed yet."

    if "nudge" in allowed:
        return "nudge", (
            f"Transfer is {facts['hours_overdue']} hours overdue with no "
            "confirmation; send a reminder."
        )

    if "escalate" in allowed and facts["receiving_cover_days"] < REVIEW_COVER_DAYS:
        return "escalate", "Reminders have not worked and cover is getting low."

    return "replan", "Reminders have not worked; find another donor."


def recipient_for(decision: str, request: TransferRequest):
    """Code, not the model, decides who a message goes to."""

    if decision == "nudge":
        return f"Site {request.from_site}"
    if decision == "escalate":
        return request.approver_role
    return None


# ---------------------------------------------------------
# Message templates (used when no model wording is available)
# ---------------------------------------------------------

def template_message(decision: str, request: TransferRequest, facts: dict):
    if decision == "nudge":
        return (
            recipient_for(decision, request),
            f"Reminder: transfer {request.request_id} ({request.units} units of "
            f"DIN {request.din} to site {request.to_site}) was due "
            f"{request.deadline:%b %d %H:%M}. "
            f"{facts['units_outstanding']} units are still unconfirmed. "
            "Please confirm a ship time or tell us if you cannot send them.",
        )

    if decision == "escalate":
        return (
            recipient_for(decision, request),
            f"Escalation: transfer {request.request_id} for DIN {request.din} "
            f"has {facts['units_outstanding']} units outstanding. Site "
            f"{request.to_site} has about {facts['receiving_cover_days']} days "
            "of cover. Human decision needed.",
        )

    return None, None


# ---------------------------------------------------------
# Deterministic replan
# ---------------------------------------------------------

def replan_inputs(request: TransferRequest, stock_rows, snapshot, now):
    """Projected stock with the failed donor's committed units moved, and the gap to fill."""

    projected = project_stock(stock_rows, snapshot, now)
    projected[request.from_site]["on_hand"] -= request.units_confirmed
    projected[request.to_site]["on_hand"] += request.units_confirmed

    outstanding = request.units - request.units_confirmed
    shortfall = {request.to_site: outstanding}

    return projected, shortfall


def exclude_failed_donor(options, failed_site):
    kept, dropped = [], []
    for option in options:
        if any(move.from_site == failed_site for move in option.moves):
            dropped.append(option)
        else:
            kept.append(option)
    return kept, dropped


def deterministic_replan(request: TransferRequest, stock_rows, snapshot, now):
    """Pick the donor (not the failed site) with the most spare stock above its reserve."""

    stock, shortfall = replan_inputs(request, stock_rows, snapshot, now)
    needed = shortfall[request.to_site]

    candidates = []
    for site_id, site in stock.items():
        if site_id in {request.from_site, request.to_site}:
            continue
        spare = math.floor(site["on_hand"] - site["daily_use"] * DONOR_KEEP_DAYS)
        if spare > 0:
            candidates.append((spare, site_id))

    candidates.sort(reverse=True)
    options = []

    for spare, site_id in candidates:
        if spare >= needed:
            options.append(
                PlanOption(
                    type="transfer",
                    description=f"Move {needed} units from {site_id} to {request.to_site}.",
                    moves=[Move(from_site=site_id, to_site=request.to_site, units=needed)],
                    reasoning=f"{site_id} has {spare} units above its "
                              f"{DONOR_KEEP_DAYS}-day reserve.",
                )
            )
            break

    verified, rejected = filter_options(options, stock, shortfall)

    return {
        "source": "rules",
        "shortfall": shortfall,
        "verified_options": [option.model_dump() for option in verified],
        "rejected_options": rejected,
        "no_verified_plan": not verified,
    }
