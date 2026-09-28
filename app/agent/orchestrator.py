"""The closed-loop controller around the agent.

    ┌──────────► agent.decide(feedback) ──► Decision
    │                                          │
    │                            evidence gate (required tools called?)
    │                                          │
    │                         validator (hard business rules, recomputed)
    │   feedback: violations  ◄──── fail ──────┤
    │                                          │ pass
    │                               policy gate (autonomy limits)
    │                                          ├──── needs human ──► approval queue (pause)
    │                                          │
    │                               executor (ERP + supplier APIs)
    │                                          │
    │                         verifier (re-read ERP, expected vs actual)
    └── feedback: discrepancies ◄── fail ──────┤
                                               │ pass
                                           completed

The loop is bounded by MAX_REVISIONS; if the agent cannot reach a verified
outcome, the orchestrator escalates to a human rather than guessing.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from app.agent.rules import RuleBasedAgent
from app.agent.tools import ToolBox, Trace
from app.config import get_settings
from app.db import dumps
from app.executor import Executor
from app.models import Action, Decision
from app.policy import approval_reasons
from app.repository import Repository
from app.validator import validate_decision
from app.verifier import verify_outcome


def make_agent(mode: str):
    if mode == "rules":
        return RuleBasedAgent()
    if mode == "llm":
        from app.agent.llm import LLMAgent
        return LLMAgent()
    raise ValueError(f"unknown agent mode {mode!r}")


class Orchestrator:
    def __init__(self, conn, mode: str = "rules", agent=None):
        self.conn = conn
        self.repo = Repository(conn)
        self.mode = mode
        self.agent = agent or make_agent(mode)
        self.max_rounds = get_settings().max_revisions + 1

    # ------------------------------------------------------------------------------------
    def run(self, case_id: str) -> dict:
        case = self.repo.get_case(case_id)
        if case is None:
            raise ValueError(f"unknown case {case_id}")
        run_id = f"RUN-{uuid.uuid4().hex[:8]}"
        self.conn.execute(
            "INSERT INTO agent_runs (id, case_id, mode, status, started_at) VALUES (?,?,?,?,?)",
            (run_id, case_id, self.mode, "running", datetime.now().isoformat()))
        self.conn.commit()
        trace = Trace()
        trace.add("start", f"Agent ({self.mode}) started on {case_id}", {"case": case["title"]})
        tools = ToolBox(self.repo, case, trace)
        return self._loop(run_id, case, tools, trace, feedback=None, executed=[], round_offset=0)

    def _loop(self, run_id, case, tools, trace, feedback, executed, round_offset) -> dict:
        decision: Decision | None = None
        status = "running"
        failed_suppliers: list[str] = list((feedback or {}).get("failed_suppliers", []))
        rounds = 0
        for rnd in range(1, self.max_rounds + 1):
            rounds = rnd
            trace.add("round", f"Round {rnd + round_offset}", {"feedback": feedback})
            try:
                decision = self.agent.decide(tools, feedback)
            except Exception as e:
                trace.add("error", "Agent failed to produce a decision", {"error": f"{type(e).__name__}: {e}"})
                status = "failed"
                break
            trace.add("decision", f"Proposed: {decision.verdict}", decision.model_dump())

            # Gate 1: evidence -----------------------------------------------------------------
            missing = tools.missing_evidence()
            if missing:
                feedback = {"type": "missing_evidence", "missing_tools": missing,
                            "message": "Decision rejected: gather this evidence first: " + ", ".join(missing)}
                trace.add("evidence_gate", "Evidence gate failed", feedback, ok=False)
                continue
            trace.add("evidence_gate", "Evidence gate passed", {"tools_called": sorted(tools.called)}, ok=True)

            # Gate 2: validation ---------------------------------------------------------------------
            report = validate_decision(self.repo, case, decision)
            trace.add("validation", "Validation passed" if report.ok else
                      f"Validation failed ({len(report.violations)} violation(s))", report.as_dict(), ok=report.ok)
            if not report.ok:
                feedback = {"type": "validation_failed",
                            "violations": [v.message for v in report.violations],
                            "failed_suppliers": failed_suppliers,
                            "message": "Your proposal violates business rules; revise it."}
                continue

            # Gate 3: autonomy policy --------------------------------------------------------------
            reasons = approval_reasons(self.repo, decision, report.total_value)
            if reasons:
                approval_id = f"APR-{uuid.uuid4().hex[:8]}"
                self.conn.execute(
                    "INSERT INTO approvals (id, run_id, case_id, actions, reasons, status) VALUES (?,?,?,?,?,?)",
                    (approval_id, run_id, case["id"], dumps([a.model_dump() for a in decision.actions]),
                     dumps(reasons), "pending"))
                trace.add("policy", "Human approval required", {"approval_id": approval_id, "reasons": reasons},
                          ok=None)
                status = "awaiting_approval"
                break
            trace.add("policy", "Within autonomy limits — executing", {"total_value": report.total_value}, ok=True)

            # Execute + verify ------------------------------------------------------------------------
            status, feedback = self._execute_and_verify(run_id, case, decision, trace, executed, failed_suppliers)
            if status != "replan":
                break
        else:
            status = "replan"

        if status == "replan" or (status == "running"):
            # Out of attempts without a verified outcome: hand over to a human, never guess.
            msg = (f"Agent could not reach a validated, verified plan after {rounds} round(s). "
                   f"Last feedback: {(feedback or {}).get('message', '')} "
                   + "; ".join((feedback or {}).get("violations", []) +
                               [d["message"] for d in (feedback or {}).get("discrepancies", [])]))
            executed.extend(Executor(self.conn, run_id).execute(case, [Action(type="escalate", reason=msg)]))
            trace.add("escalation", "Loop exhausted — escalated to buyer", {"message": msg}, ok=False)
            status = "escalated"

        return self._finish(run_id, decision, executed, status, trace)

    def _execute_and_verify(self, run_id, case, decision, trace, executed, failed_suppliers):
        results = Executor(self.conn, run_id).execute(case, decision.actions)
        executed.extend(results)
        trace.add("execution", f"Executed {len(results)} action(s)", results, ok=True)
        escalated = any(r["action"]["type"] in ("escalate", "request_cycle_count") for r in executed)
        # The verifier uses the system's own demand estimate, never the agent's.
        ver = verify_outcome(self.repo, case, results, tools_rate(self.repo, case), escalated)
        trace.add("verification", "Outcome verified" if ver.ok else
                  f"Verification found {len(ver.discrepancies)} discrepancy(ies)", ver.as_dict(), ok=ver.ok)
        if ver.ok:
            return ("completed_with_escalation" if escalated else "completed"), None
        for r in results:
            if r["action"]["type"] == "create_po" and r["actual"]["qty"] < r["expected"]["qty"]:
                failed_suppliers.append(r["action"]["supplier_id"])
        feedback = {"type": "verification_failed",
                    "discrepancies": ver.discrepancies,
                    "residual_requirement": ver.residual_requirement,
                    "failed_suppliers": sorted(set(failed_suppliers)),
                    "executed": [{"po_id": r.get("po_id"), "expected": r["expected"], "actual": r["actual"],
                                  "supplier_message": r.get("supplier_message")} for r in results],
                    "message": "Executed actions did not produce the expected outcome. The actions above are "
                               "already committed. Re-plan from the current state."}
        return "replan", feedback

    def _finish(self, run_id, decision, executed, status, trace) -> dict:
        trace.add("final", f"Run finished: {status}", {"status": status})
        self.conn.execute(
            "UPDATE agent_runs SET status=?, finished_at=?, decision=?, trace=? WHERE id=?",
            (status, datetime.now().isoformat(),
             dumps({"final": decision.model_dump() if decision else None, "executed": executed}),
             dumps(trace.steps), run_id))
        self.conn.commit()
        return self.repo.get_run(run_id)

    # --- human-in-the-loop -----------------------------------------------------------------------
    def resolve_approval(self, approval_id: str, approve: bool, approver: str = "buyer",
                         comment: str = "") -> dict:
        apr = self.repo.get_approval(approval_id)
        if apr is None or apr["status"] != "pending":
            raise ValueError("approval not found or already decided")
        self.conn.execute(
            "UPDATE approvals SET status=?, decided_by=?, decided_at=?, comment=? WHERE id=?",
            ("approved" if approve else "rejected", approver, datetime.now().isoformat(), comment, approval_id))
        run = self.repo.get_run(apr["run_id"])
        case = self.repo.get_case(apr["case_id"])
        trace = Trace()
        trace.steps = run["trace"]
        prior = run["decision"] or {}
        decision = Decision(**prior["final"]) if prior.get("final") else None
        executed = prior.get("executed", [])
        if not approve:
            trace.add("human", f"Buyer {approver} rejected the proposal", {"comment": comment}, ok=False)
            return self._finish(apr["run_id"], decision, executed, "rejected_by_human", trace)

        trace.add("human", f"Buyer {approver} approved the proposal", {"comment": comment}, ok=True)
        # Re-validate: the world may have changed while the approval was pending.
        report = validate_decision(self.repo, case, decision)
        trace.add("validation", "Re-validation at execution time " + ("passed" if report.ok else "failed"),
                  report.as_dict(), ok=report.ok)
        if not report.ok:
            return self._finish(apr["run_id"], decision, executed, "stale_approval", trace)
        failed: list[str] = []
        status, feedback = self._execute_and_verify(apr["run_id"], case, decision, trace, executed, failed)
        if status == "replan":
            tools = ToolBox(self.repo, case, trace)
            return self._loop(apr["run_id"], case, tools, trace, feedback, executed, round_offset=1)
        return self._finish(apr["run_id"], decision, executed, status, trace)


def tools_rate(repo: Repository, case: dict) -> float:
    from app.context import demand_signal
    return demand_signal(repo, case["sku_id"], case["node_id"])["recommended_daily_rate"]
