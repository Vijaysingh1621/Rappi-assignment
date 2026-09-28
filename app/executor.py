"""Executes validated, policy-cleared actions against the ERP and suppliers.

Every action returns an ``expected`` vs ``actual`` record — the raw material
for post-execution verification.
"""
import uuid
from datetime import datetime, timedelta

from app.db import SIM_TODAY, dumps
from app.models import Action
from app.supplier_api import SupplierAPI


def _day(offset: int) -> str:
    return (SIM_TODAY + timedelta(days=offset)).isoformat()


class Executor:
    def __init__(self, conn, run_id: str, actor: str = "agent"):
        self.conn = conn
        self.run_id = run_id
        self.actor = actor
        self.supplier = SupplierAPI(conn)

    def execute(self, case: dict, actions: list[Action]) -> list[dict]:
        results = [self._execute_one(case, a) for a in actions]
        self.conn.commit()
        return results

    # ------------------------------------------------------------------------------
    def _execute_one(self, case: dict, a: Action) -> dict:
        handler = getattr(self, f"_{a.type}")
        return {"action": a.model_dump(), **handler(case, a)}

    def _next_po_id(self) -> str:
        n = self.conn.execute("SELECT COUNT(*) FROM purchase_orders WHERE created_by != 'system'").fetchone()[0]
        return f"PO-A{n + 1:04d}"

    def _commit_budget(self, node_id: str, sku_id: str, amount: float) -> None:
        cat = self.conn.execute("SELECT category FROM skus WHERE id=?", (sku_id,)).fetchone()[0]
        self.conn.execute(
            "UPDATE budgets SET committed = committed + ? WHERE node_id=? AND category=?",
            (round(amount, 2), node_id, cat))

    def _create_po(self, case: dict, a: Action) -> dict:
        sp = dict(self.conn.execute(
            "SELECT * FROM supplier_products WHERE supplier_id=? AND sku_id=?",
            (a.supplier_id, case["sku_id"])).fetchone())
        day = a.delivery_day if a.delivery_day is not None else sp["lead_time_days"]
        po_id = self._next_po_id()
        self.conn.execute(
            "INSERT INTO purchase_orders (id, sku_id, node_id, supplier_id, qty_ordered, qty_confirmed, unit_cost, "
            "status, shipping_status, order_day, expected_arrival, created_by, source_run_id, notes) "
            "VALUES (?,?,?,?,?,NULL,?,'submitted','not_shipped',?,?,?,?,?)",
            (po_id, case["sku_id"], case["node_id"], a.supplier_id, a.quantity, sp["unit_cost"],
             _day(0), _day(day), self.actor, self.run_id, a.reason))
        resp = self.supplier.submit_order(a.supplier_id, case["sku_id"], a.quantity, day)
        arrival = resp["delivery_day"] if resp["delivery_day"] is not None else day
        self.conn.execute(
            "UPDATE purchase_orders SET status=?, qty_confirmed=?, expected_arrival=? WHERE id=?",
            (resp["status"], resp["confirmed_qty"], _day(arrival), po_id))
        self._commit_budget(case["node_id"], case["sku_id"], resp["confirmed_qty"] * sp["unit_cost"])
        return {
            "po_id": po_id,
            "expected": {"qty": a.quantity, "delivery_day": day, "status": "confirmed"},
            "actual": {"qty": resp["confirmed_qty"], "delivery_day": resp["delivery_day"], "status": resp["status"]},
            "supplier_message": resp["message"],
        }

    def _modify_po(self, case: dict, a: Action) -> dict:
        po = dict(self.conn.execute("SELECT * FROM purchase_orders WHERE id=?", (a.po_id,)).fetchone())
        before = po["qty_confirmed"] if po["qty_confirmed"] is not None else po["qty_ordered"]
        resp = self.supplier.modify_order(po, a.quantity)
        if resp["status"] != "rejected":
            status = "confirmed" if resp["status"] == "confirmed" else "partially_confirmed"
            self.conn.execute(
                "UPDATE purchase_orders SET qty_ordered=?, qty_confirmed=?, status=?, "
                "notes=COALESCE(notes || ' | ', '') || ? WHERE id=?",
                (a.quantity, resp["confirmed_qty"], status, f"modified by {self.actor}: {a.reason}", a.po_id))
            self._commit_budget(case["node_id"], case["sku_id"], (resp["confirmed_qty"] - before) * po["unit_cost"])
        arrival = (datetime.fromisoformat(po["expected_arrival"]).date() - SIM_TODAY).days
        return {
            "po_id": a.po_id,
            "expected": {"qty": a.quantity, "delivery_day": arrival, "status": "confirmed"},
            "actual": {"qty": resp["confirmed_qty"], "delivery_day": arrival, "status": resp["status"]},
            "supplier_message": resp["message"],
        }

    def _cancel_po(self, case: dict, a: Action) -> dict:
        po = dict(self.conn.execute("SELECT * FROM purchase_orders WHERE id=?", (a.po_id,)).fetchone())
        qty = po["qty_confirmed"] if po["qty_confirmed"] is not None else po["qty_ordered"]
        self.conn.execute("UPDATE purchase_orders SET status='cancelled' WHERE id=?", (a.po_id,))
        self._commit_budget(case["node_id"], case["sku_id"], -qty * po["unit_cost"])
        return {"po_id": a.po_id, "expected": {"status": "cancelled"}, "actual": {"status": "cancelled"},
                "supplier_message": "Cancellation acknowledged."}

    def _record_escalation(self, case: dict, kind: str, a: Action) -> dict:
        esc_id = f"ESC-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO escalations (id, run_id, case_id, kind, summary, options, status, created_at) "
            "VALUES (?,?,?,?,?,?,'open',?)",
            (esc_id, self.run_id, case["id"], kind, a.reason, dumps(a.options), datetime.now().isoformat()))
        return {"escalation_id": esc_id, "expected": {"status": "open"}, "actual": {"status": "open"}}

    def _escalate(self, case: dict, a: Action) -> dict:
        return self._record_escalation(case, "escalation", a)

    def _request_cycle_count(self, case: dict, a: Action) -> dict:
        return self._record_escalation(case, "cycle_count", a)
