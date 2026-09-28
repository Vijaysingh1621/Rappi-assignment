"""LLM agent wiring, tested offline with a scripted fake Anthropic client.

These tests check the protocol, not the model's judgement: tool calls are
executed and returned correctly, submit_decision ends a round, validation /
verification feedback is returned as the submit call's tool_result, and a
malformed decision is bounced back to the model. Model judgement is measured by
the eval harness (``python -m evals.run --mode llm``) with a real API key.
"""
import json
from types import SimpleNamespace as NS

import pytest

from app.agent.llm import LLMAgent
from app.agent.orchestrator import Orchestrator
from app.db import session

EVIDENCE = ["get_case_details", "get_item_profile", "get_demand_signal",
            "get_open_purchase_orders", "get_supplier_options", "get_budget"]


def tool_use(i, name, **inp):
    return NS(type="tool_use", id=f"tu_{i}", name=name, input=inp)


def response(*blocks, stop="tool_use"):
    return NS(content=list(blocks), stop_reason=stop, usage=NS(input_tokens=100, output_tokens=20))


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=lambda o: o.__dict__)))
        return self.script.pop(0)


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


@pytest.fixture()
def conn(tmp_path):
    with session(tmp_path / "t.db", reset=True) as c:
        yield c


def test_llm_agent_feedback_loop_on_partial_confirmation(conn):
    script = [
        response(NS(type="text", text="Let me investigate."), *[tool_use(i, n) for i, n in enumerate(EVIDENCE)]),
        response(tool_use(10, "evaluate_supplier_options", exclude_suppliers=["SUP-H"])),
        response(tool_use(11, "submit_decision", verdict="modify", summary="Source remainder from SUP-I",
                          key_factors=["gap 150"], daily_demand_used=40,
                          actions=[{"type": "create_po", "supplier_id": "SUP-I", "quantity": 150,
                                    "reason": "cover gap"}])),
        # after verification feedback: re-plan
        response(tool_use(12, "get_open_purchase_orders"),
                 tool_use(13, "evaluate_supplier_options", exclude_suppliers=["SUP-H", "SUP-I"])),
        response(tool_use(14, "submit_decision", verdict="modify", summary="Top up from SUP-J",
                          key_factors=["SUP-I confirmed 60"], daily_demand_used=40,
                          actions=[{"type": "create_po", "supplier_id": "SUP-J", "quantity": 50,
                                    "reason": "remaining gap"}])),
    ]
    client = FakeClient(script)
    run = Orchestrator(conn, "llm", agent=LLMAgent(client=client, model="test")).run("CASE-S2-C")

    assert run["status"] == "completed"
    assert [e["action"]["supplier_id"] for e in run["decision"]["executed"]] == ["SUP-I", "SUP-J"]
    # The verification discrepancy was returned to the model as the submit_decision result
    feedback_msg = client.messages.requests[3][-1]["content"][-1]
    assert feedback_msg["tool_use_id"] == "tu_11" and feedback_msg["is_error"] is True
    assert "verification_failed" in feedback_msg["content"]
    assert "partially_confirmed" in feedback_msg["content"]
    assert any(s["kind"] == "llm" for s in run["trace"])


def test_malformed_decision_is_bounced_back(conn):
    script = [
        response(*[tool_use(i, n) for i, n in enumerate(EVIDENCE)]),
        response(tool_use(10, "submit_decision", verdict="buy_everything", summary="x",
                          key_factors=[], actions=[])),
        response(tool_use(11, "submit_decision", verdict="reject", summary="Stock covers demand",
                          key_factors=["position 700 vs target 280"], actions=[])),
    ]
    client = FakeClient(script)
    run = Orchestrator(conn, "llm", agent=LLMAgent(client=client, model="test")).run("CASE-S1-C")
    assert run["status"] == "completed"
    bounced = client.messages.requests[2][-1]["content"][0]
    assert bounced["is_error"] and "Invalid decision format" in bounced["content"]


def test_refusal_fails_run_cleanly(conn):
    client = FakeClient([response(stop="refusal")])
    run = Orchestrator(conn, "llm", agent=LLMAgent(client=client, model="test")).run("CASE-S1-A")
    assert run["status"] == "failed"
    assert run["decision"]["executed"] == []
