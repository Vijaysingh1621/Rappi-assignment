"""Mock data: a small quick-commerce network with 13 purchasing cases.

Each case owns its own SKU so cases do not interfere with one another. The
numbers are deliberately chosen so that each case has a *defensible* right
answer that differs from the naive one (e.g. the system recommendation is
wrong, the demand spike is a promo artefact, the supplier's catalogue
availability is stale). See evals/cases.py for the expected outcomes.
"""
import json
import random
from datetime import timedelta

from app.db import SIM_TODAY

TODAY = SIM_TODAY


def d(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


NODES = [
    ("BOG-01", "Bogotá Chapinero dark store", "Bogotá"),
    ("MEX-02", "CDMX Roma dark store", "Ciudad de México"),
    ("SAO-03", "São Paulo Pinheiros dark store", "São Paulo"),
]

SUPPLIERS = [
    # id, name, status, on_time_rate, fill_rate
    ("SUP-A", "Andina Bebidas", "active", 0.96, 0.98),
    ("SUP-B", "Distribuidora Central", "active", 0.91, 0.95),
    ("SUP-C", "Aguas del Valle", "active", 0.97, 0.99),
    ("SUP-D", "Snack Brasil", "active", 0.88, 0.90),
    ("SUP-E", "Nestlé Distribución MX", "active", 0.93, 0.85),
    ("SUP-F", "Abarrotes Mayoreo", "active", 0.90, 0.96),
    ("SUP-G", "Club Proveedor", "active", 0.86, 0.93),
    ("SUP-H", "Kimberly Clark BR", "active", 0.92, 0.80),
    ("SUP-I", "Baby Express", "active", 0.94, 0.97),
    ("SUP-J", "Mayorista Infantil", "active", 0.89, 0.95),
    ("SUP-K", "Importadora Gourmet", "active", 0.80, 0.90),
    ("SUP-L", "Electrolit MX", "active", 0.95, 0.97),
    ("SUP-M", "Azeites do Sul", "active", 0.93, 0.96),
    ("SUP-N", "P&G Colombia", "active", 0.95, 0.97),
    ("SUP-O", "Droguería Mayorista", "active", 0.90, 0.95),
    ("SUP-P", "Alpina Directo", "active", 0.97, 0.99),
    ("SUP-Q", "Lácteos Express", "active", 0.92, 0.96),
    ("SUP-R", "Familia Institucional", "active", 0.94, 0.97),
    ("SUP-S", "Bavaria Distribución", "active", 0.96, 0.98),
    ("SUP-T", "Arroces del Llano", "active", 0.93, 0.97),
    ("SUP-X", "Distribuidora Mercurio", "blocked", 0.70, 0.75),
]

# sku_id, name, category, shelf_life_days, safety_stock_days
SKUS = [
    ("SKU-1001", "Coca-Cola Original 350ml can", "beverages", 180, 3),
    ("SKU-2001", "Agua Cristal 600ml", "beverages", 365, 3),
    ("SKU-3001", "Arroz Diana 1kg", "pantry", 365, 4),
    ("SKU-4001", "Leche Alquería entera 1L", "dairy", 12, 2),
    ("SKU-5001", "Doritos Nacho 150g", "snacks", 120, 3),
    ("SKU-6001", "Nescafé Tradición 170g", "pantry", 365, 3),
    ("SKU-6002", "Pañales Huggies Etapa 3 x50", "baby", 730, 3),
    ("SKU-6003", "Aceite de Oliva Borges 500ml", "pantry", 540, 3),
    ("SKU-7001", "Electrolit Suero 625ml", "beverages", 365, 3),
    ("SKU-7002", "Cerveza Club Colombia 330ml", "beverages", 180, 3),
    ("SKU-7003", "Azeite Extra Virgem Gallo 1L", "pantry", 540, 3),
    ("SKU-8001", "Shampoo Head & Shoulders 375ml", "personal_care", 730, 3),
    ("SKU-8002", "Papel Higiénico Familia x12", "household", 730, 2),
    ("SKU-8003", "Yogurt Griego Alpina 500g", "dairy", 21, 2),
]

# sku, node, on_hand, storage_capacity, last_counted_offset
INVENTORY = [
    ("SKU-1001", "BOG-01", 150, 900, -3),
    ("SKU-2001", "MEX-02", 280, 1500, -2),
    ("SKU-3001", "BOG-01", 700, 1000, -4),
    ("SKU-4001", "BOG-01", 480, 700, -21),
    ("SKU-5001", "SAO-03", 600, 1200, -5),
    ("SKU-6001", "MEX-02", 120, 800, -2),
    ("SKU-6002", "SAO-03", 200, 900, -3),
    ("SKU-6003", "BOG-01", 60, 600, -1),
    ("SKU-7001", "MEX-02", 420, 2000, -2),
    ("SKU-7002", "BOG-01", 300, 1200, -2),
    ("SKU-7003", "SAO-03", 300, 1400, -3),
    ("SKU-8001", "BOG-01", 100, 900, -4),
    ("SKU-8002", "MEX-02", 100, 200, -1),
    ("SKU-8003", "BOG-01", 20, 150, -2),
]

# sku, node, forecast daily units
FORECASTS = [
    ("SKU-1001", "BOG-01", 40),
    ("SKU-2001", "MEX-02", 90),
    ("SKU-3001", "BOG-01", 20),
    ("SKU-4001", "BOG-01", 60),
    ("SKU-5001", "SAO-03", 25),
    ("SKU-6001", "MEX-02", 35),
    ("SKU-6002", "SAO-03", 40),
    ("SKU-6003", "BOG-01", 30),
    ("SKU-7001", "MEX-02", 50),
    ("SKU-7002", "BOG-01", 30),
    ("SKU-7003", "SAO-03", 40),
    ("SKU-8001", "BOG-01", 45),
    ("SKU-8002", "MEX-02", 30),
    ("SKU-8003", "BOG-01", 8),
]

# supplier, sku, unit_cost, moq, case_pack, lead_time, primary, accepts_mods, live_available
SUPPLIER_PRODUCTS = [
    ("SUP-A", "SKU-1001", 0.55, 96, 24, 4, 1, 1, 5000),
    ("SUP-B", "SKU-1001", 0.62, 48, 24, 2, 0, 1, 2000),
    ("SUP-C", "SKU-2001", 0.30, 200, 20, 5, 1, 1, 10000),
    ("SUP-T", "SKU-3001", 1.20, 100, 20, 3, 1, 1, 5000),
    ("SUP-B", "SKU-4001", 1.05, 60, 12, 2, 1, 1, 3000),
    ("SUP-D", "SKU-5001", 2.10, 120, 12, 3, 1, 1, 0),
    ("SUP-E", "SKU-6001", 3.80, 120, 12, 6, 1, 1, 0),
    ("SUP-F", "SKU-6001", 4.10, 48, 12, 2, 0, 1, 1000),
    ("SUP-G", "SKU-6001", 4.40, 24, 12, 3, 0, 1, 1000),
    ("SUP-X", "SKU-6001", 3.20, 12, 12, 1, 0, 1, 5000),   # cheapest, but blocked
    ("SUP-H", "SKU-6002", 14.00, 100, 10, 7, 1, 1, 0),
    ("SUP-I", "SKU-6002", 15.20, 50, 10, 2, 0, 1, 120),   # catalogue looks fine; can only ship 120
    ("SUP-J", "SKU-6002", 16.10, 20, 10, 4, 0, 1, 500),
    ("SUP-B", "SKU-6003", 6.00, 60, 12, 5, 1, 1, 0),
    ("SUP-K", "SKU-6003", 8.70, 24, 12, 9, 0, 1, 1000),   # +45% premium and slow
    ("SUP-L", "SKU-7001", 1.10, 96, 24, 5, 1, 1, 1000),
    ("SUP-S", "SKU-7002", 0.95, 120, 24, 4, 1, 1, 3000),
    ("SUP-M", "SKU-7003", 15.00, 50, 10, 4, 1, 0, 2000),
    ("SUP-N", "SKU-8001", 4.20, 60, 12, 4, 1, 1, 3000),
    ("SUP-O", "SKU-8001", 4.60, 24, 12, 3, 0, 1, 1000),
    ("SUP-R", "SKU-8002", 6.50, 50, 10, 3, 1, 1, 2000),
    ("SUP-P", "SKU-8003", 2.10, 480, 24, 2, 1, 1, 5000),
    ("SUP-Q", "SKU-8003", 2.45, 36, 12, 1, 0, 1, 500),
]

PERIOD = TODAY.strftime("%Y-%m")

# node, category, total, committed
BUDGETS = [
    ("BOG-01", "beverages", 20000, 6000),
    ("BOG-01", "pantry", 15000, 4000),
    ("BOG-01", "dairy", 8000, 2500),
    ("BOG-01", "personal_care", 9000, 7700),   # only $1,300 left this month
    ("MEX-02", "beverages", 18000, 5000),
    ("MEX-02", "pantry", 12000, 7000),
    ("MEX-02", "household", 10000, 3000),
    ("SAO-03", "snacks", 9000, 3000),
    ("SAO-03", "baby", 14000, 6000),
    ("SAO-03", "pantry", 25000, 8000),
]

# id, sku, node, supplier, ordered, confirmed, status, shipping, order_offset, arrival_offset
PURCHASE_ORDERS = [
    ("PO-1001", "SKU-1001", "BOG-01", "SUP-A", 200, 200, "confirmed", "in_transit", -1, 3),
    ("PO-2001", "SKU-2001", "MEX-02", "SUP-C", 270, 270, "confirmed", "in_transit", -3, 2),
    ("PO-5001", "SKU-5001", "SAO-03", "SUP-D", 500, 250, "partially_confirmed", "not_shipped", 0, 3),
    ("PO-6001", "SKU-6001", "MEX-02", "SUP-E", 500, 250, "partially_confirmed", "not_shipped", -2, 4),
    ("PO-6002", "SKU-6002", "SAO-03", "SUP-H", 500, 250, "partially_confirmed", "not_shipped", -3, 4),
    ("PO-6003", "SKU-6003", "BOG-01", "SUP-B", 500, 250, "partially_confirmed", "not_shipped", 0, 5),
    ("PO-7001", "SKU-7001", "MEX-02", "SUP-L", 600, 600, "confirmed", "not_shipped", -2, 3),
    ("PO-7002", "SKU-7002", "BOG-01", "SUP-S", 300, 300, "confirmed", "not_shipped", 0, 4),
    ("PO-7003", "SKU-7003", "SAO-03", "SUP-M", 400, 400, "confirmed", "in_transit", -2, 2),
]

UNIT_COST = {(sp[0], sp[1]): sp[2] for sp in SUPPLIER_PRODUCTS}

RECOMMENDATIONS = [
    ("REC-1001", "SKU-1001", "BOG-01", "SUP-A", 800,
     "Min/max rule: order up to slot capacity (900) minus on-hand (150), rounded to 800."),
    ("REC-2001", "SKU-2001", "MEX-02", "SUP-C", 800,
     "Order-up-to policy: 12 days of forecast + 3 days safety stock minus inventory position."),
    ("REC-3001", "SKU-3001", "BOG-01", "SUP-T", 800,
     "Seasonal uplift model (Q4 pantry stock-up) applied to last year's peak."),
    ("REC-4001", "SKU-4001", "BOG-01", "SUP-B", 800,
     "Forecast-driven replenishment: 60 units/day forecast, bulk order for freight efficiency."),
    ("REC-8001", "SKU-8001", "BOG-01", "SUP-N", 540,
     "Order-up-to policy: 11 days of forecast + 3 days safety stock minus inventory position."),
    ("REC-8002", "SKU-8002", "MEX-02", "SUP-R", 260,
     "Order-up-to policy: 10 days of forecast + 2 days safety stock minus inventory position."),
    ("REC-8003", "SKU-8003", "BOG-01", "SUP-P", 480,
     "Net requirement 68 units, raised to primary supplier MOQ (480)."),
]

PROMOTIONS = [
    ("PROMO-7002", "SKU-7002", "BOG-01", d(-5), d(-3),
     "3x2 weekend promo tied to Colombia national team match (ended)"),
]

CASES = [
    # --- Scenario 1: purchase recommendation review ---
    ("CASE-S1-A", "recommendation_review", "Review 800-unit Coca-Cola recommendation",
     "The replenishment system recommends buying 800 units of Coca-Cola 350ml for BOG-01. "
     "Decide whether to accept, modify, reject or investigate.", "SKU-1001", "BOG-01", "REC-1001", None, None),
    ("CASE-S1-B", "recommendation_review", "Review 800-unit Agua Cristal recommendation",
     "The replenishment system recommends buying 800 units of Agua Cristal 600ml for MEX-02.",
     "SKU-2001", "MEX-02", "REC-2001", None, None),
    ("CASE-S1-C", "recommendation_review", "Review 800-unit Arroz Diana recommendation",
     "The replenishment system recommends buying 800 units of Arroz Diana 1kg for BOG-01.",
     "SKU-3001", "BOG-01", "REC-3001", None, None),
    ("CASE-S1-D", "recommendation_review", "Review 800-unit milk recommendation",
     "The replenishment system recommends buying 800 units of Leche Alquería 1L for BOG-01.",
     "SKU-4001", "BOG-01", "REC-4001", None, None),
    # --- Scenario 2: supplier cannot fulfil ---
    ("CASE-S2-A", "supplier_shortfall", "Snack Brasil can only ship 250 of 500 Doritos",
     "Supplier SUP-D reports it can only supply 250 of the 500 units on PO-5001.",
     "SKU-5001", "SAO-03", None, "PO-5001",
     {"type": "supplier_shortfall", "po_id": "PO-5001", "ordered": 500, "confirmed": 250,
      "message": "Due to a production line stoppage we can only ship 250 units this cycle."}),
    ("CASE-S2-B", "supplier_shortfall", "Nestlé can only ship 250 of 500 Nescafé",
     "Supplier SUP-E reports it can only supply 250 of the 500 units on PO-6001.",
     "SKU-6001", "MEX-02", None, "PO-6001",
     {"type": "supplier_shortfall", "po_id": "PO-6001", "ordered": 500, "confirmed": 250,
      "message": "Allocation cut: we can confirm 250 units. No further stock expected for 3 weeks."}),
    ("CASE-S2-C", "supplier_shortfall", "Kimberly Clark can only ship 250 of 500 diapers",
     "Supplier SUP-H reports it can only supply 250 of the 500 units on PO-6002.",
     "SKU-6002", "SAO-03", None, "PO-6002",
     {"type": "supplier_shortfall", "po_id": "PO-6002", "ordered": 500, "confirmed": 250,
      "message": "Regional shortage; 250 units confirmed, remainder cancelled."}),
    ("CASE-S2-D", "supplier_shortfall", "Olive oil supplier can only ship 250 of 500",
     "Supplier SUP-B reports it can only supply 250 of the 500 units on PO-6003.",
     "SKU-6003", "BOG-01", None, "PO-6003",
     {"type": "supplier_shortfall", "po_id": "PO-6003", "ordered": 500, "confirmed": 250,
      "message": "Import container delayed; 250 units confirmed."}),
    # --- Scenario 3: demand / forecast changed ---
    ("CASE-S3-A", "demand_change", "Electrolit sales running far above forecast",
     "Electrolit sales at MEX-02 have increased significantly versus the 50/day forecast. "
     "PO-7001 (600 units) is open. Check whether the purchasing plan still covers demand.",
     "SKU-7001", "MEX-02", None, "PO-7001", {"type": "sales_alert", "forecast_daily": 50}),
    ("CASE-S3-B", "demand_change", "Club Colombia sales spiked last week",
     "Club Colombia sales at BOG-01 jumped well above the 30/day forecast last week. "
     "PO-7002 (300 units) is open. Check whether the purchasing plan needs to change.",
     "SKU-7002", "BOG-01", None, "PO-7002", {"type": "sales_alert", "forecast_daily": 30}),
    ("CASE-S3-C", "demand_change", "Olive oil demand surge at SAO-03",
     "Gallo olive oil sales at SAO-03 are running well above the 40/day forecast. "
     "PO-7003 (400 units) is already in transit.",
     "SKU-7003", "SAO-03", None, "PO-7003", {"type": "sales_alert", "forecast_daily": 40}),
    # --- Scenario 4: purchasing constraints ---
    ("CASE-S4-A", "purchasing_constraint", "Shampoo replenishment vs. remaining budget",
     "The replenishment system recommends 540 units of Head & Shoulders for BOG-01.",
     "SKU-8001", "BOG-01", "REC-8001", None, None),
    ("CASE-S4-B", "purchasing_constraint", "Toilet paper replenishment vs. storage space",
     "The replenishment system recommends 260 units of Papel Familia x12 for MEX-02.",
     "SKU-8002", "MEX-02", "REC-8002", None, None),
    ("CASE-S4-C", "purchasing_constraint", "Greek yogurt: primary supplier MOQ vs. shelf life",
     "The replenishment system recommends 480 units of Yogurt Griego (primary supplier MOQ) for BOG-01.",
     "SKU-8003", "BOG-01", "REC-8003", None, None),
]


def _sales_series(sku: str, base: float) -> list[int]:
    """28 days of history (index 0 = 28 days ago, 27 = yesterday)."""
    rng = random.Random(sku)
    noise = lambda: rng.uniform(-0.08, 0.08)  # noqa: E731
    series = [round(base * (1 + noise())) for _ in range(28)]

    if sku == "SKU-4001":
        # Phantom inventory: sales drop to zero for the last 5 days despite 480 "on hand".
        series[-5:] = [0, 0, 0, 0, 0]
    elif sku == "SKU-7001":
        # Sustained surge (heat wave): ramp over the last 10 days, then hold ~90-95/day.
        ramp = [58, 66, 75, 84, 90, 93, 91, 95, 92, 94]
        series[-10:] = [round(v * (1 + noise() / 2)) for v in ramp]
    elif sku == "SKU-7002":
        # Promo spike on days -5..-3, normal otherwise.
        series[-5], series[-4], series[-3] = 108, 115, 104
    elif sku == "SKU-7003":
        # Sustained surge from 40/day to ~75/day over the last 9 days.
        ramp = [62, 70, 74, 76, 73, 77, 75, 78, 76]
        series[-9:] = [round(v * (1 + noise() / 2)) for v in ramp]
    return series


def seed(conn) -> None:
    conn.executemany("INSERT INTO nodes VALUES (?,?,?)", NODES)
    conn.executemany("INSERT INTO suppliers VALUES (?,?,?,?,?)", SUPPLIERS)
    conn.executemany("INSERT INTO skus VALUES (?,?,?,?,?)", SKUS)
    conn.executemany(
        "INSERT INTO inventory VALUES (?,?,?,?,?)",
        [(s, n, oh, cap, d(lc)) for s, n, oh, cap, lc in INVENTORY],
    )
    conn.executemany(
        "INSERT INTO forecasts VALUES (?,?,?,?,?)",
        [(s, n, f, d(-7), "exponential_smoothing_v3") for s, n, f in FORECASTS],
    )
    for s, n, f in FORECASTS:
        series = _sales_series(s, f)
        conn.executemany(
            "INSERT INTO sales_history VALUES (?,?,?,?)",
            [(s, n, d(i - 28), u) for i, u in enumerate(series)],
        )
    conn.executemany("INSERT INTO promotions VALUES (?,?,?,?,?,?)", PROMOTIONS)
    conn.executemany("INSERT INTO supplier_products VALUES (?,?,?,?,?,?,?,?,?)", SUPPLIER_PRODUCTS)
    conn.executemany(
        "INSERT INTO budgets VALUES (?,?,?,?,?)",
        [(n, c, PERIOD, t, cm) for n, c, t, cm in BUDGETS],
    )
    conn.executemany(
        "INSERT INTO purchase_orders (id, sku_id, node_id, supplier_id, qty_ordered, qty_confirmed, "
        "unit_cost, status, shipping_status, order_day, expected_arrival, created_by, notes) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            (i, s, n, sup, q, qc, UNIT_COST[(sup, s)], st, sh, d(o), d(a), "system", None)
            for i, s, n, sup, q, qc, st, sh, o, a in PURCHASE_ORDERS
        ],
    )
    conn.executemany(
        "INSERT INTO recommendations VALUES (?,?,?,?,?,?,?)",
        [(i, s, n, sup, q, r, d(0)) for i, s, n, sup, q, r in RECOMMENDATIONS],
    )
    conn.executemany(
        "INSERT INTO cases VALUES (?,?,?,?,?,?,?,?,?)",
        [(*c[:8], json.dumps(c[8]) if c[8] else None) for c in CASES],
    )
