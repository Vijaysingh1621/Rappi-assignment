"""Mock supplier integration.

Suppliers are external systems: the catalogue the agent can read does not
reveal what they can actually ship today. Only their response to a submitted
purchase order does. The responses are deterministic (driven by the hidden
``live_available_units`` column) so tests and evals are reproducible, yet they
can still disagree with what the agent expected — which is exactly what the
post-execution verification loop has to handle.
"""
from app.planning import floor_to_pack


class SupplierAPI:
    def __init__(self, conn):
        self.conn = conn

    def _product(self, supplier_id: str, sku_id: str) -> dict | None:
        r = self.conn.execute(
            """SELECT sp.*, s.status FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
               WHERE sp.supplier_id=? AND sp.sku_id=?""", (supplier_id, sku_id)).fetchone()
        return dict(r) if r else None

    def _consume(self, supplier_id: str, sku_id: str, qty: int) -> None:
        self.conn.execute(
            "UPDATE supplier_products SET live_available_units = live_available_units - ? "
            "WHERE supplier_id=? AND sku_id=?", (qty, supplier_id, sku_id))

    def submit_order(self, supplier_id: str, sku_id: str, qty: int, delivery_day: int) -> dict:
        p = self._product(supplier_id, sku_id)
        if p is None or p["status"] != "active":
            return {"status": "rejected", "confirmed_qty": 0, "delivery_day": None,
                    "message": "Supplier cannot accept orders for this item."}
        if qty < p["moq"]:
            return {"status": "rejected", "confirmed_qty": 0, "delivery_day": None,
                    "message": f"Order below MOQ {p['moq']}."}
        can_ship = floor_to_pack(p["live_available_units"], p["case_pack"])
        confirmed = min(qty, can_ship)
        if confirmed <= 0:
            return {"status": "rejected", "confirmed_qty": 0, "delivery_day": None,
                    "message": "No stock available to allocate."}
        self._consume(supplier_id, sku_id, confirmed)
        day = max(delivery_day, p["lead_time_days"])
        if confirmed < qty:
            return {"status": "partially_confirmed", "confirmed_qty": confirmed, "delivery_day": day,
                    "message": f"Only {confirmed} of {qty} units available for allocation."}
        return {"status": "confirmed", "confirmed_qty": confirmed, "delivery_day": day,
                "message": "Order confirmed."}

    def modify_order(self, po: dict, new_qty: int) -> dict:
        p = self._product(po["supplier_id"], po["sku_id"])
        current = po["qty_confirmed"] if po["qty_confirmed"] is not None else po["qty_ordered"]
        if p is None or not p["accepts_modifications"] or po["shipping_status"] != "not_shipped":
            return {"status": "rejected", "confirmed_qty": current,
                    "message": "Order can no longer be modified."}
        extra = new_qty - current
        if extra <= 0:
            self._consume(po["supplier_id"], po["sku_id"], extra)  # releases allocation
            return {"status": "confirmed", "confirmed_qty": new_qty, "message": "Quantity reduced."}
        can_add = min(extra, floor_to_pack(p["live_available_units"], p["case_pack"]))
        self._consume(po["supplier_id"], po["sku_id"], can_add)
        total = current + can_add
        if can_add < extra:
            return {"status": "partially_confirmed", "confirmed_qty": total,
                    "message": f"Could only add {can_add} of the {extra} extra units."}
        return {"status": "confirmed", "confirmed_qty": total, "message": "Modification confirmed."}
