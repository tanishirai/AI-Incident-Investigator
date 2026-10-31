from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.database import Database  # noqa: E402

if __name__ == "__main__":
    target = settings.database_path
    if target.exists():
        target.unlink()
        print(f"Removed local database: {target}")
    Database(target)
    print("Initialized a fresh SQLite database.")
