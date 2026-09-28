"""Pre-execution validation of a proposed decision.

The validator is deterministic and independent of whoever produced the
decision. It recomputes the situation from the ERP (it does not trust numbers
the agent quotes) and checks the proposal against hard business rules. Any
violation is fed back to the agent, which must revise its proposal.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.context import demand_signal, load_item_context
from app.models import Decision
from app.planning import REVIEW_PERIOD_DAYS, Arrival, round_to_pack
from app.repository import Repository

MAX_SUPPLIER_PREMIUM = 0.25     # buyer policy: never pay >25% over the primary supplier's price
OVERBUY_TOLERANCE = 0.10        # allowed excess over target stock (beyond pack rounding)


@dataclass
class Check:
    code: str
    passed: bool
    message: str
    severity: str = "error"     # error (blocks execution) | warning (informational)

    def as_dict(self) -> dict:
        return {"code": self.code, "passed": self.passed, "message": self.message, "severity": self.severity}


@dataclass
class ValidationReport:
    checks: list[Check] = field(default_factory=list)
    projection: dict | None = None
    total_value: float = 0.0

    @property
    def violations(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "error"]

    @property
    def warnings(self) -> list[Check]:
        return [c for c in self.checks if not c.passed and c.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, code: str, passed: bool, message: str, severity: str = "error") -> None:
        self.checks.append(Check(code, passed, message, severity))

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "total_value": round(self.total_value, 2),
            "violations": [c.as_dict() for c in self.violations],
            "warnings": [c.as_dict() for c in self.warnings],
            "checks": [c.as_dict() for c in self.checks],
            "projection_summary": {k: v for k, v in (self.projection or {}).items() if k != "days"},
        }


def validate_decision(repo: Repository, case: dict, decision: Decision) -> ValidationReport:
    rep = ValidationReport()
    sku_id, node_id = case["sku_id"], case["node_id"]
    signal = demand_signal(repo, sku_id, node_id)
    ctx = load_item_context(repo, sku_id, node_id, signal["recommended_daily_rate"])
    rate = ctx.daily_demand
    new_arrivals: list[Arrival] = []
    removed_po_qty: dict[str, int] = {}
    lead_times: list[int] = []
    used: list[dict] = []           # supplier-products the plan orders from
    purchase_value = 0.0

    _check_verdict_consistency(rep, case, decision, repo)

    # --- data quality gate ----------------------------------------------------------
    if signal["signal"] == "possible_stockout_or_data_issue":
        rep.add("DATA_QUALITY", not decision.purchase_actions(),
                "Demand data is unreliable (" + signal["note"] + ") — resolve with a cycle count "
                "before committing purchase orders." if decision.purchase_actions()
                else "No purchase committed while demand data is unreliable.")

    # --- per-action checks ------------------------------------------------------------
    for i, a in enumerate(decision.actions):
        tag = f"action[{i}] {a.type}"
        if a.type == "create_po":
            sp = repo.get_supplier_product(a.supplier_id or "", sku_id)
            if sp is None:
                rep.add("SUPPLIER_VALID", False, f"{tag}: supplier {a.supplier_id} does not supply {sku_id}")
                continue
            rep.add("SUPPLIER_ACTIVE", sp["status"] == "active",
                    f"{tag}: supplier {a.supplier_id} status is {sp['status']}")
            q = a.quantity or 0
            rep.add("QTY_POSITIVE", q > 0, f"{tag}: quantity {q} must be positive")
            rep.add("CASE_PACK", q % sp["case_pack"] == 0,
                    f"{tag}: quantity {q} must be a multiple of case pack {sp['case_pack']}")
            rep.add("MOQ", q >= sp["moq"], f"{tag}: quantity {q} vs supplier MOQ {sp['moq']}")
            day = a.delivery_day if a.delivery_day is not None else sp["lead_time_days"]
            rep.add("LEAD_TIME", day >= sp["lead_time_days"],
                    f"{tag}: delivery day {day} vs supplier lead time {sp['lead_time_days']} days")
            prem = sp["unit_cost"] / ctx.primary_cost - 1 if ctx.primary_cost else 0
            rep.add("PRICE_PREMIUM", prem <= MAX_SUPPLIER_PREMIUM + 1e-9,
                    f"{tag}: {prem:.0%} premium over primary supplier (policy max {MAX_SUPPLIER_PREMIUM:.0%})")
            purchase_value += q * sp["unit_cost"]
            new_arrivals.append(Arrival(day, q, f"new:{a.supplier_id}"))
            lead_times.append(sp["lead_time_days"])
            used.append(sp)

        elif a.type in ("modify_po", "cancel_po"):
            po = repo.get_po(a.po_id or "")
            if po is None or po["sku_id"] != sku_id or po["node_id"] != node_id:
                rep.add("PO_VALID", False, f"{tag}: {a.po_id} is not an open PO for this item")
                continue
            sp = repo.get_supplier_product(po["supplier_id"], sku_id)
            rep.add("PO_OPEN", po["status"] in ("submitted", "confirmed", "partially_confirmed"),
                    f"{tag}: {a.po_id} status is {po['status']}")
            rep.add("PO_NOT_SHIPPED", po["shipping_status"] == "not_shipped",
                    f"{tag}: {a.po_id} is {po['shipping_status']} and can no longer be changed")
            if a.type == "modify_po":
                rep.add("SUPPLIER_ALLOWS_CHANGES", bool(sp["accepts_modifications"]),
                        f"{tag}: supplier {po['supplier_id']} does not accept PO modifications")
                q = a.quantity or 0
                rep.add("CASE_PACK", q % sp["case_pack"] == 0,
                        f"{tag}: new quantity {q} must be a multiple of case pack {sp['case_pack']}")
                rep.add("MOQ", q >= sp["moq"], f"{tag}: new quantity {q} vs MOQ {sp['moq']}")
                delta = q - po["inbound_qty"]
                purchase_value += max(0, delta) * po["unit_cost"]
                removed_po_qty[po["id"]] = po["inbound_qty"]
                new_arrivals.append(Arrival(po["arrival_day"], q, f"mod:{po['id']}"))
                lead_times.append(sp["lead_time_days"])
                used.append(sp)
            else:
                removed_po_qty[po["id"]] = po["inbound_qty"]

        elif a.type == "escalate":
            rep.add("ESCALATION_CONTENT", bool(a.reason.strip()),
                    f"{tag}: escalation must state the problem for the human buyer")

    rep.total_value = purchase_value

    # --- aggregate checks -------------------------------------------------------------
    budget = ctx.budget_remaining
    if purchase_value > 0:
        rep.add("BUDGET", purchase_value <= budget + 1e-6,
                f"Total new commitment ${purchase_value:,.2f} vs remaining budget ${budget:,.2f}")

    # projection of the world after the proposed actions
    base_arrivals = [x for x in ctx.arrivals if x.label not in removed_po_qty]
    L = max(lead_times) if lead_times else min((s["lead_time_days"] for s in ctx.suppliers), default=3)
    cycle = L + REVIEW_PERIOD_DAYS  # stock must last until the next review's order can land
    horizon = max([cycle] + [x.day + 1 for x in new_arrivals])
    after = _project(ctx, base_arrivals + new_arrivals, horizon)
    before = _project(ctx, ctx.arrivals, horizon)
    rep.projection = after

    if new_arrivals:
        rep.add("STORAGE", after["max_storage_overflow"] == 0,
                f"Projected storage overflow {after['max_storage_overflow']:.0f} units "
                f"(slot capacity {ctx.inventory['storage_capacity']})")
        shelf_limit = rate * ctx.sku["shelf_life_days"]
        peak_after = max(d["on_hand_after_receipt"] for d in after["days"])
        peak_before = max(d["on_hand_after_receipt"] for d in before["days"])
        rep.add("SHELF_LIFE", peak_after <= max(shelf_limit, peak_before) + 1e-6,
                f"Peak stock {peak_after:.0f} vs shelf-life cover {shelf_limit:.0f} units "
                f"({ctx.sku['shelf_life_days']} days × {rate}/day)")

        # over-buying: stock left at the end of the cycle should be ~safety stock.
        # Slack: tolerance on target + the minimum order lot of the suppliers used.
        req = ctx.requirement(L)
        lot = max((round_to_pack(1, sp["case_pack"], sp["moq"]) for sp in used), default=0)
        allowed = req["safety_stock"] + OVERBUY_TOLERANCE * req["target_stock"] + lot
        end_stock = _project(ctx, base_arrivals + new_arrivals, cycle)["end_stock"]
        rep.add("NO_OVERBUY", end_stock <= allowed,
                f"Stock left after lead time + review period: {end_stock:.0f} units "
                f"(target ≈ safety stock {req['safety_stock']:.0f}, allowed ≤ {allowed:.0f})")

    # coverage: after the earliest date any new supply could land, do we still stock out?
    earliest = min([x.day for x in new_arrivals] or [min((s["lead_time_days"] for s in ctx.suppliers
                                                          if s["status"] == "active"), default=0)])
    residual = sum(d["lost"] for d in after["days"] if earliest <= d["day"] < cycle)
    pre_arrival = sum(d["lost"] for d in after["days"] if d["day"] < earliest)
    unresolved = residual > 0.5 or pre_arrival > 0.5
    if decision.verdict not in ("investigate",):
        rep.add("COVERAGE", not unresolved or decision.has("escalate"),
                f"Projected lost sales after plan: {pre_arrival:.0f} units before new supply can land, "
                f"{residual:.0f} units after — "
                + ("escalated to buyer." if decision.has("escalate") else
                   "residual stock-out risk must be escalated to a human buyer.")
                if unresolved else "No projected stock-out over the planning horizon.")

    return rep


def _project(ctx, arrivals, horizon):
    from app.planning import project_inventory
    return project_inventory(ctx.inventory["on_hand"], ctx.daily_demand, arrivals, horizon,
                             ctx.inventory["storage_capacity"])


def _check_verdict_consistency(rep: ValidationReport, case: dict, d: Decision, repo: Repository) -> None:
    purchases = d.purchase_actions()
    if d.verdict == "reject":
        rep.add("VERDICT_CONSISTENT", not purchases, "Verdict 'reject' must not create or increase orders")
    elif d.verdict == "investigate":
        rep.add("VERDICT_CONSISTENT", d.has("request_cycle_count") or d.has("escalate"),
                "Verdict 'investigate' must request a cycle count or escalate")
    elif d.verdict == "escalate":
        rep.add("VERDICT_CONSISTENT", d.has("escalate"), "Verdict 'escalate' must include an escalate action")
    elif d.verdict == "modify":
        rep.add("VERDICT_CONSISTENT", bool(d.actions), "Verdict 'modify' must include at least one action")
    elif d.verdict == "accept" and case.get("recommendation_id"):
        rec = repo.get_recommendation(case["recommendation_id"])
        matches = (len(purchases) == 1 and purchases[0].type == "create_po"
                   and purchases[0].quantity == rec["quantity"] and purchases[0].supplier_id == rec["supplier_id"])
        rep.add("VERDICT_CONSISTENT", matches,
                f"Verdict 'accept' must execute the recommendation as-is ({rec['quantity']} units from "
                f"{rec['supplier_id']}); otherwise use 'modify'")
