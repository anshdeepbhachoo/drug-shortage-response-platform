from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


class SupplyEvent(BaseModel):
    drug_din: Optional[str] = None
    drug_name: Optional[str] = None
    delay_weeks: Optional[float] = None
    sites_at_risk: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    next_action: Literal["plan", "needs_review", "ignore"]
    reason: str

    @model_validator(mode="after")
    def plan_needs_drug_and_sites(self):
        if self.next_action == "plan":
            if not self.drug_din:
                raise ValueError("next_action=plan requires drug_din")
            if not self.sites_at_risk:
                raise ValueError("next_action=plan requires sites_at_risk")
        return self


class Move(BaseModel):
    from_site: str
    to_site: str
    units: int = Field(gt=0)


class PlanOption(BaseModel):
    type: Literal["transfer", "substitute", "conserve", "escalate"]
    description: str
    moves: list[Move] = Field(default_factory=list)
    reasoning: str


class PlanSet(BaseModel):
    options: list[PlanOption] = Field(min_length=1, max_length=3)
    no_perfect_plan: bool