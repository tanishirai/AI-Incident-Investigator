from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    gemini_api_key: str
    gemini_model: str
    database_url: str
    database_warning: str
    github_token: str
    max_upload_mb: int
    max_evidence_items: int
    max_packet_chars: int

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.getenv(
            "DATABASE_URL", "sqlite:///incident_investigator.db"
        ).strip()
        database_warning = ""
        if not database_url.startswith("sqlite:///"):
            # The surrounding Replit template may provide its own DB URL. This
            # project is explicitly SQLite-only; do not connect to that service.
            database_url = "sqlite:///incident_investigator.db"
            database_warning = (
                "A non-SQLite DATABASE_URL was present and was ignored. "
                "This project uses its local SQLite database."
            )
        return cls(
            gemini_api_key=os.getenv("GEMINI_API_KEY", "").strip(),
            gemini_model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip(),
            database_url=database_url,
            database_warning=database_warning,
            github_token=os.getenv("GITHUB_TOKEN", "").strip(),
            max_upload_mb=max(1, int(os.getenv("MAX_UPLOAD_MB", "4"))),
            max_evidence_items=max(10, int(os.getenv("MAX_EVIDENCE_ITEMS", "120"))),
            max_packet_chars=max(3000, int(os.getenv("MAX_PACKET_CHARS", "18000"))),
        )

    @property
    def database_path(self) -> Path:
        prefix = "sqlite:///"
        if not self.database_url.startswith(prefix):
            raise ValueError("DATABASE_URL must use a local sqlite:/// path.")
        raw_path = self.database_url[len(prefix) :]
        path = Path(raw_path)
        return path if path.is_absolute() else PROJECT_ROOT / path


settings = Settings.from_env()

def get_config_value(name: str, default: str = "") -> str:
    value = os.getenv(name)

    if value and value.strip():
        return value.strip()

    try:
        import streamlit as st
        value = st.secrets.get(name, default)
    except Exception:
        value = default

    return str(value).strip()
