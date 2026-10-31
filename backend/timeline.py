from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from backend.timestamps import parse_timestamp

TYPE_ORDER = {"DEPLOYMENT": 0, "COMMIT": 1, "LOG": 2, "METRIC": 3, "ISSUE": 4, "PREVIOUS_INCIDENT": 5}


def build_timeline(incident: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    incident_start = parse_timestamp(incident.get("started_at"))
    if incident_start:
        events.append(
            {
                "timestamp": incident_start.isoformat(),
                "type": "INCIDENT",
                "evidence_id": None,
                "label": "Incident started",
                "content": incident.get("title", "Incident"),
                "source": "incident",
            }
        )
    for item in evidence:
        stamp = parse_timestamp(item.get("timestamp"))
        if not stamp:
            continue
        content = str(item.get("content", "")).replace("\n", " ").strip()
        events.append(
            {
                "timestamp": stamp.isoformat(),
                "type": item.get("type", "UNKNOWN"),
                "evidence_id": item.get("id"),
                "label": content[:100],
                "content": content,
                "source": item.get("source", ""),
            }
        )
    events.sort(
        key=lambda event: (
            event["timestamp"],
            TYPE_ORDER.get(event["type"], 6),
            event.get("evidence_id") or "",
        )
    )
    return events
