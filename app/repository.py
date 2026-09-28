"""Read access to the mock ERP. Every agent read tool goes through here."""
import json
import sqlite3
from datetime import date

from app.db import SIM_TODAY, row, rows

OPEN_PO_STATUSES = ("submitted", "confirmed", "partially_confirmed")


def day_offset(iso_day: str) -> int:
    return (date.fromisoformat(iso_day) - SIM_TODAY).days


class Repository:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- cases & recommendations -------------------------------------------------
    def list_cases(self) -> list[dict]:
        out = rows(self.conn.execute("SELECT * FROM cases ORDER BY id"))
        for c in out:
            c["event"] = json.loads(c["event"]) if c["event"] else None
        return out

    def get_case(self, case_id: str) -> dict | None:
        c = row(self.conn.execute("SELECT * FROM cases WHERE id=?", (case_id,)))
        if c:
            c["event"] = json.loads(c["event"]) if c["event"] else None
            if c["recommendation_id"]:
                c["recommendation"] = self.get_recommendation(c["recommendation_id"])
        return c

    def get_recommendation(self, rec_id: str) -> dict | None:
        return row(self.conn.execute("SELECT * FROM recommendations WHERE id=?", (rec_id,)))

    # --- product / inventory -----------------------------------------------------
    def get_sku(self, sku_id: str) -> dict | None:
        return row(self.conn.execute("SELECT * FROM skus WHERE id=?", (sku_id,)))

    def get_inventory(self, sku_id: str, node_id: str) -> dict | None:
        inv = row(self.conn.execute(
            "SELECT * FROM inventory WHERE sku_id=? AND node_id=?", (sku_id, node_id)))
        if inv:
            inv["days_since_count"] = -day_offset(inv["last_counted"])
        return inv

    # --- demand --------------------------------------------------------------------
    def get_forecast(self, sku_id: str, node_id: str) -> dict | None:
        return row(self.conn.execute(
            "SELECT * FROM forecasts WHERE sku_id=? AND node_id=?", (sku_id, node_id)))

    def get_sales(self, sku_id: str, node_id: str, days: int = 28) -> list[dict]:
        out = rows(self.conn.execute(
            "SELECT day, units FROM sales_history WHERE sku_id=? AND node_id=? ORDER BY day DESC LIMIT ?",
            (sku_id, node_id, days)))
        return list(reversed(out))

    def get_promotions(self, sku_id: str, node_id: str) -> list[dict]:
        return rows(self.conn.execute(
            "SELECT * FROM promotions WHERE sku_id=? AND node_id=? ORDER BY start_day", (sku_id, node_id)))

    # --- supply ---------------------------------------------------------------------
    def get_open_pos(self, sku_id: str, node_id: str) -> list[dict]:
        q = f"SELECT * FROM purchase_orders WHERE sku_id=? AND node_id=? AND status IN {OPEN_PO_STATUSES} ORDER BY expected_arrival"
        out = rows(self.conn.execute(q, (sku_id, node_id)))
        for po in out:
            po["arrival_day"] = day_offset(po["expected_arrival"])
            po["inbound_qty"] = inbound_qty(po)
        return out

    def get_po(self, po_id: str) -> dict | None:
        po = row(self.conn.execute("SELECT * FROM purchase_orders WHERE id=?", (po_id,)))
        if po:
            po["arrival_day"] = day_offset(po["expected_arrival"])
            po["inbound_qty"] = inbound_qty(po)
        return po

    def get_supplier_options(self, sku_id: str) -> list[dict]:
        """Supplier catalogue for a SKU. Deliberately excludes live availability,
        which only the supplier knows (revealed on PO submission)."""
        return rows(self.conn.execute(
            """SELECT s.id AS supplier_id, s.name, s.status, s.on_time_rate, s.fill_rate,
                      sp.unit_cost, sp.moq, sp.case_pack, sp.lead_time_days, sp.is_primary,
                      sp.accepts_modifications
               FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
               WHERE sp.sku_id=? ORDER BY sp.is_primary DESC, sp.unit_cost""",
            (sku_id,)))

    def get_supplier_product(self, supplier_id: str, sku_id: str) -> dict | None:
        return row(self.conn.execute(
            """SELECT s.id AS supplier_id, s.name, s.status, sp.*
               FROM supplier_products sp JOIN suppliers s ON s.id = sp.supplier_id
               WHERE sp.supplier_id=? AND sp.sku_id=?""",
            (supplier_id, sku_id)))

    # --- finance -----------------------------------------------------------------------
    def get_budget(self, node_id: str, category: str) -> dict | None:
        b = row(self.conn.execute(
            "SELECT * FROM budgets WHERE node_id=? AND category=? ORDER BY period DESC LIMIT 1",
            (node_id, category)))
        if b:
            b["remaining"] = round(b["total"] - b["committed"], 2)
        return b

    # --- agent audit ---------------------------------------------------------------------
    def list_runs(self, case_id: str | None = None) -> list[dict]:
        sql = "SELECT id, case_id, mode, status, started_at, finished_at, decision FROM agent_runs"
        args: tuple = ()
        if case_id:
            sql += " WHERE case_id=?"
            args = (case_id,)
        out = rows(self.conn.execute(sql + " ORDER BY started_at DESC", args))
        for r in out:
            r["decision"] = json.loads(r["decision"]) if r["decision"] else None
        return out

    def get_run(self, run_id: str) -> dict | None:
        r = row(self.conn.execute("SELECT * FROM agent_runs WHERE id=?", (run_id,)))
        if r:
            r["decision"] = json.loads(r["decision"]) if r["decision"] else None
            r["trace"] = json.loads(r["trace"]) if r["trace"] else []
        return r

    def list_approvals(self, status: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM approvals", ()
        if status:
            sql, args = sql + " WHERE status=?", (status,)
        out = rows(self.conn.execute(sql + " ORDER BY id DESC", args))
        for a in out:
            a["actions"] = json.loads(a["actions"])
            a["reasons"] = json.loads(a["reasons"])
        return out

    def get_approval(self, approval_id: str) -> dict | None:
        a = row(self.conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)))
        if a:
            a["actions"] = json.loads(a["actions"])
            a["reasons"] = json.loads(a["reasons"])
        return a

    def list_escalations(self) -> list[dict]:
        out = rows(self.conn.execute("SELECT * FROM escalations ORDER BY created_at DESC"))
        for e in out:
            e["options"] = json.loads(e["options"]) if e["options"] else []
        return out

    def list_pos(self) -> list[dict]:
        return rows(self.conn.execute("SELECT * FROM purchase_orders ORDER BY id"))


def inbound_qty(po: dict) -> int:
    """Quantity we can count on from an open PO: the confirmed quantity once the
    supplier has responded, otherwise the ordered quantity."""
    if po["status"] not in OPEN_PO_STATUSES:
        return 0
    return po["qty_confirmed"] if po["qty_confirmed"] is not None else po["qty_ordered"]
