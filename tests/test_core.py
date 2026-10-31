from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.baseline import (
    _time_score,
    analyze_deterministically,
    attach_evidence_context,
    calculate_confidence,
)
from backend.database import Database
from backend.evaluation import evaluate_result
from backend.gemini import GeminiUnavailable, _parse_json_response
from backend.ingestion import (
    github_repo_evidence,
    metric_anomalies,
    parse_log_upload,
    summarize_logs,
)
from backend.investigation import run_investigation
from backend.normalization import deduplicate_evidence, normalize_records
from backend.prompts import build_evidence_packet, cache_fingerprint
from backend.schemas import (
    EvidenceInput,
    EvidenceType,
    Hypothesis,
    IncidentInput,
    InvestigationResult,
)
from backend.timestamps import parse_timestamp
from backend.timeline import build_timeline


def incident(start: datetime | None = None) -> dict:
    start = start or datetime(2025, 1, 1, 14, tzinfo=timezone.utc)
    return {
        "id": "INC-TEST",
        "title": "Checkout query latency",
        "description": "Latency increased after a recent release.",
        "severity": "HIGH",
        "service": "checkout-api",
        "endpoint": "/checkout",
        "started_at": start.isoformat(),
        "status": "OPEN",
    }


def ev(eid: str, kind: str, content: str, offset: int = 1, source: str = "checkout-api") -> dict:
    stamp = datetime(2025, 1, 1, 14, tzinfo=timezone.utc) + timedelta(minutes=offset)
    return {
        "id": eid,
        "incident_id": "INC-TEST",
        "type": kind,
        "source": source,
        "timestamp": stamp.isoformat(),
        "content": content,
        "metadata": {"service": "checkout-api"},
    }


def test_timestamp_parsing_iso_z_naive_and_invalid():
    assert parse_timestamp("2025-01-02T10:00:00Z").utcoffset() == timedelta(0)
    assert parse_timestamp("2025-01-02 10:00:00").tzinfo == timezone.utc
    assert parse_timestamp("not-a-date") is None


def test_log_parsing_json_line_and_malformed_rows():
    raw = (
        b'{"timestamp":"2025-01-01T14:01:00Z","level":"ERROR","service":"checkout-api","message":"query timeout","status_code":504}\n'
        b"not a structured row\n"
    )
    evidence, summary, warnings = parse_log_upload("app.log", raw, "INC-TEST", 10000)
    assert len(evidence) == 2
    assert evidence[0].metadata["level"] == "ERROR"
    assert summary["error_count"] == 1
    assert summary["error_rate"] == 0.5
    assert warnings


def test_error_aggregation_counts_status_levels_and_common_errors():
    summary = summarize_logs(
        [
            {"timestamp": "2025-01-01T14:00:00Z", "level": "ERROR", "error_type": "QueryTimeout", "service": "a", "endpoint": "/x", "status_code": 504},
            {"timestamp": "2025-01-01T14:02:00Z", "level": "ERROR", "error_type": "QueryTimeout", "service": "a", "endpoint": "/x", "status_code": 500},
            {"timestamp": "2025-01-01T14:03:00Z", "level": "INFO", "message": "ok", "status_code": 200},
        ]
    )
    assert summary["error_count"] == 2
    assert summary["repeated_errors"] == ["QueryTimeout"]
    assert summary["affected_services"] == ["a"]
    assert summary["status_code_distribution"]["504"] == 1
    assert summary["first_occurrence"].endswith("+00:00")


def test_evidence_normalization_and_duplicate_removal():
    records = [
        {"timestamp": "2025-01-01T14:00:00Z", "service": "checkout-api", "message": "Database timeout"},
        {"timestamp": "2025-01-01T14:00:00Z", "service": "checkout-api", "message": "Database timeout"},
    ]
    normalized = normalize_records(records, "INC-TEST", "LOG", "upload")
    assert len(normalized) == 1
    assert normalized[0].metadata["service"] == "checkout-api"
    assert deduplicate_evidence(normalized) == normalized


def test_timeline_is_chronological_and_includes_incident_start():
    items = [ev("EVID-0002", "COMMIT", "changed code", 4), ev("EVID-0001", "LOG", "timeout", 1)]
    timeline = build_timeline(incident(), items)
    assert [item["type"] for item in timeline] == ["INCIDENT", "LOG", "COMMIT"]


def test_deployment_proximity_score_decays_with_time():
    case = incident()
    close = ev("EVID-0001", "DEPLOYMENT", "deployment v1", 2)
    far = ev("EVID-0002", "DEPLOYMENT", "deployment v2", 181)
    assert _time_score(case, close) > _time_score(case, far)
    assert _time_score(case, far) == 0


def test_metric_anomaly_flags_large_outlier():
    evidence = [
        {
            "id": f"EVID-{index:04d}",
            "type": "METRIC",
            "metadata": {"metric_name": "latency_ms", "value": value},
        }
        for index, value in enumerate([5, 5, 5, 5, 5, 5, 5, 100], start=1)
    ]
    anomalies = metric_anomalies(evidence)
    assert len(anomalies) == 1
    assert anomalies[0]["evidence_id"] == "EVID-0008"
    assert anomalies[0]["direction"] == "above"


def test_metric_anomaly_contributes_to_matching_baseline_hypothesis():
    metrics = []
    for index, value in enumerate([5, 5, 5, 5, 5, 5, 5, 100], start=1):
        item = ev(f"EVID-{index:04d}", "METRIC", f"database latency metric value={value}", index)
        item["metadata"].update({"metric_name": "database_latency", "value": value})
        metrics.append(item)
    evidence = metrics + [ev("EVID-0009", "LOG", "Database query regression", 1)]
    result = analyze_deterministically(incident(), evidence)
    database_hypothesis = next(
        hypothesis for hypothesis in result.hypotheses
        if "database query" in hypothesis.title.casefold()
    )
    assert "EVID-0008" in database_hypothesis.evidence_ids
    assert "relevant metric anomalies: 1" in database_hypothesis.description


def test_commit_matching_produces_root_cause_candidate():
    evidence = [
        ev("EVID-0001", "COMMIT", "Commit changed database query and SQL index plan", -5),
        ev("EVID-0002", "LOG", "ERROR database query timeout after new index", 2),
    ]
    result = analyze_deterministically(incident(), evidence)
    assert any("database query regression" in h.title.casefold() for h in result.hypotheses)


def test_previous_incident_lexical_similarity_changes_candidate_score():
    current_evidence = [ev("EVID-0001", "LOG", "Database query regression is causing timeouts", 1)]
    prior = ev(
        "EVID-0002",
        "PREVIOUS_INCIDENT",
        "title=Checkout latency | root_cause=database query regression | resolution=index update",
        -5000,
        source="archive",
    )
    plain = analyze_deterministically(incident(), current_evidence)
    with_history = analyze_deterministically(incident(), current_evidence + [prior])
    plain_db = next(h for h in plain.hypotheses if "database query" in h.title.casefold())
    history_db = next(h for h in with_history.hypotheses if "database query" in h.title.casefold())
    assert history_db.confidence > plain_db.confidence
    assert "prior incidents" in history_db.description.casefold()


def test_github_commit_metadata_and_issue_timestamps_are_retrieved(monkeypatch):
    sha = "a" * 40
    responses = {
        "commits?per_page=1": [
            {
                "sha": sha,
                "html_url": "https://github.com/acme/shop/commit/" + sha,
                "commit": {
                    "message": "adjust checkout query",
                    "author": {"name": "Dev", "date": "2025-01-01T13:55:00Z"},
                },
            }
        ],
        f"commits/{sha}": {
            "stats": {"additions": 7, "deletions": 2},
            "files": [{"filename": "services/checkout/query.py"}],
        },
        "issues?state=all&per_page=1": [
            {
                "number": 12,
                "title": "Checkout latency",
                "body": "Slow query after release",
                "state": "open",
                "labels": [{"name": "incident"}],
                "created_at": "2025-01-01T14:00:00Z",
                "updated_at": "2025-01-01T14:20:00Z",
                "html_url": "https://github.com/acme/shop/issues/12",
            }
        ],
    }

    def fake_urlopen(request, timeout):
        path = request.full_url.split("/repos/acme/shop/", 1)[1]
        return io.BytesIO(json.dumps(responses[path]).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    evidence = github_repo_evidence("INC-TEST", "acme/shop", max_items=1)
    commit = next(item for item in evidence if item.type.value == "COMMIT")
    issue = next(item for item in evidence if item.type.value == "ISSUE")
    assert commit.metadata["changed_files"] == ["services/checkout/query.py"]
    assert commit.metadata["additions"] == 7
    assert commit.metadata["deletions"] == 2
    assert "services/checkout/query.py" in commit.content
    assert issue.metadata["labels"] == ["incident"]
    assert issue.metadata["updated_at"] == "2025-01-01T14:20:00Z"


def test_confidence_penalizes_contradictions_and_rewards_cross_source_support():
    hypothesis = {
        "confidence": 0.7,
        "evidence_ids": ["EVID-0001", "EVID-0002"],
        "contradicting_evidence_ids": [],
        "service": "checkout-api",
    }
    items = [
        ev("EVID-0001", "LOG", "database query timeout", 1),
        ev("EVID-0002", "METRIC", "latency elevated", 2),
        ev("EVID-0003", "LOG", "database is healthy and unchanged", 3),
    ]
    positive = calculate_confidence(hypothesis, items)
    hypothesis["contradicting_evidence_ids"] = ["EVID-0003"]
    negative = calculate_confidence(hypothesis, items)
    assert 0 <= negative < positive <= 1


def test_evidence_links_are_filtered_and_hypothesis_id_is_stable():
    result = InvestigationResult(
        investigation_summary="Analysis",
        hypotheses=[
            Hypothesis(
                title="Database regression",
                description="A query regression is possible.",
                confidence=0.7,
                evidence_ids=["EVID-0001", "EVID-BOGUS"],
                contradicting_evidence_ids=["EVID-0002", "EVID-UNKNOWN"],
            )
        ],
    )
    attached = attach_evidence_context(result, [ev("EVID-0001", "LOG", "query timeout")])
    assert attached.hypotheses[0].evidence_ids == ["EVID-0001"]
    assert attached.hypotheses[0].contradicting_evidence_ids == []
    assert attached.hypotheses[0].hypothesis_id.startswith("HYP-")
    assert attached.requires_human_review is True


def test_contradictions_are_explicitly_attributed():
    items = [
        ev("EVID-0001", "LOG", "ERROR database query timeout after index change", 1),
        ev("EVID-0002", "METRIC", "database is healthy and unchanged", 2),
    ]
    result = analyze_deterministically(incident(), items)
    hypothesis = next(h for h in result.hypotheses if "database" in h.title.casefold())
    assert "EVID-0001" in hypothesis.evidence_ids
    assert "EVID-0002" in hypothesis.contradicting_evidence_ids


def test_cache_fingerprint_is_stable_and_model_specific():
    a = cache_fingerprint("evidence packet", "gemini-3.8-flash")
    b = cache_fingerprint("  evidence   packet ", "gemini-3.8-flash")
    c = cache_fingerprint("evidence packet", "different-model")
    assert a == b
    assert a != c


def test_evidence_packet_cites_only_selected_ids_and_respects_budget():
    items = [ev(f"EVID-{i:04d}", "LOG", f"ERROR database query timeout {i}", i) for i in range(1, 20)]
    baseline = analyze_deterministically(incident(), items).model_dump()
    packet, included = build_evidence_packet(incident(), items, baseline, max_chars=4000)
    assert len(packet) <= 4000
    assert all(item_id in packet for item_id in included)
    assert "EVID-" in packet


def test_gemini_schema_accepts_insufficient_evidence_and_requires_human_review():
    payload = {
        "investigation_summary": "Not enough supplied evidence.",
        "hypotheses": [],
        "unknowns": ["No logs supplied."],
        "recommended_next_steps": ["Collect logs."],
        "requires_human_review": False,
        "evidence_status": "INSUFFICIENT_EVIDENCE",
    }
    result = InvestigationResult.model_validate(payload)
    assert result.evidence_status == "INSUFFICIENT_EVIDENCE"
    hypothesis_payload = {
        **payload,
        "hypotheses": [{
            "title": "Possible config issue",
            "description": "A setting could be misconfigured.",
            "confidence": 0.4,
        }],
    }
    result_with_hypothesis = InvestigationResult.model_validate(hypothesis_payload)
    assert result_with_hypothesis.requires_human_review is True


def test_malformed_gemini_response_fails_as_clear_provider_error():
    with pytest.raises(GeminiUnavailable, match="JSON schema"):
        _parse_json_response("This is not JSON at all.")


def test_sqlite_cache_round_trip_and_clear(tmp_path):
    db = Database(tmp_path / "test.db")
    db.put_cache("abc", "gemini-3.8-flash", {"result": {"method": "gemini"}})
    assert db.get_cache("abc") == {"result": {"method": "gemini"}}
    assert db.clear_cache() == 1
    assert db.get_cache("abc") is None


def test_no_key_falls_back_without_gemini_and_saves_investigation(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.db")
    db.create_incident(
        IncidentInput(title="Checkout incident", started_at=parse_timestamp("2025-01-01T14:00:00Z"), service="checkout-api"),
        "INC-TEST",
    )
    evidence = [ev("EVID-0001", "LOG", "ERROR database query timeout", 1)]
    monkeypatch.setattr("backend.investigation.settings", SimpleNamespace(gemini_api_key="", gemini_model="gemini-3.8-flash", max_packet_chars=18000))
    investigation_id, result = run_investigation(db, incident(), evidence, use_ai=True)
    assert investigation_id.startswith("INV-")
    assert result.method == "deterministic"
    assert result.error_notice == "AI investigation unavailable. Showing deterministic analysis."
    assert db.latest_investigation("INC-TEST") is not None


def test_gemini_cache_prevents_second_provider_call(tmp_path, monkeypatch):
    db = Database(tmp_path / "test.db")
    inc = incident()
    db.create_incident(
        IncidentInput(title=inc["title"], started_at=parse_timestamp(inc["started_at"]), service=inc["service"]),
        inc["id"],
    )
    evidence = [ev("EVID-0001", "LOG", "ERROR database query timeout", 1)]
    calls = []

    def fake_gemini(*args, **kwargs):
        calls.append(1)
        return InvestigationResult(
            investigation_summary="Grounded analysis",
            hypotheses=[
                Hypothesis(
                    title="Database query regression",
                    description="A query regression is supported.",
                    confidence=0.7,
                    evidence_ids=["EVID-0001"],
                )
            ],
        ), {"input_tokens": 100, "output_tokens": 25}

    monkeypatch.setattr("backend.investigation.settings", SimpleNamespace(gemini_api_key="dummy-test-key", gemini_model="gemini-3.8-flash", max_packet_chars=18000))
    monkeypatch.setattr("backend.investigation.analyze_with_gemini", fake_gemini)
    _, first = run_investigation(db, inc, evidence)
    _, second = run_investigation(db, inc, evidence)
    assert len(calls) == 1
    assert first.method == "gemini"
    assert second.method == "gemini_cached"
    assert db.dashboard_stats()["usage"]["hits"] == 1


def test_evaluation_metrics_use_labeled_evidence_and_review_rate():
    evidence = [
        ev("EVID-0001", "LOG", "database query timeout", 1),
        ev("EVID-0002", "METRIC", "database healthy and unchanged", 2),
    ]
    case = {
        "incident": incident(),
        "ground_truth": "database_regression",
        "ground_truth_terms": ["database query regression"],
        "evidence": evidence,
        "supporting_evidence_ids": ["EVID-0001"],
        "contradictory_evidence_ids": ["EVID-0002"],
    }
    metrics = evaluate_result([case], lambda inc, items: analyze_deterministically(inc, items))
    assert metrics["cases"] == 1
    assert metrics["root_cause_recall"] == 1
    assert metrics["evidence_precision"] > 0
    assert metrics["contradiction_awareness"] == 1
    assert metrics["human_review_rate"] == 1


def test_gemini_benchmark_uses_isolated_sqlite_and_supports_mocking(tmp_path, monkeypatch):
    import run_evaluation

    case = {
        "incident": incident(),
        "ground_truth": "database_regression",
        "ground_truth_terms": ["database query regression"],
        "evidence": [ev("EVID-0001", "LOG", "database query regression", 1)],
        "supporting_evidence_ids": ["EVID-0001"],
        "contradictory_evidence_ids": [],
    }

    def fake_investigation(db, inc, evidence, use_ai=True, gemini_retries=2):
        assert db.get_incident(inc["id"]) is not None
        assert use_ai is True
        assert gemini_retries == 0
        return "INV-MOCK", analyze_deterministically(inc, evidence)

    monkeypatch.setattr(run_evaluation, "run_investigation", fake_investigation)
    metrics = run_evaluation.evaluate_gemini_cases([case])
    assert metrics["cases"] == 1
    assert metrics["root_cause_recall"] == 1
