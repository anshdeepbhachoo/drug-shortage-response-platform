
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
    """One line of an order option: units for one site from one supplier.
    Built in code from the supplier catalog (order_rules.py), never by a model."""

    supplier_id: str
    site_id: str
    units: int = Field(gt=0)
    unit_cost: float | None = Field(default=None, ge=0)


class PlanOption(BaseModel):
    type: Literal["transfer", "order", "substitute", "conserve", "escalate"]
    description: str
    moves: list[Move] = Field(default_factory=list)
    orders: list[OrderLine] = Field(default_factory=list)
    reasoning: str


class PlanSet(BaseModel):
    options: list[PlanOption] = Field(min_length=1, max_length=3)
    no_perfect_plan: bool
