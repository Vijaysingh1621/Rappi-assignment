"""Expected outcomes for every seeded case.

Expectations are written as *acceptable ranges and properties*, not exact
answers, because several decisions are defensible (e.g. ordering from the
faster alternate supplier). Each expectation was derived by hand from the seed
data (see docs/EVALUATION.md for the worked reasoning), not copied from agent
output.
"""

EXPECTATIONS = {
    "CASE-S1-A": dict(
        why="Recommendation ignores 200 units already inbound and would overflow storage; need ≈210.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(120, 240), allowed_suppliers={"SUP-A", "SUP-B"}),
    "CASE-S1-B": dict(
        why="Recommendation equals the computed requirement and passes all constraints.",
        verdicts={"accept"}, status={"completed"},
        purchase_qty=(800, 800), allowed_suppliers={"SUP-C"}),
    "CASE-S1-C": dict(
        why="35 days of cover on hand; any purchase is excess inventory.",
        verdicts={"reject"}, status={"completed"}, purchase_qty=(0, 0)),
    "CASE-S1-D": dict(
        why="5 days of zero sales with 480 'on hand' → phantom inventory; count before buying.",
        verdicts={"investigate"}, status={"completed_with_escalation"},
        purchase_qty=(0, 0), required_actions={"request_cycle_count"}),
    "CASE-S2-A": dict(
        why="Even with only 250 confirmed, stock covers 34 days; no action needed.",
        verdicts={"accept"}, status={"completed"}, purchase_qty=(0, 0)),
    "CASE-S2-B": dict(
        why="Gap after short-shipment; the cheapest option is a blocked supplier; primary has no stock.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(48, 250), allowed_suppliers={"SUP-F", "SUP-G"}, forbidden_suppliers={"SUP-X", "SUP-E"}),
    "CASE-S2-C": dict(
        why="Alternate supplier's catalogue looks fine but it only confirms 60 — agent must detect and re-plan.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(100, 250), allowed_suppliers={"SUP-I", "SUP-J"}, forbidden_suppliers={"SUP-H"},
        must_recover=True),
    "CASE-S2-D": dict(
        why="Only alternate costs +45% (over the 25% cap) and arrives too late; stock-out imminent → human.",
        verdicts={"escalate"}, status={"completed_with_escalation"},
        purchase_qty=(0, 0), required_actions={"escalate"}, forbidden_suppliers={"SUP-K"}),
    "CASE-S3-A": dict(
        why="Sustained surge to ~91/day; open PO not shipped and changeable → increase it by ≈350.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(300, 420), required_actions={"modify_po"}),
    "CASE-S3-B": dict(
        why="Spike was an ended 3x2 promo; underlying demand is on forecast → no change.",
        verdicts={"accept"}, status={"completed"}, purchase_qty=(0, 0)),
    "CASE-S3-C": dict(
        why="Sustained surge; PO in transit and supplier refuses changes → new PO, >$5k → human approval.",
        verdicts={"modify"}, status={"awaiting_approval"},
        purchase_qty=(300, 400), allowed_suppliers={"SUP-M"}, approve_then={"completed"}),
    "CASE-S4-A": dict(
        why="Need ≈440 units but budget only covers 300 → buy 300 and escalate the gap.",
        verdicts={"modify"}, status={"completed_with_escalation"},
        purchase_qty=(240, 300), allowed_suppliers={"SUP-N", "SUP-O"}, required_actions={"escalate"}),
    "CASE-S4-B": dict(
        why="260 units needed but only ~190 fit when the truck arrives → split into staggered deliveries.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(240, 280), allowed_suppliers={"SUP-R"}, min_po_count=2),
    "CASE-S4-C": dict(
        why="Primary MOQ 480 = 60 days of a 21-day-shelf-life item → use the small-MOQ alternate.",
        verdicts={"modify"}, status={"completed"},
        purchase_qty=(36, 96), allowed_suppliers={"SUP-Q"}, forbidden_suppliers={"SUP-P"}),
}
