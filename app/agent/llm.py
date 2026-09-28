"""Claude-powered purchasing agent (manual tool-use loop).

The model investigates with the same scoped read/calculator tools as the rules
agent and ends each round by calling ``submit_decision``. The orchestrator —
not the model — validates, gates, executes and verifies. Validation
violations and verification discrepancies come back to the model as the
*result of its submit_decision call*, so it revises within the same
conversation and keeps its full investigation context.
"""
from __future__ import annotations

import json

import anthropic
from pydantic import ValidationError

from app.agent.tools import REQUIRED_EVIDENCE, TOOL_SPECS, ToolBox
from app.config import get_settings
from app.models import Decision

SYSTEM_PROMPT = f"""You are an autonomous purchasing agent for a quick-commerce retailer operating dark stores.
You handle one purchasing case at a time for a single SKU at a single node. Your job is to investigate,
decide, and propose concrete actions. You do not execute anything yourself: you call `submit_decision`, and
the system validates your proposal against hard business rules, routes it for human approval if required,
executes it against the ERP and supplier systems, and verifies the outcome.

How to work
1. Investigate before deciding. Always call: {", ".join(REQUIRED_EVIDENCE)}. Decisions made without this
   evidence are rejected automatically.
2. Never assume the system recommendation or the forecast is correct. Recompute the requirement yourself
   with `evaluate_supplier_options` and `project_inventory`. Use these calculators for arithmetic instead of
   doing it in your head.
3. Read the demand signal carefully:
   - sustained_increase: plan with the recent rate, not the stale forecast.
   - transient_spike (ended promotion, one-off outlier): plan with the forecast; do not over-react.
   - possible_stockout_or_data_issue: the inventory/sales data cannot be trusted. Do not buy. Use verdict
     `investigate` with a `request_cycle_count` action (and escalate if customers are likely losing sales).
4. Respect every constraint: budget remaining, storage capacity at the moment each delivery lands, shelf
   life (never hold more than shelf-life days of demand), supplier MOQ and case pack (quantities must be
   exact case-pack multiples and >= MOQ), lead time, blocked suppliers, and the price-premium cap (never
   pay more than 25% above the primary supplier's unit cost).
5. Choose the right lever:
   - Storage too small for one delivery: split into several `create_po` actions with different
     `delivery_day` values (the evaluate tool proposes a split plan).
   - Budget too small: buy the largest affordable case-pack multiple AND add an `escalate` action for the
     remainder.
   - Demand increased and an open PO is not yet shipped with a supplier that accepts modifications:
     prefer `modify_po` (quantity = NEW TOTAL) over a new PO because it arrives sooner.
   - Supplier shortfall: count only the confirmed quantity; do not reorder from the supplier that just
     fell short; first check whether remaining stock is actually sufficient before buying elsewhere.
   - Stock-out that no feasible supply can prevent, or no supplier within policy: `escalate` with concrete
     options for the buyer.
6. Verdicts: `accept` = execute the recommendation/current plan exactly as-is (for a recommendation, a
   single create_po with the recommended supplier and quantity); `modify` = act with a different quantity,
   supplier, split or PO change; `reject` = buy nothing; `investigate` = data must be verified first;
   `escalate` = a human must decide.
7. Commitments above ${{limit:,.0f}} are routed to a human buyer for approval automatically — still
   propose the right action.

Feedback loop
- If `submit_decision` returns validation violations, fix every one and resubmit.
- If it returns verification discrepancies (e.g. a supplier confirmed less than ordered), the executed
  actions ARE committed. Re-query the tools (state has changed), then submit a follow-up decision that only
  covers the remaining gap. Do not re-order from a supplier that just failed to confirm.

Write the summary and key_factors for a busy human buyer: the decision, the numbers that drove it, the
constraints that bound it, and any residual risk."""


class LLMAgent:
    name = "llm"

    def __init__(self, client: anthropic.Anthropic | None = None, model: str | None = None,
                 max_turns: int = 30):
        settings = get_settings()
        self.client = client or anthropic.Anthropic()
        self.model = model or settings.agent_model
        self.system = SYSTEM_PROMPT.replace("{limit:,.0f}", f"{settings.auto_approve_max_po_value:,.0f}")
        self.max_turns = max_turns
        self.messages: list[dict] = []
        self._pending: list[dict] = []        # tool results owed to the model from the submit turn
        self._submit_id: str | None = None
        self.usage = {"input_tokens": 0, "output_tokens": 0}

    # ------------------------------------------------------------------------------------
    def decide(self, tools: ToolBox, feedback: dict | None = None) -> Decision:
        if not self.messages:
            case = tools.case
            text = (f"Case {case['id']} ({case['scenario']}): {case['title']}.\n{case['description']}\n"
                    "Investigate and submit your decision.")
            if feedback:  # resumed after human approval with a failed verification
                text += "\n\nContext from earlier in this run:\n" + json.dumps(feedback, default=str)
            self.messages.append({"role": "user", "content": text})
        elif self._submit_id:
            self.messages.append({"role": "user", "content": self._pending + [{
                "type": "tool_result", "tool_use_id": self._submit_id, "is_error": True,
                "content": json.dumps(feedback or {"message": "Please resubmit."}, default=str)}]})
            self._submit_id, self._pending = None, []

        for _ in range(self.max_turns):
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=TOOL_SPECS,
                thinking={"type": "adaptive"},
                messages=self.messages,
            )
            self._track_usage(resp)
            self.messages.append({"role": "assistant", "content": resp.content})
            for block in resp.content:
                if block.type == "text" and block.text.strip():
                    tools.trace.add("llm", "Agent reasoning", {"text": block.text})

            if resp.stop_reason == "refusal":
                raise RuntimeError("model declined the request")
            if resp.stop_reason == "max_tokens":
                raise RuntimeError("model response truncated (max_tokens)")

            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if not tool_uses:
                self.messages.append({"role": "user", "content":
                                      "Continue the investigation, then call submit_decision with your decision."})
                continue

            results, decision = [], None
            for tu in tool_uses:
                if tu.name == "submit_decision":
                    try:
                        decision = Decision(**tu.input)
                        self._submit_id = tu.id
                    except ValidationError as e:
                        results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                        "content": f"Invalid decision format: {e.errors()}"})
                    continue
                args = tu.input if isinstance(tu.input, dict) else {}
                try:
                    out = tools.call(tu.name, **args)
                    results.append({"type": "tool_result", "tool_use_id": tu.id,
                                    "content": json.dumps(out, default=str)})
                except KeyError as e:
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                    "content": str(e)})
                except TypeError as e:  # bad arguments from the model
                    results.append({"type": "tool_result", "tool_use_id": tu.id, "is_error": True,
                                    "content": f"Invalid arguments: {e}"})

            if decision is not None:
                self._pending = results
                tools.trace.add("llm_usage", "Token usage so far", dict(self.usage))
                return decision
            self.messages.append({"role": "user", "content": results})

        raise RuntimeError(f"agent did not submit a decision within {self.max_turns} turns")

    def _track_usage(self, resp) -> None:
        u = getattr(resp, "usage", None)
        if u is not None:
            self.usage["input_tokens"] += getattr(u, "input_tokens", 0) or 0
            self.usage["output_tokens"] += getattr(u, "output_tokens", 0) or 0
