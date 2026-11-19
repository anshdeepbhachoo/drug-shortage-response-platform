"""Data shapes the order agent works with."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class WatchedOrder(BaseModel):
    """One approved purchase order the agent follows up."""

    order_id: str
    plan_id: str
    din: str
    site_id: str
    supplier_id: str
    supplier: str
    qty: int = Field(gt=0)
    status: str
    expected_date: date
    approved_at: datetime
    approver_role: str


class RequestDraft(BaseModel):
    subject: str
    body: str
