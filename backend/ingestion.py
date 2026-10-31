from __future__ import annotations

import csv
import io
import json
import re
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime
from typing import Any

from backend.normalization import normalize_records, redact_secrets
from backend.schemas import EvidenceInput, EvidenceType
from backend.timestamps import parse_timestamp

LOG_LINE = re.compile(
    r"(?P<timestamp>\d{4}-\d{2}-\d{2}[T ][\d:.+-]+Z?)\s+"
    r"(?:(?P<level>TRACE|DEBUG|INFO|WARN|WARNING|ERROR|FATAL|CRITICAL)\s+)?"
    r"(?P<message>.*)",
    re.IGNORECASE,
)
KV = re.compile(r"(?P<key>[A-Za-z_][\w.-]*)=(?P<value>\"[^\"]*\"|'[^']*'|\S+)")


class IngestionError(ValueError):
    pass


def _records_from_bytes(filename: str, data: bytes) -> list[dict[str, Any]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestionError("File must be UTF-8 encoded text.") from exc
    suffix = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    if suffix == "json":
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise IngestionError(f"Invalid JSON near line {exc.lineno}.") from exc
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for key in ("records", "data", "events", "items", "logs"):
                if isinstance(value.get(key), list):
                    return [item for item in value[key] if isinstance(item, dict)]
            return [value]
        raise IngestionError("JSON must be an object or list of objects.")
    if suffix == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise IngestionError("CSV must include a header row.")
        return [dict(row) for row in reader if any(value for value in row.values())]
    if suffix in {"txt", "log"}:
        return _parse_log_text(text)
    raise IngestionError("Supported uploads are .txt, .log, .json, and .csv.")


def _parse_log_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                rows.append(parsed)
                continue
        except json.JSONDecodeError:
            pass
        match = LOG_LINE.match(line)
        if match:
            row: dict[str, Any] = {
                "timestamp": match.group("timestamp"),
                "level": (match.group("level") or "").upper(),
                "message": match.group("message"),
                "line_number": line_number,
            }
            for key_value in KV.finditer(row["message"]):
                value = key_value.group("value").strip("\"'")
                row[key_value.group("key")] = value
            rows.append(row)
        else:
            # Malformed lines remain inspectable rather than stopping a whole upload.
            rows.append({"message": line, "parse_warning": f"Unstructured line {line_number}"})
    return rows


def parse_log_upload(
    filename: str, data: bytes, incident_id: str, max_bytes: int
) -> tuple[list[EvidenceInput], dict[str, Any], list[str]]:
    if len(data) > max_bytes:
        raise IngestionError(f"Upload is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    rows = _records_from_bytes(filename, data)
    evidence = []
    warnings: list[str] = []
    for index, row in enumerate(rows):
        normalized = {str(k).lower(): v for k, v in row.items()}
        timestamp = parse_timestamp(
            normalized.get("timestamp") or normalized.get("time") or normalized.get("created_at")
        )
        level = str(normalized.get("level", "")).upper()
        message = str(
            normalized.get("message")
            or normalized.get("content")
            or normalized.get("error")
            or json.dumps(row, ensure_ascii=False, default=str)
        )
        if normalized.get("parse_warning"):
            warnings.append(f"Row {index + 1}: {normalized['parse_warning']}")
        service = str(normalized.get("service") or "unknown")
        endpoint = str(normalized.get("endpoint") or "")
        error_type = str(
            normalized.get("error_type")
            or normalized.get("exception")
            or normalized.get("error")
            or ""
        )
        status = normalized.get("status_code") or normalized.get("status")
        metadata = {
            "level": level,
            "service": service,
            "endpoint": endpoint,
            "status_code": status,
            "error_type": error_type,
            "request_id": normalized.get("request_id"),
            "parse_warning": normalized.get("parse_warning"),
        }
        content = redact_secrets(
            f"{level + ' ' if level else ''}{message}"
        )[:12000]
        evidence.append(
            EvidenceInput(
                incident_id=incident_id,
                type=EvidenceType.LOG,
                source=f"{filename} · {service}",
                timestamp=timestamp,
                content=content,
                metadata=metadata,
            )
        )
    unique: dict[tuple[str, str, str], EvidenceInput] = {}
    for item in evidence:
        key = (item.timestamp.isoformat() if item.timestamp else "", item.content.casefold(), item.source)
        unique.setdefault(key, item)
    return list(unique.values()), summarize_logs(rows), warnings


def summarize_logs(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "row_count": 0,
            "error_count": 0,
            "error_rate": 0,
            "first_occurrence": None,
            "last_occurrence": None,
            "repeated_errors": [],
            "most_frequent_errors": [],
            "affected_services": [],
            "affected_endpoints": [],
            "status_code_distribution": {},
        }
    error_counts: Counter[str] = Counter()
    timestamps: list[datetime] = []
    services: set[str] = set()
    endpoints: set[str] = set()
    statuses: Counter[str] = Counter()
    error_count = 0
    for row in rows:
        normalized = {str(k).lower(): v for k, v in row.items()}
        level = str(normalized.get("level", "")).upper()
        status = str(normalized.get("status_code") or normalized.get("status") or "")
        is_error = level in {"ERROR", "FATAL", "CRITICAL"} or status.startswith(("4", "5")) or bool(
            normalized.get("error_type") or normalized.get("exception")
        )
        if is_error:
            error_count += 1
            signature = str(
                normalized.get("error_type")
                or normalized.get("exception")
                or normalized.get("message")
                or normalized.get("error")
                or "Unclassified error"
            )[:140]
            error_counts[signature] += 1
        stamp = parse_timestamp(normalized.get("timestamp") or normalized.get("time"))
        if stamp:
            timestamps.append(stamp)
        if normalized.get("service"):
            services.add(str(normalized["service"]))
        if normalized.get("endpoint"):
            endpoints.add(str(normalized["endpoint"]))
        if status:
            statuses[status] += 1
    return {
        "row_count": len(rows),
        "error_count": error_count,
        "error_rate": round(error_count / len(rows), 4) if rows else 0,
        "first_occurrence": min(timestamps).isoformat() if timestamps else None,
        "last_occurrence": max(timestamps).isoformat() if timestamps else None,
        "repeated_errors": [text for text, count in error_counts.items() if count > 1],
        "most_frequent_errors": [
            {"error": text, "count": count} for text, count in error_counts.most_common(5)
        ],
        "affected_services": sorted(services),
        "affected_endpoints": sorted(endpoints),
        "status_code_distribution": dict(statuses),
    }


def parse_structured_upload(
    filename: str,
    data: bytes,
    incident_id: str,
    evidence_type: EvidenceType,
    max_bytes: int,
) -> tuple[list[EvidenceInput], list[str]]:
    if len(data) > max_bytes:
        raise IngestionError(f"Upload is larger than the {max_bytes // (1024 * 1024)} MB limit.")
    records = _records_from_bytes(filename, data)
    warnings = []
    converted = normalize_records(records, incident_id, evidence_type.value, filename)
    if len(converted) < len(records):
        warnings.append(f"Skipped {len(records) - len(converted)} malformed or empty row(s).")
    if not converted and records:
        warnings.append("No usable evidence rows were found.")
    return converted, warnings


def metric_anomalies(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from statistics import mean, pstdev

    by_metric: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for item in evidence:
        if item.get("type") != EvidenceType.METRIC.value:
            continue
        meta = item.get("metadata", {})
        name = str(meta.get("metric_name") or "")
        value = meta.get("value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        by_metric.setdefault(name, []).append((number, item))
    anomalies = []
    for name, values in by_metric.items():
        nums = [v for v, _ in values]
        if len(nums) < 3:
            continue
        center = mean(nums)
        spread = pstdev(nums)
        if spread == 0:
            continue
        for value, item in values:
            zscore = abs((value - center) / spread)
            if zscore >= 2.0:
                anomalies.append(
                    {
                        "evidence_id": item["id"],
                        "metric_name": name,
                        "value": value,
                        "z_score": round(zscore, 2),
                        "direction": "above" if value > center else "below",
                    }
                )
    return anomalies


def github_repo_evidence(
    incident_id: str, repository: str, token: str = "", max_items: int = 10
) -> list[EvidenceInput]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository.strip()):
        raise IngestionError("Enter a public repository in owner/repository format.")
    owner, repo = repository.strip().split("/", 1)

    def request(path: str) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "AI-Incident-Investigator/1.0",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        url = f"https://api.github.com/repos/{owner}/{repo}/{path}"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                return json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise IngestionError("Repository not found or not publicly accessible.") from exc
            if exc.code == 403:
                raise IngestionError("GitHub request was rate-limited; try later or configure GITHUB_TOKEN.") from exc
            raise IngestionError(f"GitHub returned HTTP {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise IngestionError("Could not reach GitHub. Check the network and retry.") from exc

    limit = min(max(int(max_items), 1), 10)
    commits = request(f"commits?per_page={limit}")
    issues = request(f"issues?state=all&per_page={limit}")
    evidence: list[EvidenceInput] = []
    for item in commits if isinstance(commits, list) else []:
        commit = item.get("commit", {})
        author = commit.get("author", {})
        message = str(commit.get("message", ""))[:1000]
        sha = str(item.get("sha", ""))
        detail = request(f"commits/{sha}") if re.fullmatch(r"[0-9a-fA-F]{7,40}", sha) else {}
        files = (detail.get("files") or []) if isinstance(detail, dict) else []
        changed_files = [
            str(file.get("filename", ""))
            for file in files
            if isinstance(file, dict) and file.get("filename")
        ]
        stats = (detail.get("stats") or {}) if isinstance(detail, dict) else {}
        additions = int(stats.get("additions") or 0)
        deletions = int(stats.get("deletions") or 0)
        file_summary = ", ".join(changed_files[:12])
        commit_content = (
            f"Commit {sha[:12]} by {author.get('name', 'unknown')}: {message}"
            + (f" | changed files: {file_summary}" if file_summary else "")
        )
        evidence.append(
            EvidenceInput(
                incident_id=incident_id,
                type=EvidenceType.COMMIT,
                source=f"github:{repository}",
                timestamp=parse_timestamp(author.get("date")),
                content=redact_secrets(commit_content),
                metadata={
                    "sha": sha,
                    "author": author.get("name"),
                    "html_url": item.get("html_url"),
                    "changed_files": changed_files,
                    "additions": additions,
                    "deletions": deletions,
                },
            )
        )
    for item in issues if isinstance(issues, list) else []:
        if "pull_request" in item:
            continue
        label_names = [x.get("name", "") for x in item.get("labels", [])]
        evidence.append(
            EvidenceInput(
                incident_id=incident_id,
                type=EvidenceType.ISSUE,
                source=f"github:{repository}",
                timestamp=parse_timestamp(item.get("created_at")),
                content=redact_secrets(
                    f"Issue #{item.get('number')}: {item.get('title', '')} — {item.get('body') or ''}"
                )[:12000],
                metadata={
                    "issue_id": item.get("number"),
                    "status": item.get("state"),
                    "labels": label_names,
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "html_url": item.get("html_url"),
                },
            )
        )
    return evidence
