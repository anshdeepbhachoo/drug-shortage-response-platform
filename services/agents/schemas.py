
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SupplyEvent(BaseModel):
    """Information extracted from a supplier notice."""

    drug_din: str | None = None
    drug_name: str | None = None
    delay_weeks: float | None = Field(default=None, gt=0)
    confidence: float = Field(ge=0, le=1)
    next_action: Literal["plan", "needs_review", "ignore"]
    reason: str

    @model_validator(mode="after")
    def validate_plan(self):
        if self.next_action == "plan":
            if not self.drug_din:
                raise ValueError("A plan requires a drug DIN.")
            if self.delay_weeks is None:
                raise ValueError("A plan requires a reported delay.")
        return self


class Move(BaseModel):
    from_site: str
    to_site: str
    units: int = Field(gt=0)


class OrderLine(BaseModel):
    """One site's order from one supplier. Worked out in code (services/agents/orders.py), never by a model."""

    site_id: str
    supplier_id: str
    supplier: str
    units: int = Field(gt=0)
    expected_date: str  # ISO date: today plus the supplier's lead time


class PlanOption(BaseModel):
    # transfer_order: a transfer that bridges the hospitals until an order arrives (orders.combined_option).
    type: Literal["transfer", "order", "transfer_order", "substitute", "conserve", "escalate"]
    description: str
    moves: list[Move] = Field(default_factory=list)
    order_lines: list[OrderLine] = Field(default_factory=list)
    reasoning: str


class PlanSet(BaseModel):
    # The planner returns up to three options; code may add an order option and a combined transfer and
    # order option (orders.with_order_option).
    options: list[PlanOption] = Field(min_length=1, max_length=5)
    no_perfect_plan: bool
