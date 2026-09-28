"""Deterministic purchasing agent.

Uses exactly the same tools, decision contract, validation and feedback loop
as the LLM agent. It serves three purposes:

1. It runs without an API key (demo + CI).
2. It is the *baseline* the LLM agent is evaluated against.
3. It documents, in code, the buyer playbook the LLM is asked to follow.

It contains no case-specific logic — only general purchasing rules.
"""
from __future__ import annotations

from app.agent.tools import ToolBox
from app.models import Action, Decision
from app.planning import round_to_pack
from app.validator import MAX_SUPPLIER_PREMIUM


class RuleBasedAgent:
    name = "rules"

    def decide(self, tools: ToolBox, feedback: dict | None = None) -> Decision:
        case = tools.call("get_case_details")
        item = tools.call("get_item_profile")
        demand = tools.call("get_demand_signal")
        open_pos = tools.call("get_open_purchase_orders")
        suppliers = tools.call("get_supplier_options")
        budget = tools.call("get_budget")

        rate = demand["recommended_daily_rate"]
        rec = case.get("recommendation")
        factors = [f"Demand: {demand['note']} Planning rate {rate}/day."]
        inbound = sum(p["inbound_qty"] for p in open_pos)
        factors.append(f"Inventory position: {item['on_hand']} on hand + {inbound} inbound on "
                       f"{len(open_pos)} open PO(s); storage capacity {item['storage_capacity']}.")
        factors.append(f"Budget remaining: ${budget['remaining']:,.2f} ({item['category']}).")

        # 1. Data quality gate --------------------------------------------------------
        if demand["signal"] == "possible_stockout_or_data_issue":
            factors.append(f"Last cycle count was {item['days_since_cycle_count']} days ago.")
            return Decision(
                verdict="investigate", daily_demand_used=rate, confidence="high",
                summary=(f"Do not buy yet. The system shows {item['on_hand']} units on hand but sales have been "
                         f"zero for {demand['trailing_zero_sales_days']} days against a forecast of "
                         f"{demand['forecast_daily']}/day. This pattern indicates phantom inventory or a data "
                         f"problem; any quantity computed from these numbers is unreliable. Requesting a cycle count."),
                key_factors=factors,
                actions=[Action(type="request_cycle_count",
                                reason=f"Verify physical stock of {item['name']} at {item['node_id']}: system "
                                       f"on-hand {item['on_hand']} vs {demand['trailing_zero_sales_days']} days of "
                                       f"zero sales. Last count {item['days_since_cycle_count']} days ago."),
                         Action(type="escalate",
                                reason=f"Recommendation {rec['quantity'] if rec else ''} units on hold pending "
                                       "cycle count; possible phantom inventory causing lost sales.",
                                options=["Approve emergency order sized after the count",
                                         "Keep recommendation on hold"])])

        # 2. Which suppliers are eligible? -------------------------------------------------
        excluded: dict[str, str] = {}
        event = case.get("event") or {}
        if event.get("type") == "supplier_shortfall":
            po = next((p for p in open_pos if p["id"] == event["po_id"]), None)
            if po:
                excluded[po["supplier_id"]] = "reported it cannot supply more"
                factors.append(f"{po['id']}: supplier {po['supplier_id']} confirmed {po['qty_confirmed']} of "
                               f"{po['qty_ordered']} units; only the confirmed quantity is counted as inbound.")
        for sid in (feedback or {}).get("failed_suppliers", []):
            excluded[sid] = "could not fully confirm our order in this run"

        options = tools.call("evaluate_supplier_options", daily_demand=rate)
        by_id = {o["supplier_id"]: o for o in options}

        def eligible(o: dict) -> str | None:
            if o["supplier_id"] in excluded:
                return excluded[o["supplier_id"]]
            if o["status"] != "active":
                return f"status {o['status']}"
            if o["premium_vs_primary"] > MAX_SUPPLIER_PREMIUM:
                return f"{o['premium_vs_primary']:.0%} price premium exceeds policy cap"
            return None

        candidates = [o for o in options if eligible(o) is None]
        for o in options:
            if eligible(o):
                factors.append(f"Supplier {o['supplier_id']} not eligible: {eligible(o)}.")
        # Preferred order: the recommended supplier, then arrive before stock-out, then cheapest, then fastest.
        rec_sup = rec["supplier_id"] if rec else None
        candidates.sort(key=lambda o: (o["supplier_id"] != rec_sup, o["arrives_after_projected_stockout"],
                                       o["unit_cost"], o["lead_time_days"]))

        # 3. Is anything needed at all? -------------------------------------------------------
        ref = candidates[0] if candidates else min(options, key=lambda o: o["lead_time_days"])
        need = ref["requirement"]["net_requirement"]
        factors.append(f"Net requirement with {ref['supplier_id']} (lead time {ref['lead_time_days']}d): target "
                       f"{ref['requirement']['target_stock']:.0f} vs position "
                       f"{ref['requirement']['inventory_position']} → {need} units.")
        if rec:
            factors.append(f"System recommendation: {rec['quantity']} units from {rec['supplier_id']} "
                           f"(rationale: {rec['rationale']})")

        if need == 0:
            if rec:
                return Decision(verdict="reject", daily_demand_used=rate, confidence="high", key_factors=factors,
                                summary=(f"Reject the {rec['quantity']}-unit recommendation. Current stock plus "
                                         f"open orders already cover lead time, review period and safety stock; "
                                         f"buying now would only add excess inventory."))
            return Decision(verdict="accept", daily_demand_used=rate, confidence="high", key_factors=factors,
                            summary=("No change needed. Inventory on hand plus confirmed inbound quantity covers "
                                     "demand through the next review including safety stock."))

        # 4. Prefer increasing an existing, changeable PO for demand changes ----------------
        if case["scenario"] == "demand_change":
            mod = self._modify_existing(open_pos, by_id, rate, factors, excluded)
            if mod:
                return mod

        # 5. Build a purchase plan ----------------------------------------------------------------
        actions: list[Action] = []
        escalation: list[str] = []
        plan = None
        for o in candidates:            # first choice: a fully feasible supplier (single or split delivery)
            if o["feasible"]:
                plan = [Action(type="create_po", supplier_id=o["supplier_id"], quantity=o["order_qty"],
                               reason=f"Net requirement {o['requirement']['net_requirement']} rounded to case "
                                      f"pack {o['case_pack']} / MOQ {o['moq']}.")]
            elif o["feasible_with_split"]:
                plan = [Action(type="create_po", supplier_id=o["supplier_id"], quantity=s["qty"],
                               delivery_day=s["day"],
                               reason=f"Delivery {i + 1}/{len(o['split_delivery_plan'])}: split to fit "
                                      f"{item['storage_capacity']}-unit storage.")
                        for i, s in enumerate(o["split_delivery_plan"])]
                factors.append(f"Single delivery of {o['order_qty']} would overflow storage "
                               f"(free at arrival {o['free_storage_at_arrival']:.0f}); split into "
                               f"{len(plan)} staggered deliveries.")
            if plan:
                chosen = o
                break
            factors.append(f"{o['supplier_id']} cannot be used as-is: {'; '.join(o['issues'])}.")

        if plan is None:                # second choice: buy what the budget allows, escalate the rest
            for o in candidates:
                afford = min(o["max_qty_by_budget"], o["order_qty"])
                if _only_budget(o) and afford >= o["moq"] and afford > 0:
                    plan = [Action(type="create_po", supplier_id=o["supplier_id"], quantity=afford,
                                   reason=f"Largest case-pack multiple within remaining budget "
                                          f"${budget['remaining']:,.2f}.")]
                    short = o["order_qty"] - afford
                    escalation.append(
                        f"Budget constraint: need {o['order_qty']} units (${o['order_value']:,.2f}) but only "
                        f"${budget['remaining']:,.2f} remains. Ordered {afford}; {short} units "
                        f"(${short * o['unit_cost']:,.2f}) require a budget exception.")
                    chosen = o
                    break

        if plan is None:
            opts = [f"{o['supplier_id']}: {'; '.join(o['issues'] or [eligible(o) or 'n/a'])}" for o in options]
            return Decision(
                verdict="escalate", daily_demand_used=rate, confidence="high", key_factors=factors,
                summary=(f"Need {need} more units but no eligible supplier can deliver within policy. "
                         "Escalating to the buyer with the options evaluated."),
                actions=[Action(type="escalate",
                                reason=f"{item['name']} at {item['node_id']}: requirement {need} units cannot be "
                                       f"sourced within policy (price cap, supplier availability, lead time).",
                                options=opts + ["Approve an off-policy supplier", "Accept stock-out risk"])])

        actions.extend(plan)
        if chosen["arrives_after_projected_stockout"]:
            escalation.append(f"Stock-out projected before any new supply can land (day "
                              f"{chosen['lead_time_days']}); expect lost sales in the meantime.")
        if escalation:
            actions.append(Action(type="escalate", reason=" ".join(escalation),
                                  options=["Approve budget exception / expedite", "Accept reduced cover"]))

        total = sum(a.quantity for a in plan)
        factors.append(f"Chosen supplier {chosen['supplier_id']}: ${chosen['unit_cost']}/unit, lead time "
                       f"{chosen['lead_time_days']}d, MOQ {chosen['moq']}, case {chosen['case_pack']}.")
        if rec and len(plan) == 1 and plan[0].supplier_id == rec["supplier_id"] and total == rec["quantity"]:
            verdict, summary = "accept", (f"Accept: the recommended {rec['quantity']} units match the computed "
                                          f"requirement and pass every constraint.")
        else:
            verdict = "modify"
            if rec and total == rec["quantity"] and len(plan) > 1:
                summary = (f"Modify how the recommendation is executed: keep {total} units but split into "
                           f"{len(plan)} staggered deliveries so each receipt fits storage.")
            elif rec:
                summary = (f"Modify the recommendation from {rec['quantity']} to {total} units"
                           + (f" from {chosen['supplier_id']}" if chosen["supplier_id"] != rec["supplier_id"] else "")
                           + (f" in {len(plan)} deliveries" if len(plan) > 1 else "")
                           + (". The remainder is escalated." if escalation else ".")
                           + f" Requirement is {need} units after counting stock and open orders.")
            else:
                summary = (f"Order {total} units from {chosen['supplier_id']} to close a {need}-unit gap"
                           + (". Residual risk escalated." if escalation else "."))
        return Decision(verdict=verdict, summary=summary, key_factors=factors, daily_demand_used=rate,
                        actions=actions, confidence="high")

    # ------------------------------------------------------------------------------------------
    def _modify_existing(self, open_pos, by_id, rate, factors, excluded) -> Decision | None:
        for po in open_pos:
            sup = by_id.get(po["supplier_id"])
            if (not sup or po["supplier_id"] in excluded or po["shipping_status"] != "not_shipped"
                    or not sup["accepts_modifications"]):
                if sup:
                    factors.append(f"{po['id']} cannot be increased ({po['shipping_status']}, supplier "
                                   f"{'accepts' if sup['accepts_modifications'] else 'does not accept'} changes).")
                continue
            need = sup["requirement"]["net_requirement"]
            add = round_to_pack(need, sup["case_pack"])
            new_total = po["inbound_qty"] + add
            factors.append(f"{po['id']} is not shipped yet and {po['supplier_id']} accepts changes: increase it "
                           f"by {add} to {new_total} (arrives day {po['arrival_day']}, sooner than a new PO).")
            return Decision(
                verdict="modify", daily_demand_used=rate, confidence="high", key_factors=factors,
                summary=(f"Demand has shifted to ~{rate}/day. Increase {po['id']} from {po['inbound_qty']} to "
                         f"{new_total} units rather than placing a new order."),
                actions=[Action(type="modify_po", po_id=po["id"], quantity=new_total,
                                reason=f"Sustained demand increase to {rate}/day; net requirement {need}.")])
        return None


def _only_budget(o: dict) -> bool:
    return all("budget" in i or "stockout" in i for i in o["issues"])
