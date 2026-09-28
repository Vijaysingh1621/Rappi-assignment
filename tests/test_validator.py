"""Validator & policy tests against the seeded cases."""
import pytest

from app.db import session
from app.models import Action, Decision
from app.policy import approval_reasons
from app.repository import Repository
from app.validator import validate_decision


@pytest.fixture()
def repo(tmp_path):
    with session(tmp_path / "t.db", reset=True) as conn:
        yield Repository(conn)


def codes(report):
    return {c.code for c in report.violations}


def po(supplier, qty, **kw):
    return Action(type="create_po", supplier_id=supplier, quantity=qty, reason="test", **kw)


def test_blindly_accepting_bad_recommendation_is_blocked(repo):
    case = repo.get_case("CASE-S1-A")
    rep = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-A", 800)]))
    assert {"STORAGE", "NO_OVERBUY"} <= codes(rep)


def test_right_sized_order_passes(repo):
    case = repo.get_case("CASE-S1-A")
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-A", 216)]))
    assert rep.ok, rep.as_dict()


def test_accept_requires_matching_recommendation(repo):
    case = repo.get_case("CASE-S1-B")
    good = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-C", 800)]))
    assert good.ok, good.as_dict()
    bad = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-C", 760)]))
    assert "VERDICT_CONSISTENT" in codes(bad)


def test_case_pack_and_moq(repo):
    case = repo.get_case("CASE-S1-A")
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-A", 50)]))
    assert {"CASE_PACK", "MOQ"} <= codes(rep)


def test_budget_violation(repo):
    case = repo.get_case("CASE-S4-A")
    rep = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-N", 444)]))
    assert "BUDGET" in codes(rep)


def test_partial_purchase_must_escalate_residual_risk(repo):
    case = repo.get_case("CASE-S4-A")
    no_esc = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-N", 300)]))
    assert "COVERAGE" in codes(no_esc)
    esc = Action(type="escalate", reason="Budget covers only 300 of 444 units")
    with_esc = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-N", 300), esc]))
    assert with_esc.ok, with_esc.as_dict()


def test_moq_exceeding_shelf_life_is_blocked(repo):
    case = repo.get_case("CASE-S4-C")
    rep = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-P", 480)]))
    assert "SHELF_LIFE" in codes(rep)


def test_blocked_supplier_rejected(repo):
    case = repo.get_case("CASE-S2-B")
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-X", 60)]))
    assert "SUPPLIER_ACTIVE" in codes(rep)


def test_price_premium_cap(repo):
    case = repo.get_case("CASE-S2-D")
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-K", 144)]))
    assert "PRICE_PREMIUM" in codes(rep)


def test_in_transit_po_cannot_be_modified(repo):
    case = repo.get_case("CASE-S3-C")
    act = Action(type="modify_po", po_id="PO-7003", quantity=750)
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[act]))
    assert {"PO_NOT_SHIPPED", "SUPPLIER_ALLOWS_CHANGES"} <= codes(rep)


def test_purchase_blocked_while_data_unreliable(repo):
    case = repo.get_case("CASE-S1-D")
    rep = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[po("SUP-B", 180)]))
    assert "DATA_QUALITY" in codes(rep)
    ok = validate_decision(repo, case, Decision(verdict="investigate", summary="",
                                                actions=[Action(type="request_cycle_count", reason="phantom stock")]))
    assert ok.ok, ok.as_dict()


def test_reject_with_purchase_is_inconsistent(repo):
    case = repo.get_case("CASE-S1-C")
    assert "VERDICT_CONSISTENT" in codes(
        validate_decision(repo, case, Decision(verdict="reject", summary="", actions=[po("SUP-T", 100)])))
    assert validate_decision(repo, case, Decision(verdict="reject", summary="")).ok


def test_split_delivery_respects_storage(repo):
    case = repo.get_case("CASE-S4-B")
    single = validate_decision(repo, case, Decision(verdict="accept", summary="", actions=[po("SUP-R", 260)]))
    assert "STORAGE" in codes(single)
    split = validate_decision(repo, case, Decision(verdict="modify", summary="", actions=[
        po("SUP-R", 190, delivery_day=3), po("SUP-R", 70, delivery_day=6)]))
    assert split.ok, split.as_dict()


def test_high_value_requires_approval(repo):
    d = Decision(verdict="modify", summary="", actions=[po("SUP-M", 350)])
    assert approval_reasons(repo, d, 5250.0)
    assert not approval_reasons(repo, d, 400.0)
