"""Data shapes for the order agent.

An order moves through the same statuses as the purchase_order table:

    pending_approval -> approved -> placed -> in_transit -> delivered
                                         \\-> delayed ---/
    (any open status) -> cancelled

Orders come from two places:
- routine: the nightly reorder scan finds a site that will drop under its
  safety stock before a new delivery could arrive;
- shortage: a person approved the "order from a supplier" option of a
  shortage plan (see agent.py and approvals.py).
"""

import datetime as dt
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

OrderStatus = Literal[
    "pending_approval", "approved", "placed", "in_transit", "delayed", "delivered", "cancelled"
]
OPEN_STATUSES = {"approved", "placed", "in_transit", "delayed"}
FINAL_STATUSES = {"delivered", "cancelled"}

OrderDecisionKind = Literal[
    "place",            # send an approved order to the supplier (code, after a person approved it)
    "wait",             # nothing to do yet
    "chase",            # draft a reminder to the supplier
    "accept",           # record the supplier's confirmation or shipment
    "record_delay",     # record a later delivery date (and raise a supply event if it hurts)
    "switch_supplier",  # draft a replacement order with another supplier (needs approval)
    "escalate",         # hand to a person
    "close",            # delivered
]


class SupplierOffer(BaseModel):
    """One row of the supplier catalog: what one supplier sells for one drug."""

    supplier_id: str
    supplier_name: str
    din: str
    unit_cost: float = Field(ge=0)
    pack_size: int = Field(gt=0)
    min_order_qty: int = Field(gt=0)
    lead_time_days: int = Field(ge=0)
    availability: Literal["in_stock", "limited", "backorder"]
    on_time_rate: float = Field(ge=0, le=1)
    channel: str = "email"
    contact: str | None = None
    active: bool = True


class PurchaseOrder(BaseModel):
    """One order of one drug for one site from one supplier."""

    order_id: str
    kind: Literal["routine", "shortage"]
    din: str
    drug_name: str | None = None
    site_id: str
    supplier_id: str
    qty: int = Field(gt=0)
    unit_cost: float = Field(ge=0)
    status: OrderStatus = "pending_approval"
    created_at: datetime
    needed_by: dt.date | None = None       # day the site runs out without this order
    expected_date: dt.date | None = None   # supplier's current promised delivery day
    promised_date: dt.date | None = None   # first promised day (to measure delays)
    placed_at: datetime | None = None
    confirmed_qty: int = Field(default=0, ge=0)
    received_qty: int = Field(default=0, ge=0)
    approved_by: str | None = None
    approved_role: str | None = None
    plan_id: str | None = None
    event_id: str | None = None
    replaces: str | None = None         # order this one replaces after a supplier switch
    action_id: str | None = None        # plan_action this order carries out (shortage orders)
    reason: str = ""                    # why the order exists, in plain words
    chases: int = Field(default=0, ge=0)
    last_chased_at: datetime | None = None
    escalated_at: datetime | None = None  # handed to a person; agent waits for a new reply

    @property
    def total_cost(self) -> float:
        return round(self.qty * self.unit_cost, 2)


class SupplierReply(BaseModel):
    """A message a supplier sent about an order. Untrusted text."""

    order_id: str
    received_at: datetime
    sender: str
    text: str
    # Scripted reading used ONLY offline (no model available).
    offline_reading: dict | None = None


class SupplierReading(BaseModel):
    """What the reader agent extracted from a supplier reply."""

    kind: Literal[
        "confirmed",   # will ship the full quantity (date = promised delivery)
        "partial",     # will ship only qty (date = promised delivery for that part)
        "delayed",     # delivery moves to date
        "rejected",    # cannot supply at all
        "shipped",     # on its way (date = expected delivery)
        "delivered",   # arrived at the site (qty = units received)
        "question",    # supplier needs something from us
        "unrelated",
    ]
    qty: int | None = Field(default=None, ge=0)
    date: dt.date | None = None
    confidence: float = Field(ge=0, le=1)
    summary: str


class OrderDecision(BaseModel):
    """What the decision agent returns."""

    decision: OrderDecisionKind
    reason: str
    message_to: str | None = None
    message_draft: str | None = None


class OrderAction(BaseModel):
    """What our code records after each check of an order."""

    at: datetime
    order_id: str
    decision: OrderDecisionKind
    decided_by: Literal["rule", "agent", "fallback"]
    reason: str
    status_before: OrderStatus
    status_after: OrderStatus
    message_to: str | None = None
    message_draft: str | None = None
    facts: dict = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)
    new_order: dict | None = None       # replacement order drafted by switch_supplier
    raised_event: dict | None = None    # supply event for the shortage pipeline
    trace: list[str] = Field(default_factory=list)
    needs_human: bool
    # "simulated" when the order or a message went through the simulated supplier
    # channel. There is no real channel: nothing leaves this system.
    sent_via: Literal["simulated"] | None = None
