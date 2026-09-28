import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None
    agent_model: str
    database_path: Path
    auto_approve_max_po_value: float
    max_revisions: int


def get_settings() -> Settings:
    db = Path(os.getenv("DATABASE_PATH", "data/purchasing.db"))
    if not db.is_absolute():
        db = ROOT / db
    return Settings(
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        agent_model=os.getenv("AGENT_MODEL", "claude-opus-5"),
        database_path=db,
        auto_approve_max_po_value=float(os.getenv("AUTO_APPROVE_MAX_PO_VALUE", "5000")),
        max_revisions=int(os.getenv("MAX_REVISIONS", "3")),
    )
