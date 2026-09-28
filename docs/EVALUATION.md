# Evaluation

## How to run

```bash
python -m evals.run                              # rules agent + naive baseline + guardrails
python -m evals.run --mode llm                   # Claude agent (needs ANTHROPIC_API_KEY)
python -m evals.run --mode all                   # naive vs rules vs llm
python -m evals.run --case CASE-S2-C --mode llm  # a single case
pytest -q                                        # 48 unit / integration tests
```

Each case runs in a **fresh database**, so results are independent and reproducible. The runner prints a matrix,
writes JSON + Markdown to `evals/results/`, and exits non-zero if any non-baseline case or guardrail fails, so
it can gate CI.

## What is graded

Every run is graded on the six questions from the brief. All graders read the **run trace and ERP state**,
never the agent's own claims.

| Dimension | Question | How it is checked |
|---|---|---|
| decision | Was the decision correct? | Verdict ∈ expected set **and** final run status ∈ expected set |
| evidence | Did it obtain the necessary information? | All 6 required read tools appear in the trace before the decision |
| constraints | Did it respect relevant constraints? | Every execution was preceded by a *passing* validation; post-execution budget and storage checks pass; no forbidden supplier used |
| action | Did it take the appropriate action? | Purchased quantity (supplier-**confirmed**) in expected range; suppliers in allowed set; required action types present (e.g. `modify_po`, `request_cycle_count`, `escalate`); minimum PO count for split deliveries |
| validation | Did it validate the result? | Every execution is followed by a verification step; for approval cases, the eval approves and checks the post-approval run completes with confirmed POs |
| recovery | What happens when the initial action does not work? | For S2-C the verification sequence must be `[fail, …, pass]`; elsewhere, no run may end on a failed verification |

Expectations are **ranges and properties**, not exact numbers, because several answers are defensible (e.g. the
faster alternate supplier needs a smaller quantity). They were derived by hand from the seed data (below),
before looking at agent output.

## Test scenarios and expected outcomes

Planning conventions: review period 7 days; target = rate × (lead + 7) + rate × safety days.

| Case | Situation | Hand calculation | Expected |
|---|---|---|---|
| **S1-A** | Rec: 800 Coca-Cola | rate 40, L 4, SS 3d → target 560; position 150 + 200 inbound = 350 → need 210 → **216** (case 24). 800 would put 990 units in a 900 slot | `modify`, 120–240 units |
| **S1-B** | Rec: 800 water | rate 90, L 5 → target 1,350; position 280 + 270 = 550 → need **800** | `accept` 800 from SUP-C |
| **S1-C** | Rec: 800 rice | 700 on hand at 20/day = 35 days; target 280 | `reject`, buy nothing |
| **S1-D** | Rec: 800 milk | 480 "on hand" but 5 straight days of 0 sales vs 60/day forecast; last count 21 days ago → data untrustworthy | `investigate` + cycle count, buy nothing |
| **S2-A** | 250 of 500 Doritos | 600 + 250 = 850 at 25/day = 34 days ≫ target 325 | `accept`, no purchase |
| **S2-B** | 250 of 500 Nescafé | Primary has no stock for 3 weeks; cheapest alternate SUP-X is **blocked**; stock-out projected on day 3 before PO lands day 4 | `modify`: buy from SUP-F/SUP-G, never SUP-X/SUP-E |
| **S2-C** | 250 of 500 diapers | Gap ~150 with SUP-I (L 5). **SUP-I can really ship only 60** (hidden) → verification fails → remaining ~50 from SUP-J | `modify`, 100–250 units, must show recovery |
| **S2-D** | 250 of 500 olive oil | Stock-out day 2, PO lands day 5; only alternate SUP-K is +45% (cap 25%) and 9-day lead | `escalate`, buy nothing |
| **S3-A** | Electrolit surge | Clean 7-day avg 91.3 vs forecast 50 (7/7 days above) → sustained. At 91.3/day need ≈350; PO-7001 not shipped, supplier accepts changes | `modify_po` +300–420 |
| **S3-B** | Beer spike | Raw 7-day avg 64.9 vs 30 forecast, but 3 of those days were a 3x2 promo that ended; clean avg 31.8 → no change. (Naively using 64.9 would buy ~300 extra) | `accept`, no purchase |
| **S3-C** | Olive oil surge | Sustained 74.6/day; PO-7003 in transit and supplier refuses changes → new PO ≈350 × $15 = $5,250 > $5,000 limit | `modify`, `awaiting_approval`; completes after approval |
| **S4-A** | Rec: 444 shampoo | Need 440 → 444 = $1,865, budget left $1,300 → max 300 units | `modify` 240–300 + `escalate` the gap |
| **S4-B** | Rec: 260 toilet paper | Slot 200; ~10 on hand at arrival → one delivery overflows. Split 190 (day 3) + 70 (day 6) | `modify`, ≥2 POs, 240–280 units |
| **S4-C** | Rec: 480 yogurt | Need ~68; primary MOQ 480 = 60 days vs 21-day shelf life. Alternate SUP-Q (+17%, MOQ 36) | `modify`: 36–96 from SUP-Q, never SUP-P |

## Baseline: why the agent is needed

`NaiveAgent` models a buyer who rubber-stamps the system: it follows every recommendation as-is and re-orders
short-shipped units from the same supplier. The same guardrails wrap it, so it **cannot** execute its bad
orders. They are blocked and escalated, which shows up as failed decisions rather than damage.

## Guardrail / fault-injection checks

These run a scripted agent that proposes a specific harmful decision, to prove the safety net holds whichever
agent is plugged in (including a misbehaving LLM):

| Injected fault | Must be caught by |
|---|---|
| Blindly accept an 800-unit rec that overflows storage | `STORAGE`, `NO_OVERBUY` |
| Decide without investigating | Evidence gate |
| Order from a blocked supplier | `SUPPLIER_ACTIVE` |
| Order over remaining budget | `BUDGET` |
| Buy an MOQ exceeding shelf life | `SHELF_LIFE` |
| Buy while inventory data is unreliable | `DATA_QUALITY` |
| Partial purchase without escalating the residual stock-out risk | `COVERAGE` |

In every case the expected outcome is: no PO created, run status `escalated`.

## Results (rules agent, deterministic)

From [eval_report_rules.md](eval_report_rules.md):

| Agent | Fully correct cases | decision | evidence | constraints | action | validation | recovery |
|---|---|---|---|---|---|---|---|
| rules | **14 / 14** | 14 | 14 | 14 | 14 | 14 | 14 |
| naive baseline | 2 / 14 | 2 | 14 | 13 | 5 | 14 | 13 |

Guardrails: **7 / 7** harmful proposals blocked.

The Claude agent is evaluated with the same harness (`--mode llm`). Because LLM output varies between runs, run
it several times and compare per-dimension pass rates against the rules baseline. The deterministic
validator/verifier guarantee that an LLM mistake degrades into a *revision* or an *escalation*, never an
invalid PO.

## How decisions are validated at runtime (not just in evals)

The same machinery the evals rely on runs on every live decision:

1. **Evidence gate**: the decision is refused if required data wasn't consulted.
2. **Validator**: 19 checks recomputed from the ERP (see `app/validator.py`); violations are fed back.
3. **Policy gate**: value and irreversibility thresholds route to a human.
4. **Verifier**: after execution, re-reads the ERP; discrepancies trigger re-planning.
5. **Bounded loop**: after `MAX_REVISIONS`, escalate with the last feedback rather than guess.

## Test suite (`pytest`)

| File | Covers |
|---|---|
| `tests/test_planning.py` | Demand classification (stable / surge / promo / outlier / zero-sales), projection, requirement, rounding, supplier evaluation, split deliveries |
| `tests/test_validator.py` | Each validation rule against the seeded cases; approval policy |
| `tests/test_execution.py` | Supplier partial confirmation / rejection / refused modification; budget commitment; verification discrepancies |
| `tests/test_orchestrator.py` | Re-planning after partial confirmation, approval pause/approve/reject, evidence gate, revision after validation feedback, escalation on loop exhaustion |
| `tests/test_llm_agent.py` | Claude agent protocol with a fake client: feedback returned as the `submit_decision` tool result, malformed decisions bounced, refusals handled |
| `tests/test_api.py` | HTTP API end-to-end including the approval flow |
