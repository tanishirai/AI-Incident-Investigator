from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from backend.schemas import InvestigationResult
from backend.timestamps import parse_timestamp

CAUSES: dict[str, dict[str, Any]] = {
    "database_regression": {
        "title": "Database query regression",
        "terms": ("query", "sql", "database", "db", "migration", "slow query", "index", "plan"),
        "metric_terms": ("database_latency", "db_latency", "query_latency", "query_duration", "slow_queries"),
        "diagnostic": "Compare query plans and database latency for the affected endpoint before and after the relevant change.",
    },
    "configuration_error": {
        "title": "Configuration error",
        "terms": ("config", "configuration", "environment variable", "env var", "timeout setting", "misconfigured"),
        "metric_terms": ("config_error_rate",),
        "diagnostic": "Diff the active runtime configuration against the last known-good deployment, with secret values redacted.",
    },
    "external_api_failure": {
        "title": "External API degradation",
        "terms": ("third-party", "external api", "provider", "upstream api", "vendor", "429"),
        "metric_terms": ("upstream_latency", "provider_latency", "external_api_error_rate"),
        "diagnostic": "Check provider status and correlate upstream response codes and latency with the incident window.",
    },
    "connection_exhaustion": {
        "title": "Database connection exhaustion",
        "terms": ("connection pool", "connections exhausted", "pool timeout", "too many connections", "connection limit"),
        "metric_terms": ("db_connections", "database_connections", "connection_wait", "pool_wait"),
        "diagnostic": "Inspect active, idle, and waiting database connections alongside pool limits and request concurrency.",
    },
    "feature_flag_error": {
        "title": "Feature flag configuration error",
        "terms": ("feature flag", "flag enabled", "rollout", "experiment", "toggle"),
        "metric_terms": ("flag_error_rate",),
        "diagnostic": "Compare flag targeting and rollout changes with affected users, regions, and request traces.",
    },
    "memory_leak": {
        "title": "Memory growth or leak",
        "terms": ("memory leak", "heap", "out of memory", "oom", "memory usage", "rss"),
        "metric_terms": ("memory_usage", "heap_usage", "rss"),
        "diagnostic": "Compare process memory and garbage-collection behavior across deployment age and request volume.",
    },
    "incorrect_deployment": {
        "title": "Incorrect deployment or release regression",
        "terms": ("deployment", "deploy", "release", "rollback", "version", "build"),
        "diagnostic": "Compare the deployed artifact, change list, and configuration against the last healthy release.",
    },
    "authentication_failure": {
        "title": "Authentication or authorization failure",
        "terms": ("auth", "authentication", "authorization", "oauth", "token expired", "401", "403"),
        "metric_terms": ("auth_error_rate", "authentication_error_rate"),
        "diagnostic": "Inspect identity-provider errors, token expiry, permission changes, and affected request paths.",
    },
    "network_timeout": {
        "title": "Network timeout or connectivity issue",
        "terms": ("network", "timeout", "connection reset", "dns", "socket", "unreachable"),
        "metric_terms": ("network_latency", "network_errors", "timeout_rate"),
        "diagnostic": "Check service-to-service connectivity, DNS resolution, packet loss, and timeout distributions.",
    },
    "cache_invalidation_issue": {
        "title": "Cache invalidation or stale data issue",
        "terms": ("cache", "stale", "invalidation", "ttl", "miss rate", "redis"),
        "metric_terms": ("cache_miss_rate", "cache_hit_rate", "cache_evictions"),
        "diagnostic": "Compare cache hit/miss behavior, invalidation events, and source-of-truth values for affected keys.",
    },
}
CONTRADICTION_CUES = (
    "healthy", "no errors", "unchanged", "not affected", "no increase", "normal",
    "not reproduced", "successful", "recovered before", "no regression",
    "no config drift", "was disabled", "within pool limit", "no stale",
)


def _time_score(incident: dict[str, Any], item: dict[str, Any]) -> float:
    start = parse_timestamp(incident.get("started_at"))
    stamp = parse_timestamp(item.get("timestamp"))
    if not start or not stamp:
        return 0.0
    delta_minutes = abs((stamp - start).total_seconds()) / 60
    return max(0.0, 1 - min(delta_minutes / 180, 1))


def _service_match(incident: dict[str, Any], item: dict[str, Any]) -> bool:
    service = str(incident.get("service") or "").casefold()
    content = f"{item.get('source', '')} {item.get('content', '')} {item.get('metadata', {})}".casefold()
    return bool(service and service != "unknown" and service in content)


def analyze_deterministically(
    incident: dict[str, Any], evidence: list[dict[str, Any]], max_hypotheses: int = 5
) -> InvestigationResult:
    evidence = evidence[:120]
    searchable = [
        (
            item,
            f"{item.get('content', '')} {item.get('metadata', {})}".casefold(),
        )
        for item in evidence
    ]
    from backend.ingestion import metric_anomalies

    anomaly_ids = {item["evidence_id"] for item in metric_anomalies(evidence)}
    candidates: list[dict[str, Any]] = []
    for cause_key, cause in CAUSES.items():
        supporting: list[dict[str, Any]] = []
        contradicting: list[dict[str, Any]] = []
        matched_terms: set[str] = set()
        sources: set[str] = set()
        temporal_sum = 0.0
        service_hits = 0
        similar_previous_incidents = 0
        metric_anomaly_ids: list[str] = []
        for item, text in searchable:
            hits = {term for term in cause["terms"] if term in text}
            metric_name = str(item.get("metadata", {}).get("metric_name") or "").casefold()
            metric_hit = (
                item.get("id") in anomaly_ids
                and any(term in metric_name for term in cause.get("metric_terms", ()))
            )
            if not hits and not metric_hit:
                continue
            matched_terms.update(hits)
            sources.add(str(item.get("type", "")))
            if metric_hit:
                metric_anomaly_ids.append(str(item["id"]))
            if item.get("type") == "PREVIOUS_INCIDENT":
                similar_previous_incidents += 1
            temporal_sum += _time_score(incident, item)
            service_hits += int(_service_match(incident, item))
            has_contradiction = any(cue in text for cue in CONTRADICTION_CUES)
            (contradicting if has_contradiction else supporting).append(item)
        if not supporting and not contradicting:
            continue
        corroboration = min(len(sources) / 3, 1)
        temporal = min(temporal_sum / max(1, len(supporting) + len(contradicting)), 1)
        service = min(service_hits / 2, 1)
        direct_match = min(len(matched_terms) / 3, 1)
        previous_incident_similarity = min(similar_previous_incidents / 2, 1)
        metric_signal = min(len(metric_anomaly_ids) / 2, 1)
        score = (
            0.27 * direct_match
            + 0.17 * corroboration
            + 0.15 * temporal
            + 0.10 * service
            + 0.10 * min(len(supporting) / 8, 1)
            + 0.08 * previous_incident_similarity
            + 0.13 * metric_signal
        )
        score -= min(len(contradicting) / 4, 1) * 0.18
        score = min(max(score, 0.05), 0.88)
        if not supporting:
            score = min(score, 0.25)
        candidates.append(
            {
                "cause_key": cause_key,
                "cause": cause,
                "supporting": supporting[:8],
                "contradicting": contradicting[:6],
                "score": score,
                "sources": sources,
                "matched_terms": sorted(matched_terms),
                "temporal": temporal,
                "previous_incident_similarity": previous_incident_similarity,
                "metric_anomaly_ids": metric_anomaly_ids,
            }
        )
    candidates.sort(key=lambda item: (item["score"], len(item["supporting"])), reverse=True)
    hypotheses = []
    for candidate in candidates[:max_hypotheses]:
        if candidate["score"] < 0.14:
            continue
        cause = candidate["cause"]
        rule_matches = ", ".join(candidate["matched_terms"][:5]) or "a type-specific metric outlier"
        hypotheses.append(
            {
                "title": cause["title"],
                "description": (
                    f"Rule matches: {rule_matches}. "
                    f"Corroboration spans {len(candidate['sources'])} evidence source type(s). "
                    f"Lexical match to prior incidents: {candidate['previous_incident_similarity']:.0%}; "
                    f"relevant metric anomalies: {len(candidate['metric_anomaly_ids'])}."
                ),
                "confidence": round(candidate["score"], 3),
                "heuristic_confidence": round(candidate["score"], 3),
                "evidence_ids": [item["id"] for item in candidate["supporting"]],
                "contradicting_evidence_ids": [item["id"] for item in candidate["contradicting"]],
                "reasoning": (
                    "Deterministic lexical, temporal, service, and source-diversity signals; "
                    "correlation alone does not establish causation."
                ),
                "suggested_diagnostic": cause["diagnostic"],
                "severity_if_true": incident.get("severity", "MEDIUM"),
                "hypothesis_id": candidate["cause_key"],
            }
        )
    total_errors = sum(
        1 for item in evidence
        if item.get("type") == "LOG"
        and any(word in str(item.get("content", "")).casefold() for word in ("error", "exception", "timeout", "failed"))
    )
    unknowns = []
    if not evidence:
        unknowns.append("No evidence has been attached to this incident.")
    if not any(item.get("type") == "LOG" for item in evidence):
        unknowns.append("No application log evidence is available.")
    if not any(item.get("type") in {"DEPLOYMENT", "COMMIT"} for item in evidence):
        unknowns.append("No deployment or commit evidence is available to assess recent change risk.")
    if not any(item.get("type") == "METRIC" for item in evidence):
        unknowns.append("No metric time series is available to confirm the impact pattern.")
    if not hypotheses:
        unknowns.append("Available evidence did not match a supported cause rule strongly enough to rank a hypothesis.")
    if len({item.get("type") for item in evidence}) < 2:
        unknowns.append("Cross-source corroboration is limited.")
    summary = (
        f"Deterministic analysis reviewed {len(evidence)} evidence items across "
        f"{len({item.get('type') for item in evidence})} source types. "
        f"It found {total_errors} log items with error-like terms and ranked "
        f"{len(hypotheses)} rule-based hypothesis/hypotheses. Human verification is required."
    )
    return InvestigationResult(
        investigation_summary=summary,
        hypotheses=hypotheses,
        unknowns=unknowns,
        recommended_next_steps=[
            "Verify the top hypothesis against the original evidence and a service owner.",
            "Add missing logs, deployment/commit history, and metrics for the incident window.",
            "Run the suggested diagnostic before changing production configuration.",
        ],
        requires_human_review=True,
        evidence_status="SUFFICIENT_EVIDENCE" if hypotheses else "INSUFFICIENT_EVIDENCE",
        method="deterministic",
    )


def calculate_confidence(
    hypothesis: dict[str, Any],
    evidence: list[dict[str, Any]],
    incident_service: str = "",
    incident_started_at: Any = None,
) -> float:
    """Transparent ranking signal; deliberately not described as calibrated probability."""
    by_id = {item["id"]: item for item in evidence}
    supporting = [by_id[i] for i in hypothesis.get("evidence_ids", []) if i in by_id]
    contradicting = [
        by_id[i] for i in hypothesis.get("contradicting_evidence_ids", []) if i in by_id
    ]
    ai = float(hypothesis.get("confidence", 0.35))
    source_types = {item.get("type") for item in supporting}
    incident_service = str(incident_service or hypothesis.get("service", "")).casefold()
    service_signal = (
        sum(
            incident_service in str(item.get("content", "")).casefold()
            or incident_service in str(item.get("source", "")).casefold()
            or incident_service in str(item.get("metadata", {}).get("service", "")).casefold()
            for item in supporting
        )
        / len(supporting)
        if supporting and incident_service
        else 0.0
    )
    count_signal = min(len(supporting) / 5, 1)
    agreement = min(len(source_types) / 3, 1)
    incident_stub = {"started_at": incident_started_at}
    temporal = (
        sum(_time_score(incident_stub, item) for item in supporting) / len(supporting)
        if supporting and incident_started_at
        else 0.0
    )
    contradiction_penalty = min(len(contradicting) / 4, 1) * 0.25
    result = (
        0.40 * ai
        + 0.15 * count_signal
        + 0.15 * agreement
        + 0.15 * temporal
        + 0.15 * service_signal
    )
    return round(min(0.99, max(0.01, result - contradiction_penalty)), 3)


def attach_evidence_context(
    result: InvestigationResult,
    evidence: list[dict[str, Any]],
    incident: dict[str, Any] | None = None,
) -> InvestigationResult:
    available = {item["id"] for item in evidence}
    for index, hypothesis in enumerate(result.hypotheses, start=1):
        hypothesis.evidence_ids = list(dict.fromkeys(i for i in hypothesis.evidence_ids if i in available))
        hypothesis.contradicting_evidence_ids = list(
            dict.fromkeys(i for i in hypothesis.contradicting_evidence_ids if i in available)
        )
        if not hypothesis.hypothesis_id:
            import hashlib

            stable = hashlib.sha1(hypothesis.title.casefold().encode()).hexdigest()[:10]
            hypothesis.hypothesis_id = f"HYP-{index}-{stable}"
        hypothesis.heuristic_confidence = calculate_confidence(
            hypothesis.model_dump(),
            evidence,
            str((incident or {}).get("service") or ""),
            (incident or {}).get("started_at"),
        )
    if not evidence or not any(
        hypothesis.evidence_ids or hypothesis.contradicting_evidence_ids
        for hypothesis in result.hypotheses
    ):
        result.evidence_status = "INSUFFICIENT_EVIDENCE"
    result.requires_human_review = True
    return result
