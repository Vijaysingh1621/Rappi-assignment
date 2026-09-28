"""Supplier API, executor and post-execution verification."""
import pytest

from app.db import session
from app.executor import Executor
from app.models import Action
from app.repository import Repository
from app.verifier import verify_outcome


@pytest.fixture()
def conn(tmp_path):
    with session(tmp_path / "t.db", reset=True) as c:
        yield c


def test_confirmed_po_verifies_and_commits_budget(conn):
    repo = Repository(conn)
    case = repo.get_case("CASE-S1-A")
    before = repo.get_budget("BOG-01", "beverages")["committed"]
    res = Executor(conn, "run-1").execute(case, [Action(type="create_po", supplier_id="SUP-A", quantity=216)])
    assert res[0]["actual"]["status"] == "confirmed"
    assert repo.get_budget("BOG-01", "beverages")["committed"] == pytest.approx(before + 216 * 0.55)
    ver = verify_outcome(repo, case, res, 40.0, escalated=False)
    assert ver.ok, ver.as_dict()


def test_partial_confirmation_is_detected_as_discrepancy(conn):
    """SUP-I's catalogue looks fine but it can only ship 60 units."""
    repo = Repository(conn)
    case = repo.get_case("CASE-S2-C")
    res = Executor(conn, "run-1").execute(case, [Action(type="create_po", supplier_id="SUP-I", quantity=150)])
    assert res[0]["actual"] == {"qty": 60, "delivery_day": 5, "status": "partially_confirmed"}
    ver = verify_outcome(repo, case, res, 40.0, escalated=False)
    failed = {c["code"] for c in ver.discrepancies}
    assert {"PO_CONFIRMED", "COVERAGE"} <= failed
    assert ver.residual_requirement["net_requirement"] > 0


def test_supplier_rejects_when_nothing_available(conn):
    repo = Repository(conn)
    case = repo.get_case("CASE-S2-B")
    res = Executor(conn, "run-1").execute(case, [Action(type="create_po", supplier_id="SUP-E", quantity=240)])
    assert res[0]["actual"]["status"] == "rejected"
    assert res[0]["actual"]["qty"] == 0


def test_modify_po_increases_quantity(conn):
    repo = Repository(conn)
    case = repo.get_case("CASE-S3-A")
    res = Executor(conn, "run-1").execute(case, [Action(type="modify_po", po_id="PO-7001", quantity=960)])
    assert res[0]["actual"]["status"] == "confirmed"
    assert repo.get_po("PO-7001")["qty_confirmed"] == 960
    assert verify_outcome(repo, case, res, 91.3, escalated=False).ok


def test_in_transit_po_modification_rejected_by_supplier(conn):
    repo = Repository(conn)
    case = repo.get_case("CASE-S3-C")
    res = Executor(conn, "run-1").execute(case, [Action(type="modify_po", po_id="PO-7003", quantity=750)])
    assert res[0]["actual"]["status"] == "rejected"
    assert repo.get_po("PO-7003")["qty_confirmed"] == 400


def test_escalation_is_recorded(conn):
    repo = Repository(conn)
    case = repo.get_case("CASE-S2-D")
    Executor(conn, "run-1").execute(case, [Action(type="escalate", reason="No viable supplier", options=["a", "b"])])
    esc = repo.list_escalations()
    assert esc[0]["case_id"] == "CASE-S2-D" and esc[0]["options"] == ["a", "b"]
