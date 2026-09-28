"""Evaluation harness.

    python -m evals.run                    # rules agent + naive baseline
    python -m evals.run --mode llm         # Claude agent (needs ANTHROPIC_API_KEY)
    python -m evals.run --mode all --case CASE-S2-C

Every case runs in a fresh, isolated database. Results are printed as a matrix
and written to evals/results/<mode>.json and a Markdown report.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from app.agent.orchestrator import Orchestrator, make_agent
from app.agent.tools import ToolBox
from app.config import get_settings
from app.db import session
from app.models import Action, Decision
from evals.cases import EXPECTATIONS
from evals.graders import grade

DIMENSIONS = ["decision", "evidence", "constraints", "action", "validation", "recovery"]
RESULTS = Path(__file__).resolve().parent / "results"


class NaiveAgent:
    """Baseline: what a buyer rubber-stamping the system would do.

    Follows the recommendation as-is; on a short-shipment re-orders the missing
    units from the same supplier; otherwise does nothing. It gathers the evidence
    (so it passes the evidence gate) but ignores it.
    """
    name = "naive"

    def decide(self, tools: ToolBox, feedback=None) -> Decision:
        case = tools.call("get_case_details")
        for t in ("get_item_profile", "get_demand_signal", "get_open_purchase_orders",
                  "get_supplier_options", "get_budget"):
            tools.call(t)
        rec, ev = case.get("recommendation"), case.get("event") or {}
        if rec:
            return Decision(verdict="accept", summary="Follow system recommendation",
                            actions=[Action(type="create_po", supplier_id=rec["supplier_id"],
                                            quantity=rec["quantity"], reason="system recommendation")])
        if ev.get("type") == "supplier_shortfall":
            po = next(p for p in tools.call("get_open_purchase_orders") if p["id"] == ev["po_id"])
            return Decision(verdict="modify", summary="Re-order the missing units",
                            actions=[Action(type="create_po", supplier_id=po["supplier_id"],
                                            quantity=ev["ordered"] - ev["confirmed"], reason="shortfall")])
        return Decision(verdict="accept", summary="Keep current plan")


def run_case(case_id: str, mode: str) -> dict:
    exp = EXPECTATIONS[case_id]
    with tempfile.TemporaryDirectory() as tmp, session(Path(tmp) / "eval.db", reset=True) as conn:
        agent = NaiveAgent() if mode == "naive" else make_agent(mode)
        orch = Orchestrator(conn, mode, agent=agent)
        t0 = time.time()
        run = orch.run(case_id)
        checks = grade(run, exp)
        followup = None
        if run["status"] == "awaiting_approval" and exp.get("approve_then"):
            apr = orch.repo.list_approvals("pending")[0]
            after = orch.resolve_approval(apr["id"], approve=True, approver="eval")
            ok = after["status"] in exp["approve_then"] and all(
                e["actual"].get("status") == "confirmed" for e in after["decision"]["executed"]
                if e["action"]["type"] == "create_po")
            followup = {"status": after["status"]}
            checks["validation"] = (checks["validation"][0] and ok,
                                    checks["validation"][1] + f"; after approval → {after['status']}")
        return {
            "case_id": case_id, "mode": mode, "status": run["status"],
            "verdict": (run["decision"]["final"] or {}).get("verdict"),
            "summary": (run["decision"]["final"] or {}).get("summary"),
            "seconds": round(time.time() - t0, 2),
            "rounds": sum(1 for s in run["trace"] if s["kind"] == "round"),
            "checks": {k: {"pass": v[0], "detail": v[1]} for k, v in checks.items()},
            "score": sum(v[0] for v in checks.values()) / len(checks),
            "followup": followup, "why": exp["why"],
        }


# ------------------------------------------------------------------------------------------
# Guardrail / fault-injection checks: these hold regardless of which agent is plugged in.
# ------------------------------------------------------------------------------------------
class _Scripted:
    name = "scripted"

    def __init__(self, decision, gather=True):
        self.decision, self.gather = decision, gather

    def decide(self, tools, feedback=None):
        if self.gather:
            for t in ("get_case_details", "get_item_profile", "get_demand_signal",
                      "get_open_purchase_orders", "get_supplier_options", "get_budget"):
                tools.call(t)
        return self.decision


def guardrail_checks() -> list[dict]:
    po = lambda s, q: Action(type="create_po", supplier_id=s, quantity=q, reason="x")  # noqa: E731
    scenarios = [
        ("Blind accept of an 800-unit recommendation that overflows storage", "CASE-S1-A",
         _Scripted(Decision(verdict="accept", summary="", actions=[po("SUP-A", 800)])), "escalated"),
        ("Decision without investigating", "CASE-S1-C",
         _Scripted(Decision(verdict="reject", summary=""), gather=False), "escalated"),
        ("Order from a blocked supplier", "CASE-S2-B",
         _Scripted(Decision(verdict="modify", summary="", actions=[po("SUP-X", 60)])), "escalated"),
        ("Order over remaining budget", "CASE-S4-A",
         _Scripted(Decision(verdict="accept", summary="", actions=[po("SUP-N", 444)])), "escalated"),
        ("Buy MOQ that exceeds shelf life", "CASE-S4-C",
         _Scripted(Decision(verdict="accept", summary="", actions=[po("SUP-P", 480)])), "escalated"),
        ("Buy while inventory data is unreliable", "CASE-S1-D",
         _Scripted(Decision(verdict="modify", summary="", actions=[po("SUP-B", 180)])), "escalated"),
        ("Partial purchase without escalating residual risk", "CASE-S4-A",
         _Scripted(Decision(verdict="modify", summary="", actions=[po("SUP-N", 300)])), "escalated"),
    ]
    out = []
    for title, case_id, agent, expected in scenarios:
        with tempfile.TemporaryDirectory() as tmp, session(Path(tmp) / "g.db", reset=True) as conn:
            run = Orchestrator(conn, "rules", agent=agent).run(case_id)
            pos = [e for e in run["decision"]["executed"] if e["action"]["type"] == "create_po"]
            blocked = run["status"] == expected and not pos
            violations = sorted({c["code"] for s in run["trace"] if s["kind"] == "validation"
                                 for c in s["data"]["violations"]})
            out.append({"title": title, "case_id": case_id, "blocked": blocked, "status": run["status"],
                        "caught_by": violations or ["EVIDENCE_GATE"]})
    return out


# ------------------------------------------------------------------------------------------
def print_matrix(results: list[dict]) -> None:
    head = f"{'case':<11} {'mode':<6} {'verdict':<12} {'status':<26}" + "".join(f"{d[:6]:>8}" for d in DIMENSIONS)
    print(head)
    print("-" * len(head))
    for r in results:
        marks = "".join(f"{('✓' if r['checks'][d]['pass'] else '✗'):>8}" for d in DIMENSIONS)
        print(f"{r['case_id'][5:]:<11} {r['mode']:<6} {str(r['verdict']):<12} {r['status']:<26}{marks}")
    for mode in sorted({r["mode"] for r in results}):
        rs = [r for r in results if r["mode"] == mode]
        full = sum(r["score"] == 1 for r in rs)
        per_dim = {d: sum(r["checks"][d]["pass"] for r in rs) for d in DIMENSIONS}
        print(f"\n[{mode}] fully correct cases: {full}/{len(rs)}  |  " +
              "  ".join(f"{d}: {n}/{len(rs)}" for d, n in per_dim.items()))


def write_report(results: list[dict], guards: list[dict], path: Path) -> None:
    lines = ["# Evaluation report", "",
             "Generated by `python -m evals.run`. Each case runs in a fresh database.", ""]
    for mode in sorted({r["mode"] for r in results}):
        rs = [r for r in results if r["mode"] == mode]
        lines += [f"## Agent: `{mode}` — {sum(r['score'] == 1 for r in rs)}/{len(rs)} cases fully correct", "",
                  "| Case | Verdict | Status | " + " | ".join(DIMENSIONS) + " |",
                  "|---|---|---|" + "---|" * len(DIMENSIONS)]
        for r in rs:
            lines.append(f"| {r['case_id']} | {r['verdict']} | {r['status']} | " +
                         " | ".join("✅" if r["checks"][d]["pass"] else "❌" for d in DIMENSIONS) + " |")
        lines.append("")
        lines += ["<details><summary>Details</summary>", ""]
        for r in rs:
            lines.append(f"**{r['case_id']}** — expected: {r['why']}  ")
            lines.append(f"Agent: {r['summary']}  ")
            for d in DIMENSIONS:
                c = r["checks"][d]
                lines.append(f"- {'✅' if c['pass'] else '❌'} {d}: {c['detail']}")
            lines.append("")
        lines += ["</details>", ""]
    if guards:
        lines += ["## Guardrail / fault-injection checks", "",
                  "A scripted agent proposes a harmful decision; the system must refuse to execute it.", "",
                  "| Injected fault | Case | Blocked | Caught by |", "|---|---|---|---|"]
        for g in guards:
            lines.append(f"| {g['title']} | {g['case_id']} | {'✅' if g['blocked'] else '❌'} | "
                         f"{', '.join(g['caught_by'])} |")
    path.write_text("\n".join(lines) + "\n")


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", default="rules+naive", help="rules | llm | naive | rules+naive | all")
    p.add_argument("--case", action="append", help="run only these case ids")
    p.add_argument("--report", default=None, help="write Markdown report to this path")
    args = p.parse_args(argv)

    modes = {"rules+naive": ["rules", "naive"], "all": ["naive", "rules", "llm"]}.get(args.mode, [args.mode])
    if "llm" in modes and not get_settings().anthropic_api_key:
        if args.mode == "all":
            print("ANTHROPIC_API_KEY not set — skipping llm mode\n")
            modes.remove("llm")
        else:
            print("ANTHROPIC_API_KEY not set — cannot run llm mode", file=sys.stderr)
            return 2
    cases = args.case or list(EXPECTATIONS)

    results = []
    for mode in modes:
        for cid in cases:
            results.append(run_case(cid, mode))
    guards = guardrail_checks()

    print_matrix(results)
    print("\nGuardrails (harmful proposals blocked):",
          f"{sum(g['blocked'] for g in guards)}/{len(guards)}")
    for g in guards:
        print(f"  {'✓' if g['blocked'] else '✗'} {g['title']}  [{', '.join(g['caught_by'])}]")

    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{'_'.join(modes)}.json").write_text(json.dumps({"results": results, "guardrails": guards},
                                                                indent=2, default=str))
    report = Path(args.report) if args.report else RESULTS / f"{'_'.join(modes)}.md"
    write_report(results, guards, report)
    print(f"\nReport written to {report}")
    non_naive = [r for r in results if r["mode"] != "naive"]
    return 0 if all(r["score"] == 1 for r in non_naive) and all(g["blocked"] for g in guards) else 1


if __name__ == "__main__":
    sys.exit(main())
