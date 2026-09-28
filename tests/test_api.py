"""HTTP API smoke tests (FastAPI TestClient)."""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    from app.main import app
    with TestClient(app) as c:
        c.post("/api/reset")
        yield c


def test_list_and_detail(client):
    cases = client.get("/api/cases").json()
    assert len(cases) == 14
    d = client.get("/api/cases/CASE-S1-A").json()
    assert d["case"]["recommendation"]["quantity"] == 800
    assert d["demand"]["signal"] == "stable"


def test_run_and_approval_flow(client):
    run = client.post("/api/cases/CASE-S3-C/run", json={"mode": "rules"}).json()
    assert run["status"] == "awaiting_approval"
    pending = client.get("/api/approvals", params={"status": "pending"}).json()
    assert len(pending) == 1
    done = client.post(f"/api/approvals/{pending[0]['id']}", json={"approve": True, "approver": "t"}).json()
    assert done["status"] == "completed"
    again = client.post(f"/api/approvals/{pending[0]['id']}", json={"approve": True})
    assert again.status_code == 409


def test_llm_mode_without_key_is_rejected(client):
    r = client.post("/api/cases/CASE-S1-A/run", json={"mode": "llm"})
    assert r.status_code == 400


def test_unknown_case(client):
    assert client.post("/api/cases/NOPE/run", json={"mode": "rules"}).status_code == 404


def test_ui_served(client):
    assert "AI Purchasing Agent" in client.get("/").text
