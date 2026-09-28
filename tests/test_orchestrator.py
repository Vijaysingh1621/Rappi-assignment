"""End-to-end loop behaviour: gates, feedback, re-planning, approvals."""
import pytest

from app.agent.orchestrator import Orchestrator
from app.agent.tools import ToolBox
from app.db import session
from app.models import Action, Decision


@pytest.fixture()
def conn(tmp_path):
    with session(tmp_path / "t.db", reset=True) as c:
        yield c


def kinds(run, kind):
    return [s for s in run["trace"] if s["kind"] == kind]


def test_partial_confirmation_triggers_replan(conn):
    run = Orchestrator(conn, "rules").run("CASE-S2-C")
    assert run["status"] == "completed"
    ver = kinds(run, "verification")
    assert [v["ok"] for v in ver] == [False, True]          # first outcome rejected, second verified
    suppliers = [e["action"]["supplier_id"] for e in run["decision"]["executed"]]
    assert suppliers == ["SUP-I", "SUP-J"]                  # did not retry the supplier that fell short


def test_high_value_pauses_for_approval_then_executes(conn):
    orch = Orchestrator(conn, "rules")
    run = orch.run("CASE-S3-C")
    assert run["status"] == "awaiting_approval"
    assert run["decision"]["executed"] == []                # nothing committed before sign-off
    apr = orch.repo.list_approvals("pending")[0]
    done = orch.resolve_approval(apr["id"], approve=True, approver="test")
    assert done["status"] == "completed"
    assert done["decision"]["executed"][0]["actual"]["status"] == "confirmed"


def test_rejected_approval_executes_nothing(conn):
    orch = Orchestrator(conn, "rules")
    orch.run("CASE-S3-C")
    apr = orch.repo.list_approvals("pending")[0]
    done = orch.resolve_approval(apr["id"], approve=False, approver="test", comment="wait for promo plan")
    assert done["status"] == "rejected_by_human"
    assert done["decision"]["executed"] == []


class ScriptedAgent:
    """Agent that replays a fixed list of decisions, optionally skipping evidence."""
    name = "scripted"

    def __init__(self, decisions, gather=True):
        self.decisions = list(decisions)
        self.gather = gather
        self.feedback_seen = []

    def decide(self, tools: ToolBox, feedback=None):
        self.feedback_seen.append(feedback)
        if self.gather:
            for t in ("get_case_details", "get_item_profile", "get_demand_signal",
                      "get_open_purchase_orders", "get_supplier_options", "get_budget"):
                tools.call(t)
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]


def test_evidence_gate_blocks_uninformed_decisions(conn):
    agent = ScriptedAgent([Decision(verdict="reject", summary="looks fine")], gather=False)
    run = Orchestrator(conn, "rules", agent=agent).run("CASE-S1-C")
    assert run["status"] == "escalated"
    assert all(not g["ok"] for g in kinds(run, "evidence_gate"))
    assert agent.feedback_seen[1]["type"] == "missing_evidence"


def test_validation_feedback_lets_agent_revise(conn):
    bad = Decision(verdict="accept", summary="blind accept",
                   actions=[Action(type="create_po", supplier_id="SUP-A", quantity=800, reason="rec")])
    good = Decision(verdict="modify", summary="right-sized",
                    actions=[Action(type="create_po", supplier_id="SUP-A", quantity=216, reason="need")])
    agent = ScriptedAgent([bad, good])
    run = Orchestrator(conn, "rules", agent=agent).run("CASE-S1-A")
    assert run["status"] == "completed"
    assert agent.feedback_seen[1]["type"] == "validation_failed"
    assert any("storage" in v.lower() for v in agent.feedback_seen[1]["violations"])
    assert [e["action"]["quantity"] for e in run["decision"]["executed"]] == [216]


def test_loop_exhaustion_escalates_instead_of_guessing(conn):
    bad = Decision(verdict="accept", summary="blind accept",
                   actions=[Action(type="create_po", supplier_id="SUP-A", quantity=800, reason="rec")])
    run = Orchestrator(conn, "rules", agent=ScriptedAgent([bad])).run("CASE-S1-A")
    assert run["status"] == "escalated"
    assert run["decision"]["executed"][-1]["action"]["type"] == "escalate"
    assert not any(e["action"]["type"] == "create_po" for e in run["decision"]["executed"])
