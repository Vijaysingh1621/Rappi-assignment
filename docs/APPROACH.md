# Approach

## 1. Breaking the problem down

A buyer's decision on one item at one store always reduces to the same questions, whatever the trigger
(a recommendation, a short-shipment, a sales alert):

1. **What will demand really be?** The forecast, recent sales, and whether the difference is real (a sustained
   shift) or noise (a promotion, an outlier, broken data).
2. **What do we already have?** Stock on hand plus what is *confirmed* inbound, and when it lands.
3. **What do we need?** Enough to cover the time until the next order could arrive, plus safety stock.
4. **Where can we get it within the rules?** Supplier eligibility, cost, MOQ, case pack, lead time, budget,
   storage at the moment of receipt, shelf life.
5. **Can I act alone?** Or does this need a human because of value, irreversibility, or residual risk?
6. **Did it work?** Did the supplier confirm what we asked for, and are we now covered?

So the system has one general loop, not one pipeline per scenario. The four scenarios differ only in the trigger
and in what counts as "the plan" being reviewed.

## 2. Information the agent needs, and why

| Information | Why it matters | Tool |
|---|---|---|
| Recommendation / triggering event | What is being decided; the recommendation is a hypothesis, not an instruction | `get_case_details` |
| On hand, slot capacity, last cycle count, shelf life, safety-stock policy | Starting position; storage and perishability limits; data freshness | `get_item_profile` |
| 28-day sales, forecast, promotions, demand classification | Distinguish real shifts from promo spikes, outliers and data errors | `get_demand_signal` |
| Open POs: ordered vs **confirmed**, shipping status, ETA | Inventory position; whether a PO can still be changed | `get_open_purchase_orders` |
| Suppliers: cost, MOQ, case pack, lead time, status, reliability, modification policy | Feasible sourcing options | `get_supplier_options` |
| Budget remaining for the category at the node | Hard spend constraint | `get_budget` |
| Projection, requirement, constraint checks per supplier | The arithmetic, done deterministically | `project_inventory`, `evaluate_supplier_options` |

## 3. How decisions are made

**Demand.** Recent sales are cleaned of promotion days and outliers (> 2.5× median). If the clean 7-day average is
> 1.25× forecast and ≥ 5 of 7 clean days are above forecast, demand has *shifted*, so plan with the recent rate.
If the raw average is high but the clean one isn't, it was a *transient spike*, so plan with the forecast.
Three or more consecutive zero-sales days against a positive forecast mean the data can't be trusted.

**Requirement.** Order-up-to policy:
`target = rate × (lead time + 7-day review period) + rate × safety-stock days`;
`need = target − (on hand + confirmed inbound)`, rounded up to case pack and MOQ.
A day-by-day projection with lost sales (quick-commerce customers don't wait) checks when stock-outs happen
and whether storage overflows at each receipt.

**Levers, in order of preference:**

| Situation | Lever |
|---|---|
| Need is zero | Reject the recommendation, or accept the current plan unchanged |
| Demand rose, open PO not shipped, supplier accepts changes | Increase that PO; it lands sooner than a new one |
| Need fits one supplier's constraints | One PO with the cheapest eligible supplier that arrives before the stock-out |
| Storage too small for one receipt | Split into staggered deliveries that each fit |
| Budget too small | Buy the largest affordable case multiple and escalate the gap |
| Primary MOQ exceeds shelf-life cover | Use an alternate with a smaller MOQ within the price-premium cap |
| Supplier fell short | Count only confirmed qty; check if stock suffices; never re-order from the one that failed |
| Nothing eligible, or stock-out unavoidable | Escalate with the evaluated options |
| Data unreliable | Investigate: request a cycle count, don't buy |

**Verdicts** map onto the brief: `accept`, `modify`, `reject`, `investigate`, plus `escalate` when a human
must decide.

## 4. Constraints and uncertainty

- **Hard constraints** are enforced by the validator and cannot be argued away: supplier active, MOQ,
  case pack, lead time, ≤ 25% premium over the primary supplier, budget, storage at every receipt, shelf life,
  no over-buying, PO changeability, and data quality.
- **Residual risk must be surfaced.** If the plan still leaves a projected stock-out (e.g. budget-limited),
  the validator *requires* an `escalate` action. The agent cannot silently under-deliver.
- **Supplier uncertainty** is modelled explicitly: catalogue data does not reveal live availability, so the
  system must act, observe and adapt.
- **Data uncertainty**: when inputs are suspicious, the right output is a request for better data, not a number.
- **Model uncertainty**: a decision flagged `confidence: low` that commits money goes to a human.

## 5. When humans are involved

| Trigger | Mechanism |
|---|---|
| Commitment > `AUTO_APPROVE_MAX_PO_VALUE` ($5,000) | Approval queue; re-validated at execution time |
| Cancelling or reducing a confirmed PO | Approval queue |
| Low-confidence purchase | Approval queue |
| Budget exception, no eligible supplier, unavoidable stock-out | Escalation with options |
| Unreliable inventory / sales data | Cycle-count request + escalation |
| Agent can't converge within `MAX_REVISIONS` | Automatic escalation with the last feedback |

## 6. Feedback loop and validation

There are two independent feedback points:

1. **Before acting (validation).** The proposal is checked against the recomputed world. Violations go back to
   the agent verbatim ("Projected storage overflow 90 units (slot capacity 900)"), and it must revise.
2. **After acting (verification).** The verifier re-reads the POs and budget from the ERP and checks: confirmed
   quantity = requested, ETA ≤ planned, budget integrity, storage, and coverage over the planning cycle. If
   anything differs, the discrepancy (with what was requested vs. what the supplier confirmed, and the residual
   requirement) goes back to the agent. Executed actions stay committed, so the agent re-plans only the
   remaining gap and excludes suppliers that just failed.

CASE-S2-C shows the whole loop. The first alternate supplier looks perfect in the catalogue but confirms only
60 of 150. Verification flags `PO_CONFIRMED` and `COVERAGE`. The agent re-queries, excludes that supplier,
orders the remaining 50 from another, and the second verification passes.

## 7. Additional buyer problems handled by the same architecture

- **Forecast anomalies / phantom inventory** (CASE-S1-D): zero-sales streak detection leads to a cycle count.
- **Promotional buying** (CASE-S3-B): ended promotions are excluded from the demand signal.
- **Alternate suppliers & supplier reliability** (S2-B/C/D): blocked suppliers, price caps, partial confirmations.
- **Safety stock / shelf life / storage-aware replenishment** (S4-B/C).

## 8. What I would do next

- Probabilistic demand (quantiles instead of a point rate), with safety stock driven by service level.
- Supplier reliability (fill rate, on-time rate) feeding into sourcing choice and expected confirmed quantity.
- Multi-SKU budget allocation: prioritise items by stock-out cost when the budget can't cover everything.
- Async runs with streaming trace updates; persist the LLM conversation for long approval waits.
- LLM-as-judge on the quality of the written rationale, alongside the deterministic graders.
