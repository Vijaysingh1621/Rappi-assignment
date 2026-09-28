"""Post-execution verification: did the world end up where the agent expected?

After actions run, the verifier re-reads the ERP (never the agent's own
account of what happened) and checks:

1. Each PO was confirmed by the supplier for the quantity requested.
2. Each PO will arrive no later than planned.
3. The committed budget is still within limits.
4. Storage will not overflow when the confirmed POs land.
5. Projected stock covers demand for the planning cycle — or an escalation exists.

Any failed check is a *discrepancy* and is fed back to the agent for re-planning.
"""
from dataclasses import dataclass, field

from app.context import load_item_context
from app.planning import REVIEW_PERIOD_DAYS
from app.repository import Repository


@dataclass
class VerificationReport:
    checks: list[dict] = field(default_factory=list)
    residual_requirement: dict | None = None
    projection_summary: dict | None = None

    @property
    def discrepancies(self) -> list[dict]:
        return [c for c in self.checks if not c["passed"]]

    @property
    def ok(self) -> bool:
        return not self.discrepancies

    def add(self, code: str, passed: bool, message: str) -> None:
        self.checks.append({"code": code, "passed": passed, "message": message})

    def as_dict(self) -> dict:
        return {"ok": self.ok, "checks": self.checks, "discrepancies": self.discrepancies,
                "residual_requirement": self.residual_requirement,
                "projection_summary": self.projection_summary}


def verify_outcome(repo: Repository, case: dict, results: list[dict], daily_demand: float,
                   escalated: bool) -> VerificationReport:
    rep = VerificationReport()

    for r in results:
        if "po_id" not in r or r["action"]["type"] == "cancel_po":
            continue
        po = repo.get_po(r["po_id"])  # re-read from the system of record
        exp = r["expected"]
        got = po["qty_confirmed"] or 0
        rep.add("PO_CONFIRMED", po["status"] == "confirmed" and got >= exp["qty"],
                f"{po['id']} ({po['supplier_id']}): requested {exp['qty']}, supplier confirmed {got} "
                f"— status {po['status']}")
        if exp.get("delivery_day") is not None and po["status"] != "rejected":
            rep.add("PO_ON_TIME", po["arrival_day"] <= exp["delivery_day"],
                    f"{po['id']}: expected day {exp['delivery_day']}, supplier ETA day {po['arrival_day']}")

    ctx = load_item_context(repo, case["sku_id"], case["node_id"], daily_demand)
    budget = repo.get_budget(case["node_id"], ctx.sku["category"])
    rep.add("BUDGET_INTEGRITY", budget["committed"] <= budget["total"] + 1e-6,
            f"Committed ${budget['committed']:,.2f} of ${budget['total']:,.2f} budget")

    active = [s for s in ctx.suppliers if s["status"] == "active"]
    L = min((s["lead_time_days"] for s in active), default=0)
    horizon = max([L + REVIEW_PERIOD_DAYS] + [a.day + 1 for a in ctx.arrivals])
    proj = ctx.projection(horizon)
    rep.projection_summary = {k: v for k, v in proj.items() if k != "days"}
    rep.add("STORAGE_OK", proj["max_storage_overflow"] == 0,
            f"Max storage overflow {proj['max_storage_overflow']:.0f} units "
            f"(capacity {ctx.inventory['storage_capacity']})")

    # The residual requirement is computed with the fastest active supplier: if even that
    # does not leave a gap, the plan is sufficient.
    req = ctx.requirement(L)
    rep.residual_requirement = req
    covered = req["net_requirement"] == 0 and proj["first_stockout_day"] is None
    rep.add("COVERAGE", covered or escalated,
            "Inventory position covers lead time + review period + safety stock"
            if covered else
            (f"Residual requirement {req['net_requirement']} units / first stock-out day "
             f"{proj['first_stockout_day']} — " + ("escalated to buyer." if escalated else "not covered.")))
    return rep
