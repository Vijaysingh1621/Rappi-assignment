"""Graders: turn a finished run into pass/fail checks along six dimensions.

1. decision     – verdict and final status are in the expected set
2. evidence     – the agent consulted the required data before deciding
3. constraints  – nothing executed violated validation; final state is within budget/storage
4. action       – the right actions were taken (types, suppliers, quantities)
5. validation   – every execution was followed by an outcome verification
6. recovery     – when an action didn't work, the agent detected it and re-planned
"""
from app.agent.tools import REQUIRED_EVIDENCE


def purchased_qty(run: dict) -> int:
    total = 0
    for e in run["decision"]["executed"]:
        a = e["action"]
        if a["type"] == "create_po":
            total += e["actual"]["qty"] or 0
        elif a["type"] == "modify_po":
            prior = _prior_qty(run, a["po_id"])
            total += (e["actual"]["qty"] or 0) - prior
    return total


def _prior_qty(run: dict, po_id: str) -> int:
    for s in run["trace"]:
        if s["kind"] == "tool_call" and s["title"] == "get_open_purchase_orders":
            for po in s["data"]["output"]:
                if po["id"] == po_id:
                    return po["inbound_qty"]
    return 0


def planned_qty(run: dict) -> int:
    """For runs paused before execution: quantity in the proposed decision."""
    final = run["decision"]["final"] or {}
    return sum(a.get("quantity") or 0 for a in final.get("actions", []) if a["type"] == "create_po")


def grade(run: dict, exp: dict) -> dict[str, tuple[bool, str]]:
    final = run["decision"]["final"] or {}
    executed = run["decision"]["executed"]
    trace = run["trace"]
    tools_called = {s["title"] for s in trace if s["kind"] == "tool_call"}
    action_types = {e["action"]["type"] for e in executed} | {a["type"] for a in final.get("actions", [])}
    suppliers = {e["action"].get("supplier_id") for e in executed if e["action"]["type"] == "create_po"}
    if run["status"] == "awaiting_approval":
        suppliers |= {a.get("supplier_id") for a in final.get("actions", []) if a["type"] == "create_po"}
    qty = planned_qty(run) if run["status"] == "awaiting_approval" else purchased_qty(run)
    checks: dict[str, tuple[bool, str]] = {}

    # 1. decision ---------------------------------------------------------------------------
    checks["decision"] = (final.get("verdict") in exp["verdicts"] and run["status"] in exp["status"],
                          f"verdict={final.get('verdict')} status={run['status']} "
                          f"(expected {sorted(exp['verdicts'])} / {sorted(exp['status'])})")

    # 2. evidence ---------------------------------------------------------------------------
    missing = [t for t in REQUIRED_EVIDENCE if t not in tools_called]
    checks["evidence"] = (not missing, "all required data consulted" if not missing else f"missing {missing}")

    # 3. constraints ------------------------------------------------------------------------
    problems = []
    validations = [s for s in trace if s["kind"] == "validation"]
    executions = [s for s in trace if s["kind"] == "execution"]
    for ex in executions:
        prior = [v for v in validations if v["n"] < ex["n"]]
        if not prior or not prior[-1]["ok"]:
            problems.append(f"execution at step {ex['n']} without passing validation")
    for v in [s for s in trace if s["kind"] == "verification"]:
        for c in v["data"]["checks"]:
            if c["code"] in ("BUDGET_INTEGRITY", "STORAGE_OK") and not c["passed"]:
                problems.append(c["message"])
    bad_sup = suppliers & exp.get("forbidden_suppliers", set())
    if bad_sup:
        problems.append(f"ordered from forbidden supplier(s) {sorted(bad_sup)}")
    checks["constraints"] = (not problems, "; ".join(problems) or "all executed actions validated, state within limits")

    # 4. action -----------------------------------------------------------------------------
    lo, hi = exp["purchase_qty"]
    issues = []
    if not lo <= qty <= hi:
        issues.append(f"purchased {qty} (expected {lo}-{hi})")
    allowed = exp.get("allowed_suppliers")
    if allowed and suppliers - allowed:
        issues.append(f"suppliers {sorted(suppliers - allowed)} not in {sorted(allowed)}")
    for req in exp.get("required_actions", set()):
        if req not in action_types:
            issues.append(f"missing action {req}")
    pos = [e for e in executed if e["action"]["type"] == "create_po"] or \
          [a for a in final.get("actions", []) if a["type"] == "create_po"]
    if len(pos) < exp.get("min_po_count", 0):
        issues.append(f"{len(pos)} PO(s), expected ≥{exp['min_po_count']} (split delivery)")
    checks["action"] = (not issues, "; ".join(issues) or f"purchased {qty} units from {sorted(suppliers) or '—'}")

    # 5. validation of result ----------------------------------------------------------------
    unverified = [ex["n"] for ex in executions
                  if not any(s["kind"] == "verification" and s["n"] > ex["n"] for s in trace)]
    checks["validation"] = (not unverified, "every execution verified against the ERP"
                            if not unverified else f"executions {unverified} never verified")

    # 6. recovery ----------------------------------------------------------------------------
    ver = [s["ok"] for s in trace if s["kind"] == "verification"]
    if exp.get("must_recover"):
        recovered = False in ver and ver[-1] is True
        checks["recovery"] = (recovered, f"verification sequence {ver}")
    else:
        failed_then_fixed = (False not in ver) or (ver and ver[-1] is True) or run["status"] == "escalated"
        checks["recovery"] = (bool(failed_then_fixed), f"verification sequence {ver or '—'}")
    return checks
