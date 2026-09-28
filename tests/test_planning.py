from datetime import timedelta

from app.db import SIM_TODAY
from app.planning import (
    Arrival,
    ItemContext,
    analyze_demand,
    evaluate_supplier,
    net_requirement,
    plan_deliveries,
    project_inventory,
    round_to_pack,
)


def _sales(values):
    start = SIM_TODAY - timedelta(days=len(values))
    return [{"day": (start + timedelta(days=i)).isoformat(), "units": u} for i, u in enumerate(values)]


def _ctx(on_hand=100, rate=10.0, cap=1000, shelf=365, pos=None, budget=10_000, suppliers=None):
    sup = suppliers or [{"supplier_id": "S1", "name": "S1", "status": "active", "unit_cost": 2.0,
                         "moq": 24, "case_pack": 12, "lead_time_days": 3, "is_primary": 1}]
    return ItemContext(
        sku={"id": "X", "shelf_life_days": shelf, "safety_stock_days": 2, "category": "c"},
        inventory={"on_hand": on_hand, "storage_capacity": cap},
        daily_demand=rate, open_pos=pos or [], suppliers=sup, budget_remaining=budget)


# --- demand signal ---------------------------------------------------------------
def test_stable_demand_uses_forecast():
    sig = analyze_demand(_sales([30] * 28), 30, [])
    assert sig["signal"] == "stable"
    assert sig["recommended_daily_rate"] == 30


def test_sustained_surge_uses_recent_rate():
    sig = analyze_demand(_sales([50] * 21 + [90, 92, 95, 91, 94, 93, 90]), 50, [])
    assert sig["signal"] == "sustained_increase"
    assert 90 <= sig["recommended_daily_rate"] <= 95


def test_promo_spike_is_transient():
    sales = _sales([30] * 23 + [110, 115, 104, 31, 30])
    promo = [{"start_day": sales[23]["day"], "end_day": sales[25]["day"]}]
    sig = analyze_demand(sales, 30, promo)
    assert sig["signal"] == "transient_spike"
    assert sig["recommended_daily_rate"] == 30
    assert len(sig["promo_days_excluded"]) == 3


def test_single_outlier_without_promo_is_transient():
    sig = analyze_demand(_sales([30] * 27 + [400]), 30, [])
    assert sig["signal"] == "transient_spike"


def test_zero_sales_streak_flags_data_issue():
    sig = analyze_demand(_sales([60] * 23 + [0] * 5), 60, [])
    assert sig["signal"] == "possible_stockout_or_data_issue"


# --- projection -------------------------------------------------------------------
def test_projection_detects_stockout_and_overflow():
    proj = project_inventory(50, 20, [Arrival(3, 500)], horizon=6, capacity=400)
    assert proj["first_stockout_day"] == 2        # 50 -> 30 -> 10 -> short on day 2
    assert proj["lost_units"] == 10               # 10 on hand vs 20 demand on day 2; receipt on day 3
    assert proj["max_storage_overflow"] == 100    # 0 + 500 > 400


def test_projection_arrival_prevents_stockout():
    proj = project_inventory(50, 20, [Arrival(2, 100)], horizon=5)
    assert proj["first_stockout_day"] is None


# --- requirement & rounding --------------------------------------------------------
def test_net_requirement_counts_open_orders():
    req = net_requirement(on_hand=150, inbound=200, daily_demand=40, lead_time=4, safety_days=3)
    assert req["target_stock"] == 560
    assert req["net_requirement"] == 210


def test_net_requirement_never_negative():
    assert net_requirement(900, 0, 10, 3, 2)["net_requirement"] == 0


def test_round_to_pack_respects_moq_and_case():
    assert round_to_pack(210, 24, 96) == 216
    assert round_to_pack(10, 12, 50) == 60
    assert round_to_pack(0, 12, 50) == 0


# --- supplier evaluation --------------------------------------------------------------
def test_budget_violation_is_reported():
    ev = evaluate_supplier(_ctx(on_hand=0, rate=50, budget=100), _ctx().suppliers[0])
    assert not ev["feasible"]
    assert any("budget" in i for i in ev["issues"])
    assert ev["max_qty_by_budget"] == 48


def test_moq_above_shelf_life_is_infeasible():
    sup = {"supplier_id": "S1", "name": "S1", "status": "active", "unit_cost": 2.0,
           "moq": 480, "case_pack": 24, "lead_time_days": 2, "is_primary": 1}
    ev = evaluate_supplier(_ctx(on_hand=20, rate=8, shelf=21, suppliers=[sup]), sup)
    assert not ev["feasible"]
    assert any("shelf-life" in i for i in ev["issues"])


def test_blocked_supplier_is_infeasible():
    sup = dict(_ctx().suppliers[0], status="blocked")
    assert not evaluate_supplier(_ctx(on_hand=0), sup)["feasible"]


def test_split_delivery_fits_storage_and_moq():
    ctx = _ctx(on_hand=100, rate=30, cap=200)
    plan = plan_deliveries(ctx, lead_time=3, qty=260, case_pack=10, moq=50)
    assert sum(a.qty for a in plan) == 260
    assert all(a.qty >= 50 for a in plan)
    proj = ctx.projection(12, plan)
    assert proj["max_storage_overflow"] == 0
