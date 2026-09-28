"""Autonomy policy: which validated actions the agent may execute on its own.

Validation answers "is this proposal correct and allowed?". Policy answers
"may the agent do it without a human?". Only purchase commitments above a
value threshold and destructive changes need a human signature; escalations
and cycle-count requests are informational and always allowed.
"""
from app.config import get_settings
from app.models import Decision
from app.repository import Repository


def approval_reasons(repo: Repository, decision: Decision, total_value: float) -> list[str]:
    limit = get_settings().auto_approve_max_po_value
    reasons = []
    if total_value > limit:
        reasons.append(f"Commitment ${total_value:,.2f} exceeds the ${limit:,.0f} auto-approval limit")
    for a in decision.actions:
        if a.type == "cancel_po":
            reasons.append(f"Cancelling {a.po_id} is destructive and needs buyer sign-off")
        if a.type == "modify_po" and a.po_id:
            po = repo.get_po(a.po_id)
            if po and a.quantity is not None and a.quantity < po["inbound_qty"]:
                reasons.append(f"Reducing {a.po_id} below its confirmed quantity needs buyer sign-off")
    if decision.confidence == "low" and decision.purchase_actions():
        reasons.append("Agent reported low confidence in a purchasing decision")
    return reasons
