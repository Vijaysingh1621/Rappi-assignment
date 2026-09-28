"""SQLite storage for the mock ERP / purchasing system.

The whole world the agent operates in lives here: SKUs, nodes (dark stores),
inventory, sales history, forecasts, promotions, suppliers, purchase orders,
budgets, recommendations, purchasing cases, and the agent's own audit trail.
"""
import json
import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from app.config import get_settings

# The simulation runs on a fixed clock so every run is reproducible.
SIM_TODAY = date(2026, 9, 28)

SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skus (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    shelf_life_days INTEGER NOT NULL,     -- max days of cover we may hold
    safety_stock_days REAL NOT NULL       -- safety stock policy in days of demand
);

CREATE TABLE IF NOT EXISTS inventory (
    sku_id TEXT NOT NULL REFERENCES skus(id),
    node_id TEXT NOT NULL REFERENCES nodes(id),
    on_hand INTEGER NOT NULL,
    storage_capacity INTEGER NOT NULL,    -- slot / planogram capacity in units
    last_counted TEXT NOT NULL,           -- last physical cycle count
    PRIMARY KEY (sku_id, node_id)
);

CREATE TABLE IF NOT EXISTS sales_history (
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    day TEXT NOT NULL,
    units INTEGER NOT NULL,
    PRIMARY KEY (sku_id, node_id, day)
);

CREATE TABLE IF NOT EXISTS forecasts (
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    daily_units REAL NOT NULL,
    generated_on TEXT NOT NULL,
    method TEXT NOT NULL,
    PRIMARY KEY (sku_id, node_id)
);

CREATE TABLE IF NOT EXISTS promotions (
    id TEXT PRIMARY KEY,
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    start_day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL,                 -- active | blocked
    on_time_rate REAL NOT NULL,           -- historical on-time-in-full rate
    fill_rate REAL NOT NULL               -- historical share of ordered qty delivered
);

CREATE TABLE IF NOT EXISTS supplier_products (
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    sku_id TEXT NOT NULL REFERENCES skus(id),
    unit_cost REAL NOT NULL,
    moq INTEGER NOT NULL,
    case_pack INTEGER NOT NULL,
    lead_time_days INTEGER NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0,
    accepts_modifications INTEGER NOT NULL DEFAULT 1,
    -- Hidden from the agent: what the supplier can *really* ship right now.
    -- Only revealed through the supplier's response to a submitted PO.
    live_available_units INTEGER NOT NULL,
    PRIMARY KEY (supplier_id, sku_id)
);

CREATE TABLE IF NOT EXISTS budgets (
    node_id TEXT NOT NULL,
    category TEXT NOT NULL,
    period TEXT NOT NULL,
    total REAL NOT NULL,
    committed REAL NOT NULL,
    PRIMARY KEY (node_id, category, period)
);

CREATE TABLE IF NOT EXISTS purchase_orders (
    id TEXT PRIMARY KEY,
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    qty_ordered INTEGER NOT NULL,
    qty_confirmed INTEGER,                -- NULL until the supplier responds
    unit_cost REAL NOT NULL,
    status TEXT NOT NULL,                 -- submitted|confirmed|partially_confirmed|rejected|cancelled|received
    shipping_status TEXT NOT NULL DEFAULT 'not_shipped',  -- not_shipped | in_transit
    order_day TEXT NOT NULL,
    expected_arrival TEXT NOT NULL,
    created_by TEXT NOT NULL,             -- system | agent | human
    source_run_id TEXT,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS recommendations (
    id TEXT PRIMARY KEY,
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    supplier_id TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    rationale TEXT NOT NULL,
    created_on TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    scenario TEXT NOT NULL,               -- recommendation_review | supplier_shortfall | demand_change | purchasing_constraint
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    sku_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    recommendation_id TEXT,
    po_id TEXT,
    event TEXT                            -- JSON: triggering event (e.g. supplier message)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id TEXT PRIMARY KEY,
    case_id TEXT NOT NULL,
    mode TEXT NOT NULL,                   -- rules | llm
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    decision TEXT,                        -- JSON
    trace TEXT                            -- JSON list of steps
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    actions TEXT NOT NULL,                -- JSON list of actions awaiting sign-off
    reasons TEXT NOT NULL,                -- JSON list of why approval is required
    status TEXT NOT NULL,                 -- pending | approved | rejected
    decided_by TEXT,
    decided_at TEXT,
    comment TEXT
);

CREATE TABLE IF NOT EXISTS escalations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    case_id TEXT NOT NULL,
    kind TEXT NOT NULL,                   -- escalation | cycle_count
    summary TEXT NOT NULL,
    options TEXT,                         -- JSON list of options for the human
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or get_settings().database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection, reset: bool = False) -> None:
    from app.seed import seed

    if reset:
        tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.execute("PRAGMA foreign_keys = OFF")
        for t in tables:
            conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM skus").fetchone()[0] == 0:
        seed(conn)
    conn.commit()


@contextmanager
def session(path: Path | None = None, reset: bool = False):
    conn = connect(path)
    try:
        init_db(conn, reset=reset)
        yield conn
    finally:
        conn.close()


def rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def row(cursor) -> dict | None:
    r = cursor.fetchone()
    return dict(r) if r else None


def dumps(obj) -> str:
    return json.dumps(obj, default=str)
