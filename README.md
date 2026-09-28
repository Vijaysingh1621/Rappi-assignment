# AI Purchasing Agent

A full-stack agent that takes a purchasing situation for a quick-commerce dark store, **investigates** inventory,
demand, open POs, suppliers, budget and storage, **decides** what to do, **executes** purchase-order actions and
**verifies** the outcome through a closed feedback loop, re-planning or escalating to a human when reality differs
from what it expected.

It is not a chatbot. The agent never writes to the ERP directly: it proposes a structured decision, and a
deterministic control loop validates, gates, executes and verifies it.

```
decide ─► evidence gate ─► validator ─► autonomy policy ─► execute ─► verify ─► done
   ▲             │              │               │                        │
   └─────────────┴── feedback ──┘         human approval        discrepancies ─┘ (re-plan)
```

| | |
|---|---|
| **Scenarios covered** | All four from the brief: 14 seeded cases (recommendation review, supplier short-shipment, demand change, purchasing constraints) |
| **Agents** | `llm`: Claude via tool use · `rules`: deterministic buyer playbook, which also serves as the offline baseline |
| **Evaluation** | 14 graded cases × 6 dimensions, a naive baseline, and 7 fault-injection guardrail checks |
| **Stack** | Python 3.11+, FastAPI, SQLite, Anthropic SDK, vanilla JS UI (no build step) |

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional: add ANTHROPIC_API_KEY to enable the Claude agent

uvicorn app.main:app --reload   # open http://localhost:8000
```

The database (`data/purchasing.db`) is created and seeded automatically. Use **Reset data** in the UI to restore
the seeded state.

```bash
pytest -q                        # 48 unit / integration tests (no API key needed)
python -m evals.run              # evaluate rules agent + naive baseline + guardrails
python -m evals.run --mode llm   # evaluate the Claude agent (needs ANTHROPIC_API_KEY)
python -m evals.run --mode all   # naive vs rules vs llm side by side
```

### Demo walkthrough (UI)

1. **CASE-S1-A**: the system recommends 800 Coca-Cola. The agent finds 200 units already inbound and that 800
   would overflow the 900-unit slot, so it **modifies** the order to 216 units.
2. **CASE-S2-C**: the supplier ships only 250 of 500. The agent orders the gap from an alternate supplier, which
   confirms only 60 of 150. **Verification catches it**, and the agent re-plans and sources the remainder from a third supplier.
3. **CASE-S3-B**: sales doubled last week, but it was an ended 3x2 promo, so there is **no change** and no over-reaction.
4. **CASE-S3-C**: a real demand surge. The open PO is already in transit, and the new $5,250 PO exceeds the autonomy limit, so it goes
   to the **Approvals** tab. Approving it executes the order and re-verifies it.
5. **CASE-S4-B**: 260 units needed but only ~190 fit on arrival, so the order is **split into two staggered deliveries**.
6. **CASE-S1-D**: 480 units "on hand" but zero sales for 5 days. That looks like phantom inventory, so the agent
   **investigates** (cycle count) instead of buying.

Each run shows the verdict, key factors, executed actions (requested vs. confirmed), and the full trace: every
tool call, validation check, policy decision and verification check.

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture diagram, components, data model, tools, design decisions |
| [docs/APPROACH.md](docs/APPROACH.md) | How the problem was broken down, the decision logic, and how constraints and uncertainty are handled |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Test scenarios, grading dimensions, how decisions are validated, results |
| [docs/eval_report_rules.md](docs/eval_report_rules.md) | Latest generated evaluation report |

## Repository layout

```
app/
  main.py            FastAPI app: REST API + static UI
  db.py, seed.py     SQLite schema and the mock ERP dataset (14 cases)
  repository.py      read API over the ERP
  planning.py        deterministic math: demand signal, projection, order-up-to, supplier evaluation
  context.py         builds the planning context for one SKU×node
  models.py          Decision / Action contract
  validator.py       pre-execution validation (hard business rules)
  policy.py          autonomy policy (when a human must approve)
  supplier_api.py    mock supplier system (partial confirmations, refusals)
  executor.py        executes actions against ERP + suppliers
  verifier.py        post-execution verification against the system of record
  agent/
    tools.py         scoped, traced tool surface + JSON schemas for the LLM
    llm.py           Claude tool-use agent
    rules.py         deterministic agent (baseline, offline mode)
    orchestrator.py  the closed feedback loop + human approval resume
frontend/            single-page UI
evals/               expectations, graders, runner, naive baseline, fault injection
tests/               pytest suite
```

## Configuration

See [.env.example](.env.example):

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Enables the Claude agent (`mode=llm`). Not needed for the rules agent, tests or evals. |
| `AGENT_MODEL` | `claude-opus-5` | Model used by the Claude agent |
| `DATABASE_PATH` | `data/purchasing.db` | SQLite file |
| `AUTO_APPROVE_MAX_PO_VALUE` | `5000` | Commitments above this (USD) need human approval |
| `MAX_REVISIONS` | `3` | Re-plans allowed before the loop escalates to a human |

## API

| Method | Path | |
|---|---|---|
| GET | `/api/cases`, `/api/cases/{id}` | Cases and full situation snapshot |
| POST | `/api/cases/{id}/run` `{"mode": "rules"\|"llm"}` | Run the agent; returns run + trace |
| GET | `/api/runs/{id}` | A run with its full trace |
| GET/POST | `/api/approvals`, `/api/approvals/{id}` `{"approve": true}` | Human-in-the-loop queue |
| GET | `/api/escalations`, `/api/purchase-orders` | Escalations and cycle-count requests; all POs |
| POST | `/api/reset` | Restore seed data |
