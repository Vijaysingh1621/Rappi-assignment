"""HTTP API + static UI for the purchasing agent."""
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent.orchestrator import Orchestrator
from app.agent.tools import ToolBox, Trace
from app.config import get_settings
from app.db import SIM_TODAY, connect, init_db
from app.repository import Repository

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="AI Purchasing Agent", version="1.0")


def get_conn():
    conn = connect()
    init_db(conn)
    try:
        yield conn
    finally:
        conn.close()


class RunRequest(BaseModel):
    mode: str = "rules"


class ApprovalDecision(BaseModel):
    approve: bool
    approver: str = "buyer"
    comment: str = ""


@app.get("/api/health")
def health():
    s = get_settings()
    return {"ok": True, "sim_today": SIM_TODAY.isoformat(), "llm_available": bool(s.anthropic_api_key),
            "model": s.agent_model, "auto_approve_max_po_value": s.auto_approve_max_po_value}


@app.get("/api/cases")
def list_cases(conn=Depends(get_conn)):
    repo = Repository(conn)
    runs = repo.list_runs()
    latest = {}
    for r in runs:
        latest.setdefault(r["case_id"], {"run_id": r["id"], "status": r["status"], "mode": r["mode"],
                                         "verdict": ((r["decision"] or {}).get("final") or {}).get("verdict")})
    return [{**c, "latest_run": latest.get(c["id"])} for c in repo.list_cases()]


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, conn=Depends(get_conn)):
    repo = Repository(conn)
    case = repo.get_case(case_id)
    if not case:
        raise HTTPException(404, "case not found")
    tools = ToolBox(repo, case, Trace())   # same read tools the agent uses; not logged to any run
    return {
        "case": case,
        "item": tools.get_item_profile(),
        "demand": tools.get_demand_signal(),
        "open_pos": tools.get_open_purchase_orders(),
        "suppliers": tools.get_supplier_options(),
        "budget": tools.get_budget(),
        "projection": tools.project_inventory(horizon_days=14),
        "runs": repo.list_runs(case_id),
    }


@app.post("/api/cases/{case_id}/run")
def run_case(case_id: str, req: RunRequest, conn=Depends(get_conn)):
    if req.mode == "llm" and not get_settings().anthropic_api_key:
        raise HTTPException(400, "LLM mode needs ANTHROPIC_API_KEY in .env — use mode 'rules' or add a key.")
    if req.mode not in ("rules", "llm"):
        raise HTTPException(400, "mode must be 'rules' or 'llm'")
    try:
        return Orchestrator(conn, req.mode).run(case_id)
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/runs")
def list_runs(case_id: str | None = None, conn=Depends(get_conn)):
    return Repository(conn).list_runs(case_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, conn=Depends(get_conn)):
    run = Repository(conn).get_run(run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/approvals")
def approvals(status: str | None = None, conn=Depends(get_conn)):
    return Repository(conn).list_approvals(status)


@app.post("/api/approvals/{approval_id}")
def decide_approval(approval_id: str, body: ApprovalDecision, conn=Depends(get_conn)):
    repo = Repository(conn)
    apr = repo.get_approval(approval_id)
    run = repo.get_run(apr["run_id"]) if apr else None
    # If execution after approval needs re-planning, use the same agent that proposed it.
    mode = run["mode"] if run and (run["mode"] == "rules" or get_settings().anthropic_api_key) else "rules"
    try:
        return Orchestrator(conn, mode).resolve_approval(approval_id, body.approve, body.approver, body.comment)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/escalations")
def escalations(conn=Depends(get_conn)):
    return Repository(conn).list_escalations()


@app.get("/api/purchase-orders")
def purchase_orders(conn=Depends(get_conn)):
    return Repository(conn).list_pos()


@app.post("/api/reset")
def reset(conn=Depends(get_conn)):
    init_db(conn, reset=True)
    return {"ok": True}


app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")
