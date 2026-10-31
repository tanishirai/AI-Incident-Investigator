from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from backend.schemas import EvidenceInput
from backend.timestamps import parse_timestamp

SENSITIVE_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+"),
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+"),
)


def redact_secrets(text: str) -> str:
    safe = text
    safe = SENSITIVE_PATTERNS[0].sub(lambda m: m.group(1) + "=[REDACTED]", safe)
    safe = SENSITIVE_PATTERNS[1].sub("Bearer [REDACTED]", safe)
    return safe


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def fingerprint_values(kind: str, source: str, timestamp: Any, content: str) -> str:
    import datetime

    parsed = parse_timestamp(timestamp)
    stamp = parsed.isoformat() if parsed else ""
    text = " ".join(redact_secrets(content).split()).casefold()
    raw = f"{kind.upper()}|{source.strip().casefold()}|{stamp}|{text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def deduplicate_evidence(items: Iterable[EvidenceInput]) -> list[EvidenceInput]:
    seen: set[str] = set()
    unique: list[EvidenceInput] = []
    for item in items:
        key = fingerprint_values(item.type.value, item.source, item.timestamp, item.content)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def evidence_terms(text: str) -> set[str]:
    return {
        token.casefold()
        for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_./:-]{2,}", text)
        if token.casefold() not in {"the", "and", "for", "with", "from", "into", "that", "this"}
    }


def normalize_records(
    records: list[dict[str, Any]], incident_id: str, evidence_type: str, source: str
) -> list[EvidenceInput]:
    from backend.schemas import EvidenceType

    normalized: list[EvidenceInput] = []
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            continue
        values = {str(key).strip().lower(): value for key, value in row.items()}
        timestamp = next(
            (values.get(key) for key in ("timestamp", "time", "created_at", "date", "started_at") if values.get(key)),
            None,
        )
        fields = (
            "message", "content", "title", "body", "error", "error_type", "exception",
            "description", "root_cause", "resolution", "metric_name", "version", "status",
        )
        fragments = []
        for key in fields:
            value = values.get(key)
            if value not in (None, ""):
                fragments.append(f"{key}={value}")
        if not fragments:
            fragments = [canonical_json(row)]
        row_source = str(
            values.get("source")
            or values.get("service")
            or values.get("sha")
            or values.get("issue_id")
            or source
        )[:400]
        safe_metadata = {k: v for k, v in values.items() if k not in {"token", "password", "secret", "api_key"}}
        try:
            normalized.append(
                EvidenceInput(
                    incident_id=incident_id,
                    type=EvidenceType(evidence_type),
                    source=row_source,
                    timestamp=parse_timestamp(timestamp),
                    content=redact_secrets(" | ".join(fragments))[:12000],
                    metadata={"record_index": index, **safe_metadata},
                )
            )
        except (ValueError, TypeError):
            continue
    return deduplicate_evidence(normalized)
