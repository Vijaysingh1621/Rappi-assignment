"""Deterministic purchasing math.

The agent (LLM or rules) reasons about *what* to do; the arithmetic that
decides *how much* lives here so that it is testable and never hallucinated.

Conventions
-----------
* Day 0 is today. A PO with lead time L ordered today arrives at the start of day L.
* Receipts land at the start of a day, then that day's demand is served.
* Unserved demand is lost (quick-commerce customers don't wait for backorders).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from statistics import mean, median

REVIEW_PERIOD_DAYS = 7          # how often each SKU is reviewed / can be re-ordered
SURGE_THRESHOLD = 1.25          # clean recent demand vs forecast to call a shift
DAY_ABOVE_THRESHOLD = 1.2       # a single day counts as "above forecast" above this ratio
OUTLIER_MULTIPLIER = 2.5        # a day this many times the median is an outlier
ACCEPT_TOLERANCE = 0.10         # recommendation within ±10% of computed need is acceptable


# ----------------------------------------------------------------------------------
# Demand analysis
# ----------------------------------------------------------------------------------
def analyze_demand(sales: list[dict], forecast_daily: float, promotions: list[dict]) -> dict:
    """Classify the recent demand signal and recommend a planning rate.

    ``sales`` is a chronological list of {day, units}; the last item is yesterday.
    """
    units = [s["units"] for s in sales]
    last7 = sales[-7:]
    med = median(units) if units else 0

    promo_days = {
        s["day"] for s in sales for p in promotions if p["start_day"] <= s["day"] <= p["end_day"]
    }
    outlier_days = {s["day"] for s in sales if med > 0 and s["units"] > OUTLIER_MULTIPLIER * med}

    clean7 = [s["units"] for s in last7 if s["day"] not in promo_days | outlier_days]
    avg7 = mean(s["units"] for s in last7) if last7 else 0.0
    avg28 = mean(units) if units else 0.0
    clean_avg7 = mean(clean7) if clean7 else avg7
    days_above = sum(1 for u in clean7 if forecast_daily and u > DAY_ABOVE_THRESHOLD * forecast_daily)
    days_below = sum(1 for u in clean7 if forecast_daily and u < forecast_daily / DAY_ABOVE_THRESHOLD)

    zero_streak = 0
    for u in reversed(units):
        if u != 0:
            break
        zero_streak += 1

    if zero_streak >= 3 and forecast_daily > 0:
        signal = "possible_stockout_or_data_issue"
        rate = forecast_daily
        note = (f"{zero_streak} consecutive days with zero sales while forecast is {forecast_daily}/day. "
                "Either the item is not actually available (phantom inventory) or sales data is broken.")
    elif forecast_daily and clean_avg7 > SURGE_THRESHOLD * forecast_daily and days_above >= 5:
        signal = "sustained_increase"
        rate = round(clean_avg7, 1)
        note = (f"Clean 7-day average {clean_avg7:.1f}/day is {clean_avg7 / forecast_daily:.2f}x forecast "
                f"with {days_above}/{len(clean7)} days above forecast — demand has shifted.")
    elif forecast_daily and avg7 > SURGE_THRESHOLD * forecast_daily:
        signal = "transient_spike"
        rate = forecast_daily
        why = []
        if promo_days & {s["day"] for s in last7}:
            why.append("a promotion that has ended")
        if outlier_days & {s["day"] for s in last7}:
            why.append("single-day outliers")
        note = (f"Raw 7-day average {avg7:.1f}/day is elevated, but excluding "
                f"{' and '.join(why) or 'anomalous days'} demand is {clean_avg7:.1f}/day, in line with forecast.")
    elif forecast_daily and clean_avg7 < forecast_daily / SURGE_THRESHOLD and days_below >= 5:
        signal = "sustained_decrease"
        rate = round(clean_avg7, 1)
        note = f"Clean 7-day average {clean_avg7:.1f}/day is well below forecast {forecast_daily}/day."
    else:
        signal = "stable"
        rate = forecast_daily
        note = f"Recent demand ({clean_avg7:.1f}/day) is consistent with forecast ({forecast_daily}/day)."

    return {
        "forecast_daily": forecast_daily,
        "avg_7d": round(avg7, 1),
        "avg_28d": round(avg28, 1),
        "clean_avg_7d": round(clean_avg7, 1),
        "days_above_forecast_last7": days_above,
        "trailing_zero_sales_days": zero_streak,
        "promo_days_excluded": sorted(promo_days & {s["day"] for s in last7}),
        "outlier_days_excluded": sorted(outlier_days & {s["day"] for s in last7}),
        "signal": signal,
        "recommended_daily_rate": rate,
        "note": note,
        "last_7_days": [s["units"] for s in last7],
    }


# ----------------------------------------------------------------------------------
# Inventory projection
# ----------------------------------------------------------------------------------
@dataclass
class Arrival:
    day: int
    qty: int
    label: str = ""


def project_inventory(on_hand: int, daily_demand: float, arrivals: list[Arrival],
                      horizon: int, capacity: int | None = None) -> dict:
    stock = float(on_hand)
    days, first_stockout, lost, max_overflow = [], None, 0.0, 0.0
    for d in range(horizon):
        received = sum(a.qty for a in arrivals if max(a.day, 0) == d)
        begin = stock
        stock += received
        overflow = max(0.0, stock - capacity) if capacity is not None else 0.0
        max_overflow = max(max_overflow, overflow)
        sold = min(stock, daily_demand)
        short = daily_demand - sold
        if short > 1e-9 and first_stockout is None:
            first_stockout = d
        lost += short
        stock -= sold
        days.append({"day": d, "begin": round(begin, 1), "received": received,
                     "on_hand_after_receipt": round(begin + received, 1),
                     "demand": round(daily_demand, 1), "lost": round(short, 1),
                     "end": round(stock, 1), "overflow": round(overflow, 1)})
    return {
        "horizon_days": horizon,
        "first_stockout_day": first_stockout,
        "lost_units": round(lost, 1),
        "max_storage_overflow": round(max_overflow, 1),
        "end_stock": round(stock, 1),
        "days": days,
    }


def stock_before_receipt(on_hand: int, daily_demand: float, arrivals: list[Arrival], day: int) -> float:
    """Units physically on hand at the start of ``day`` including that day's other receipts."""
    proj = project_inventory(on_hand, daily_demand, arrivals, day + 1)
    return proj["days"][day]["on_hand_after_receipt"]


# ----------------------------------------------------------------------------------
# Requirement & rounding
# ----------------------------------------------------------------------------------
def net_requirement(on_hand: int, inbound: int, daily_demand: float, lead_time: int,
                    safety_days: float, review_days: int = REVIEW_PERIOD_DAYS) -> dict:
    """Order-up-to policy: cover lead time + review period + safety stock."""
    cycle = daily_demand * (lead_time + review_days)
    safety = daily_demand * safety_days
    target = cycle + safety
    position = on_hand + inbound
    return {
        "daily_demand": daily_demand,
        "lead_time_days": lead_time,
        "review_period_days": review_days,
        "cycle_stock": round(cycle, 1),
        "safety_stock": round(safety, 1),
        "target_stock": round(target, 1),
        "inventory_position": position,
        "net_requirement": max(0, math.ceil(target - position)),
    }


def round_to_pack(qty: float, case_pack: int, moq: int = 0) -> int:
    if qty <= 0:
        return 0
    q = math.ceil(qty / case_pack) * case_pack
    min_q = math.ceil(moq / case_pack) * case_pack
    return max(q, min_q)


def floor_to_pack(qty: float, case_pack: int) -> int:
    return max(0, math.floor(qty / case_pack) * case_pack)


# ----------------------------------------------------------------------------------
# Supplier option evaluation
# ----------------------------------------------------------------------------------
@dataclass
class ItemContext:
    """Everything the planner needs about one SKU at one node."""
    sku: dict
    inventory: dict
    daily_demand: float
    open_pos: list[dict]
    suppliers: list[dict]
    budget_remaining: float
    extra_arrivals: list[Arrival] = field(default_factory=list)

    @property
    def arrivals(self) -> list[Arrival]:
        return [Arrival(po["arrival_day"], po["inbound_qty"], po["id"]) for po in self.open_pos] + self.extra_arrivals

    @property
    def inbound(self) -> int:
        return sum(a.qty for a in self.arrivals)

    @property
    def primary_cost(self) -> float | None:
        prim = [s for s in self.suppliers if s["is_primary"]]
        return prim[0]["unit_cost"] if prim else None

    def requirement(self, lead_time: int) -> dict:
        return net_requirement(self.inventory["on_hand"], self.inbound, self.daily_demand,
                               lead_time, self.sku["safety_stock_days"])

    def projection(self, horizon: int, extra: list[Arrival] | None = None) -> dict:
        return project_inventory(self.inventory["on_hand"], self.daily_demand,
                                 self.arrivals + (extra or []), horizon,
                                 self.inventory["storage_capacity"])


def plan_deliveries(ctx: ItemContext, lead_time: int, qty: int, case_pack: int, moq: int,
                    max_days: int = 30) -> list[Arrival]:
    """Split ``qty`` into receipts that each fit into storage when they land."""
    cap = ctx.inventory["storage_capacity"]
    planned: list[Arrival] = []
    remaining, day = qty, lead_time
    while remaining > 0 and day <= lead_time + max_days:
        room = cap - stock_before_receipt(ctx.inventory["on_hand"], ctx.daily_demand,
                                          ctx.arrivals + planned, day)
        chunk = min(remaining, floor_to_pack(room, case_pack))
        if 0 < remaining - chunk < moq:
            # never leave a tail smaller than the MOQ for a later delivery
            chunk = remaining - math.ceil(moq / case_pack) * case_pack
        if chunk >= moq:
            planned.append(Arrival(day, chunk, "planned"))
            remaining -= chunk
        day += 1
    if remaining > 0:
        return []
    return planned


def evaluate_supplier(ctx: ItemContext, sup: dict, need: int | None = None,
                      exclude_reason: str | None = None) -> dict:
    """Assess one supplier for covering ``need`` units (defaults to the net requirement
    computed with this supplier's lead time)."""
    L = sup["lead_time_days"]
    req = ctx.requirement(L)
    need = req["net_requirement"] if need is None else need
    qty = round_to_pack(need, sup["case_pack"], sup["moq"])
    stock_at_arrival = stock_before_receipt(ctx.inventory["on_hand"], ctx.daily_demand, ctx.arrivals, L)
    cap = ctx.inventory["storage_capacity"]
    shelf_cap_units = ctx.daily_demand * ctx.sku["shelf_life_days"]
    baseline = ctx.projection(L + REVIEW_PERIOD_DAYS)
    premium = (sup["unit_cost"] / ctx.primary_cost - 1) if ctx.primary_cost else 0.0

    issues: list[str] = []
    if exclude_reason:
        issues.append(exclude_reason)
    if sup["status"] != "active":
        issues.append(f"supplier status is '{sup['status']}'")
    max_by_budget = floor_to_pack(ctx.budget_remaining / sup["unit_cost"], sup["case_pack"])
    if qty * sup["unit_cost"] > ctx.budget_remaining + 1e-6:
        issues.append(f"cost ${qty * sup['unit_cost']:,.2f} exceeds remaining budget ${ctx.budget_remaining:,.2f}")
    max_by_shelf = floor_to_pack(shelf_cap_units - stock_at_arrival, sup["case_pack"])
    if qty > max_by_shelf:
        issues.append(f"{qty} units would exceed shelf-life cover ({ctx.sku['shelf_life_days']} days = "
                      f"{shelf_cap_units:.0f} units incl. stock at arrival); MOQ {sup['moq']}")
    room = cap - stock_at_arrival
    split = None
    if qty > room:
        split = plan_deliveries(ctx, L, qty, sup["case_pack"], sup["moq"])
        issues.append(f"single delivery of {qty} exceeds free storage at arrival ({room:.0f} units)"
                      + ("; split delivery possible" if split else "; no feasible split"))
    arrives_after_stockout = (baseline["first_stockout_day"] is not None
                              and baseline["first_stockout_day"] < L)
    if arrives_after_stockout:
        issues.append(f"arrives day {L}, after projected stockout on day {baseline['first_stockout_day']}")

    # Storage overflow and late arrival are handled by the planner (split / escalate);
    # everything else makes the supplier unusable for this quantity.
    hard = [i for i in issues if "free storage" not in i and "after projected stockout" not in i]
    return {
        "supplier_id": sup["supplier_id"],
        "supplier_name": sup["name"],
        "status": sup["status"],
        "lead_time_days": L,
        "unit_cost": sup["unit_cost"],
        "premium_vs_primary": round(premium, 3),
        "moq": sup["moq"],
        "case_pack": sup["case_pack"],
        "on_time_rate": sup.get("on_time_rate"),
        "fill_rate": sup.get("fill_rate"),
        "accepts_modifications": bool(sup.get("accepts_modifications", 1)),
        "requirement": req,
        "order_qty": qty,
        "order_value": round(qty * sup["unit_cost"], 2),
        "stock_at_arrival": stock_at_arrival,
        "free_storage_at_arrival": round(room, 1),
        "max_qty_by_budget": max_by_budget,
        "max_qty_by_shelf_life": max_by_shelf,
        "split_delivery_plan": [{"day": a.day, "qty": a.qty} for a in split] if split else None,
        "arrives_after_projected_stockout": arrives_after_stockout,
        "issues": issues,
        "feasible": qty > 0 and not hard and qty <= room,
        "feasible_with_split": qty > 0 and not hard and bool(split),
    }


def days_of_cover(stock: float, daily_demand: float) -> float | None:
    return round(stock / daily_demand, 1) if daily_demand > 0 else None
