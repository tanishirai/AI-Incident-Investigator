from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.schemas import EvidenceInput, IncidentInput, ReviewInput


def iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value else None


class Database:
    """Small SQLite repository; each operation owns a short-lived connection."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    severity TEXT NOT NULL,
                    service TEXT NOT NULL,
                    endpoint TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    resolved_at TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    type TEXT NOT NULL,
                    source TEXT NOT NULL,
                    timestamp TEXT,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(incident_id, fingerprint)
                );
                CREATE INDEX IF NOT EXISTS idx_evidence_incident_time
                    ON evidence(incident_id, timestamp);
                CREATE TABLE IF NOT EXISTS investigations (
                    id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL REFERENCES incidents(id) ON DELETE CASCADE,
                    method TEXT NOT NULL,
                    model TEXT,
                    result_json TEXT NOT NULL,
                    duration_seconds REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_investigations_incident
                    ON investigations(incident_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    investigation_id TEXT NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
                    hypothesis_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    reviewed_at TEXT NOT NULL,
                    UNIQUE(investigation_id, hypothesis_id)
                );
                CREATE TABLE IF NOT EXISTS gemini_cache (
                    cache_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    incident_id TEXT,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_hit INTEGER NOT NULL DEFAULT 0,
                    details TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS id_sequence (
                    name TEXT PRIMARY KEY,
                    next_value INTEGER NOT NULL
                );
                INSERT OR IGNORE INTO id_sequence(name, next_value) VALUES ('evidence', 1);
                """
            )

    def create_incident(self, payload: IncidentInput, incident_id: str) -> dict[str, Any]:
        created = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO incidents
                (id,title,description,severity,service,endpoint,started_at,resolved_at,status,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    incident_id,
                    payload.title,
                    payload.description,
                    payload.severity.value,
                    payload.service,
                    payload.endpoint,
                    iso(payload.started_at),
                    iso(payload.resolved_at),
                    payload.status.value,
                    created,
                ),
            )
        return self.get_incident(incident_id)  # type: ignore[return-value]

    def update_incident_status(self, incident_id: str, status: str) -> None:
        with self.connection() as conn:
            conn.execute("UPDATE incidents SET status=? WHERE id=?", (status, incident_id))

    def get_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()
        return dict(row) if row else None

    def list_incidents(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM incidents ORDER BY started_at DESC").fetchall()
        return [dict(row) for row in rows]

    def add_evidence(self, payload: EvidenceInput) -> tuple[str, bool]:
        fingerprint = evidence_fingerprint(payload)
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute(
                "SELECT id FROM evidence WHERE incident_id=? AND fingerprint=?",
                (payload.incident_id, fingerprint),
            ).fetchone()
            if existing:
                return str(existing["id"]), False
            next_id = conn.execute(
                "SELECT next_value FROM id_sequence WHERE name='evidence'"
            ).fetchone()["next_value"]
            conn.execute(
                "UPDATE id_sequence SET next_value=? WHERE name='evidence'",
                (next_id + 1,),
            )
            evidence_id = f"EVID-{int(next_id):04d}"
            conn.execute(
                """INSERT INTO evidence
                (id,incident_id,type,source,timestamp,content,metadata_json,fingerprint,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,
                    payload.incident_id,
                    payload.type.value,
                    payload.source,
                    iso(payload.timestamp),
                    payload.content,
                    json.dumps(payload.metadata, ensure_ascii=False, default=str),
                    fingerprint,
                    now,
                ),
            )
        return evidence_id, True

    def list_evidence(self, incident_id: str) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM evidence WHERE incident_id=? ORDER BY timestamp, id",
                (incident_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            out.append(item)
        return out

    def save_investigation(
        self, investigation_id: str, incident_id: str, result: dict[str, Any], duration: float
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO investigations
                (id,incident_id,method,model,result_json,duration_seconds,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    investigation_id,
                    incident_id,
                    result.get("method", "deterministic"),
                    result.get("model"),
                    json.dumps(result, ensure_ascii=False),
                    duration,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def latest_investigation(self, incident_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                """SELECT * FROM investigations WHERE incident_id=?
                ORDER BY created_at DESC LIMIT 1""",
                (incident_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["result"] = json.loads(item.pop("result_json"))
        return item

    def list_investigations(self, limit: int = 15) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """SELECT i.*, n.title AS incident_title FROM investigations i
                JOIN incidents n ON n.id=i.incident_id
                ORDER BY i.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_review(self, payload: ReviewInput) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO reviews
                (investigation_id,hypothesis_id,decision,notes,reviewed_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(investigation_id,hypothesis_id) DO UPDATE SET
                decision=excluded.decision, notes=excluded.notes, reviewed_at=excluded.reviewed_at""",
                (
                    payload.investigation_id,
                    payload.hypothesis_id,
                    payload.decision,
                    payload.notes,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def list_reviews(self) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute("SELECT * FROM reviews ORDER BY reviewed_at DESC").fetchall()
        return [dict(row) for row in rows]

    def review_for(self, investigation_id: str, hypothesis_id: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT * FROM reviews WHERE investigation_id=? AND hypothesis_id=?",
                (investigation_id, hypothesis_id),
            ).fetchone()
        return dict(row) if row else None

    def get_cache(self, key: str) -> dict[str, Any] | None:
        with self.connection() as conn:
            row = conn.execute(
                "SELECT result_json FROM gemini_cache WHERE cache_key=?", (key,)
            ).fetchone()
        return json.loads(row["result_json"]) if row else None

    def put_cache(self, key: str, model: str, result: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gemini_cache(cache_key,result_json,model,created_at)
                VALUES (?,?,?,?)""",
                (
                    key,
                    json.dumps(result, ensure_ascii=False),
                    model,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def clear_cache(self) -> int:
        with self.connection() as conn:
            cur = conn.execute("DELETE FROM gemini_cache")
            return cur.rowcount

    def log_usage(
        self,
        event_type: str,
        incident_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_hit: bool = False,
        details: str = "",
    ) -> None:
        with self.connection() as conn:
            conn.execute(
                """INSERT INTO usage_events
                (event_type,incident_id,input_tokens,output_tokens,cache_hit,details,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (
                    event_type,
                    incident_id,
                    input_tokens,
                    output_tokens,
                    int(cache_hit),
                    details[:500],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def dashboard_stats(self) -> dict[str, Any]:
        with self.connection() as conn:
            incidents = conn.execute("SELECT COUNT(*) n FROM incidents").fetchone()["n"]
            methods = conn.execute(
                "SELECT method,COUNT(*) n FROM investigations GROUP BY method"
            ).fetchall()
            usage = conn.execute(
                """SELECT
                SUM(CASE WHEN event_type='gemini_call' THEN 1 ELSE 0 END) calls,
                SUM(CASE WHEN event_type='cache_hit' THEN 1 ELSE 0 END) hits,
                SUM(CASE WHEN event_type='cache_miss' THEN 1 ELSE 0 END) misses,
                SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,
                SUM(CASE WHEN event_type='fallback' THEN 1 ELSE 0 END) fallbacks
                FROM usage_events"""
            ).fetchone()
            reviews = conn.execute(
                "SELECT decision,COUNT(*) n FROM reviews GROUP BY decision"
            ).fetchall()
            times = conn.execute(
                "SELECT AVG(duration_seconds) average FROM investigations"
            ).fetchone()["average"]
        return {
            "incidents": incidents,
            "methods": {row["method"]: row["n"] for row in methods},
            "usage": {key: (usage[key] or 0) for key in usage.keys()},
            "reviews": {row["decision"]: row["n"] for row in reviews},
            "average_seconds": times or 0,
        }


def evidence_fingerprint(payload: EvidenceInput) -> str:
    import hashlib

    normalized = "|".join(
        (
            payload.type.value,
            payload.source.strip().lower(),
            iso(payload.timestamp) or "",
            " ".join(payload.content.split()).lower(),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
