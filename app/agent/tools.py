"""The agent's tool surface.

Design choices
--------------
* Tools are **scoped to the case** (SKU x node). The agent cannot read or act on
  other items, so a confused model cannot place an order for the wrong product.
* Read tools and calculators only. There is deliberately no "create PO" tool:
  the agent *proposes* actions via ``submit_decision`` and the orchestrator
  validates, gates and executes them.
* Every call is logged to the run trace, which is also how the orchestrator
  checks that the agent gathered the required evidence before deciding.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from app.context import demand_signal, load_item_context
from app.planning import Arrival, evaluate_supplier
from app.repository import Repository

# Evidence the agent must have looked at before any decision is accepted.
REQUIRED_EVIDENCE = [
    "get_case_details",
    "get_item_profile",
    "get_demand_signal",
    "get_open_purchase_orders",
    "get_supplier_options",
    "get_budget",
]


class Trace:
    def __init__(self):
        self.steps: list[dict] = []

    def add(self, kind: str, title: str, data: Any = None, **extra) -> dict:
        step = {"n": len(self.steps) + 1, "kind": kind, "title": title, "data": data,
                "ts": round(time.time(), 3), **extra}
        self.steps.append(step)
        return step


class ToolBox:
    def __init__(self, repo: Repository, case: dict, trace: Trace):
        self.repo = repo
        self.case = case
        self.sku_id = case["sku_id"]
        self.node_id = case["node_id"]
        self.trace = trace
        self.called: set[str] = set()
        self._impl: dict[str, Callable[..., Any]] = {
            "get_case_details": self.get_case_details,
            "get_item_profile": self.get_item_profile,
            "get_demand_signal": self.get_demand_signal,
            "get_open_purchase_orders": self.get_open_purchase_orders,
            "get_supplier_options": self.get_supplier_options,
            "get_budget": self.get_budget,
            "project_inventory": self.project_inventory,
            "evaluate_supplier_options": self.evaluate_supplier_options,
        }

    # ------------------------------------------------------------------------------
    def call(self, name: str, **kwargs) -> Any:
        if name not in self._impl:
            raise KeyError(f"unknown tool {name}")
        try:
            out = self._impl[name](**kwargs)
            ok = True
        except Exception as e:  # tool errors are returned to the agent, not raised
            out, ok = {"error": f"{type(e).__name__}: {e}"}, False
        self.called.add(name)
        self.trace.add("tool_call", name, {"input": kwargs, "output": out}, ok=ok)
        return out

    def missing_evidence(self) -> list[str]:
        return [t for t in REQUIRED_EVIDENCE if t not in self.called]

    # --- read tools -------------------------------------------------------------------
    def get_case_details(self) -> dict:
        c = self.repo.get_case(self.case["id"])
        return {k: c.get(k) for k in ("id", "scenario", "title", "description", "sku_id", "node_id",
                                      "recommendation", "po_id", "event")}

    def get_item_profile(self) -> dict:
        sku = self.repo.get_sku(self.sku_id)
        inv = self.repo.get_inventory(self.sku_id, self.node_id)
        return {**sku, "node_id": self.node_id, "on_hand": inv["on_hand"],
                "storage_capacity": inv["storage_capacity"], "free_storage_now": inv["storage_capacity"] - inv["on_hand"],
                "last_cycle_count": inv["last_counted"], "days_since_cycle_count": inv["days_since_count"]}

    def get_demand_signal(self) -> dict:
        sig = demand_signal(self.repo, self.sku_id, self.node_id)
        sig["sales_28d"] = [s["units"] for s in self.repo.get_sales(self.sku_id, self.node_id, 28)]
        sig["promotions"] = self.repo.get_promotions(self.sku_id, self.node_id)
        return sig

    def get_open_purchase_orders(self) -> list[dict]:
        keep = ("id", "supplier_id", "qty_ordered", "qty_confirmed", "inbound_qty", "unit_cost", "status",
                "shipping_status", "expected_arrival", "arrival_day", "created_by")
        return [{k: po[k] for k in keep} for po in self.repo.get_open_pos(self.sku_id, self.node_id)]

    def get_supplier_options(self) -> list[dict]:
        return self.repo.get_supplier_options(self.sku_id)

    def get_budget(self) -> dict:
        sku = self.repo.get_sku(self.sku_id)
        return self.repo.get_budget(self.node_id, sku["category"])

    # --- calculators ------------------------------------------------------------------
    def project_inventory(self, daily_demand: float | None = None, horizon_days: int = 14,
                          additional_orders: list[dict] | None = None) -> dict:
        ctx = load_item_context(self.repo, self.sku_id, self.node_id, daily_demand)
        extra = [Arrival(int(o["day"]), int(o["qty"]), "proposed") for o in (additional_orders or [])]
        proj = ctx.projection(int(horizon_days), extra)
        proj["daily_demand"] = ctx.daily_demand
        proj["storage_capacity"] = ctx.inventory["storage_capacity"]
        return proj

    def evaluate_supplier_options(self, daily_demand: float | None = None, quantity: int | None = None,
                                  exclude_suppliers: list[str] | None = None) -> list[dict]:
        ctx = load_item_context(self.repo, self.sku_id, self.node_id, daily_demand)
        excl = set(exclude_suppliers or [])
        return [evaluate_supplier(ctx, s, quantity, "excluded by agent" if s["supplier_id"] in excl else None)
                for s in ctx.suppliers]


# --- JSON schemas exposed to the LLM -------------------------------------------------------
_NO_ARGS = {"type": "object", "properties": {}, "additionalProperties": False}

ACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string",
                 "enum": ["create_po", "modify_po", "cancel_po", "escalate", "request_cycle_count"]},
        "supplier_id": {"type": "string", "description": "create_po only"},
        "quantity": {"type": "integer", "description": "create_po: units to order. modify_po: NEW TOTAL quantity"},
        "po_id": {"type": "string", "description": "modify_po / cancel_po only"},
        "delivery_day": {"type": "integer",
                         "description": "create_po: requested delivery in days from today (>= lead time). "
                                        "Use for split / staggered deliveries."},
        "reason": {"type": "string"},
        "options": {"type": "array", "items": {"type": "string"},
                    "description": "escalate: concrete options for the human buyer"},
    },
    "required": ["type", "reason"],
}

TOOL_SPECS = [
    {"name": "get_case_details", "input_schema": _NO_ARGS,
     "description": "The purchasing situation: scenario type, system recommendation (if any) and triggering event "
                    "(e.g. supplier short-shipment message)."},
    {"name": "get_item_profile", "input_schema": _NO_ARGS,
     "description": "SKU master data and node inventory: on hand, storage (slot) capacity, shelf life, safety "
                    "stock policy in days, last cycle count date."},
    {"name": "get_demand_signal", "input_schema": _NO_ARGS,
     "description": "28 days of daily sales, the current forecast, promotions, and a statistical classification of "
                    "recent demand (stable / sustained_increase / transient_spike / possible_stockout_or_data_issue) "
                    "with a recommended planning rate."},
    {"name": "get_open_purchase_orders", "input_schema": _NO_ARGS,
     "description": "Open POs for this item: ordered vs confirmed quantity, status, shipping status, arrival day."},
    {"name": "get_supplier_options", "input_schema": _NO_ARGS,
     "description": "Supplier catalogue for this item: unit cost, MOQ, case pack, lead time, primary flag, "
                    "reliability, whether they accept PO modifications. Does NOT show live availability."},
    {"name": "get_budget", "input_schema": _NO_ARGS,
     "description": "Purchasing budget for this item's category at this node: total, committed, remaining."},
    {"name": "project_inventory",
     "description": "Day-by-day projection of on-hand stock (day 0 = today) with existing open POs and optional "
                    "additional hypothetical orders. Reports first stock-out day, lost units and storage overflow.",
     "input_schema": {"type": "object", "properties": {
         "daily_demand": {"type": "number", "description": "Planning rate; defaults to the demand signal's recommendation"},
         "horizon_days": {"type": "integer", "default": 14},
         "additional_orders": {"type": "array", "items": {"type": "object", "properties": {
             "qty": {"type": "integer"}, "day": {"type": "integer"}}, "required": ["qty", "day"]}}}}},
    {"name": "evaluate_supplier_options",
     "description": "For every supplier: net requirement (order-up-to: lead time + 7-day review + safety stock minus "
                    "inventory position), rounded order quantity, cost, premium vs primary, and constraint checks "
                    "(budget, storage at arrival incl. a split-delivery plan, shelf life, arrival vs stock-out).",
     "input_schema": {"type": "object", "properties": {
         "daily_demand": {"type": "number"},
         "quantity": {"type": "integer", "description": "Evaluate a specific quantity instead of the net requirement"},
         "exclude_suppliers": {"type": "array", "items": {"type": "string"}}}}},
    {"name": "submit_decision",
     "description": "Submit your final decision for validation and execution. The system validates it against "
                    "business rules; if it is rejected you will receive the violations and must revise. After "
                    "execution you will receive the verification result and may need to re-plan.",
     "input_schema": {"type": "object", "properties": {
         "verdict": {"type": "string", "enum": ["accept", "modify", "reject", "investigate", "escalate"]},
         "summary": {"type": "string", "description": "One-paragraph explanation for the buyer"},
         "key_factors": {"type": "array", "items": {"type": "string"}},
         "daily_demand_used": {"type": "number"},
         "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
         "actions": {"type": "array", "items": ACTION_SCHEMA}},
         "required": ["verdict", "summary", "key_factors", "actions"]}},
]
