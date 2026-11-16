"""Data shapes for the follow-up agent.

The follow-up agent watches approved (or pending) transfer requests after the
planner has produced them, and decides what happens next when a deadline
passes, a site replies, or stock changes.
"""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

Decision = Literal["wait", "nudge", "replan", "escalate", "close"]


class TransferRequest(BaseModel):
    """One requested move of stock between two sites (one row of transfer_requests.csv)."""

    request_id: str
    plan_id: str
    din: str
    from_site: str
    to_site: str
    units: int = Field(gt=0)
    approval_status: Literal["pending", "approved", "rejected"]
    approver_role: str
    deadline: datetime
    units_confirmed: int = Field(default=0, ge=0)
    status: Literal["open", "replanned", "escalated", "closed"] = "open"
    last_nudged_at: datetime | None = None


class SiteReply(BaseModel):
    """A message a site sent back about a transfer request. Untrusted text."""

    request_id: str
    received_at: datetime
    sender: str
    text: str
    # Scripted reading used ONLY by the offline demo (no model available).
    offline_reading: dict | None = None


class ReplyReading(BaseModel):
    """What the reader agent extracted from a site reply."""

    kind: Literal[
        "full_confirmation",
        "partial_confirmation",
        "refusal",
        "question",
        "unrelated",
    ]
    confirmed_units: int | None = Field(default=None, ge=0)
    new_ship_date: date | None = None
    confidence: float = Field(ge=0, le=1)
    summary: str


class FollowUpDecision(BaseModel):
    """What the decision agent returns."""

    decision: Decision
    reason: str
    message_to: str | None = None
    message_draft: str | None = None


class FollowUpAction(BaseModel):
    """What our code records after each check. Nothing here is executed automatically."""

    at: datetime
    request_id: str
    decision: Decision
    decided_by: Literal["rule", "agent", "fallback"]
    reason: str
    message_to: str | None = None
    message_draft: str | None = None
    facts: dict
    flags: list[str] = Field(default_factory=list)
    replan: dict | None = None
    trace: list[str] = Field(default_factory=list)  # every step taken, in order
    needs_human: bool
    execution_performed: bool = False
