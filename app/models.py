"""Structured decision contract between the agent and the execution system.

Whatever produces the decision (LLM or deterministic rules) must emit this
shape. The agent never writes to the ERP directly; it proposes a Decision and
the orchestrator validates, gates, executes and verifies it.
"""
from typing import Literal

from pydantic import BaseModel, Field

Verdict = Literal["accept", "modify", "reject", "investigate", "escalate"]
ActionType = Literal["create_po", "modify_po", "cancel_po", "escalate", "request_cycle_count"]
PURCHASE_ACTIONS = ("create_po", "modify_po")


class Action(BaseModel):
    type: ActionType
    supplier_id: str | None = Field(None, description="create_po: supplier to order from")
    quantity: int | None = Field(None, description="create_po: units to order; modify_po: NEW total quantity")
    po_id: str | None = Field(None, description="modify_po / cancel_po: target purchase order")
    delivery_day: int | None = Field(
        None, description="create_po: requested delivery, days from today (defaults to supplier lead time)")
    reason: str = ""
    options: list[str] = Field(default_factory=list, description="escalate: options for the human buyer")


class Decision(BaseModel):
    verdict: Verdict
    summary: str
    key_factors: list[str] = Field(default_factory=list)
    daily_demand_used: float | None = None
    actions: list[Action] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"] = "medium"

    def purchase_actions(self) -> list[Action]:
        return [a for a in self.actions if a.type in PURCHASE_ACTIONS]

    def has(self, action_type: str) -> bool:
        return any(a.type == action_type for a in self.actions)
