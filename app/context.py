"""Assemble a planning ItemContext from the ERP."""
from app.planning import ItemContext, analyze_demand
from app.repository import Repository


def demand_signal(repo: Repository, sku_id: str, node_id: str) -> dict:
    fc = repo.get_forecast(sku_id, node_id)
    return analyze_demand(repo.get_sales(sku_id, node_id, 28), fc["daily_units"] if fc else 0.0,
                          repo.get_promotions(sku_id, node_id))


def load_item_context(repo: Repository, sku_id: str, node_id: str,
                      daily_demand: float | None = None) -> ItemContext:
    sku = repo.get_sku(sku_id)
    if sku is None:
        raise ValueError(f"unknown sku {sku_id}")
    inv = repo.get_inventory(sku_id, node_id)
    if inv is None:
        raise ValueError(f"{sku_id} is not stocked at {node_id}")
    if daily_demand is None:
        daily_demand = demand_signal(repo, sku_id, node_id)["recommended_daily_rate"]
    budget = repo.get_budget(node_id, sku["category"])
    return ItemContext(
        sku=sku,
        inventory=inv,
        daily_demand=float(daily_demand),
        open_pos=repo.get_open_pos(sku_id, node_id),
        suppliers=repo.get_supplier_options(sku_id),
        budget_remaining=budget["remaining"] if budget else 0.0,
    )
