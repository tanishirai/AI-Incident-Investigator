from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from backend.baseline import analyze_deterministically
from backend.schemas import InvestigationResult


def evaluate_result(
    cases: list[dict[str, Any]],
    analyzer: Callable[[dict[str, Any], list[dict[str, Any]]], InvestigationResult],
    evidence_filter: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    recalls: list[float] = []
    precisions: list[float] = []
    unsupported: list[float] = []
    contradictions: list[float] = []
    durations: list[float] = []
    human_review: list[float] = []
    for case in cases:
        evidence = case["evidence"]
        if evidence_filter:
            evidence = [item for item in evidence if evidence_filter(item)]
        start = time.perf_counter()
        result = analyzer(case["incident"], evidence)
        durations.append(time.perf_counter() - start)
        expected = case["ground_truth"]
        matched = [
            hypothesis
            for hypothesis in result.hypotheses
            if expected.replace("_", " ").casefold() in hypothesis.title.casefold()
            or expected.casefold() in hypothesis.title.casefold()
            or expected.casefold() in hypothesis.description.casefold()
            or any(term.casefold() in hypothesis.title.casefold() for term in case.get("ground_truth_terms", []))
        ]
        recalls.append(float(bool(matched)))
        valid_ids = {item["id"] for item in evidence}
        cited = [
            evidence_id
            for hypothesis in result.hypotheses
            for evidence_id in hypothesis.evidence_ids
        ]
        relevant = set(case.get("supporting_evidence_ids", [])) & valid_ids
        precisions.append(
            len(set(cited) & relevant) / len(set(cited))
            if cited
            else 0.0
        )
        unsupported.append(
            sum(1 for hypothesis in result.hypotheses if not (set(hypothesis.evidence_ids) & valid_ids))
            / len(result.hypotheses)
            if result.hypotheses
            else 0.0
        )
        expected_contradictions = set(case.get("contradictory_evidence_ids", [])) & valid_ids
        cited_contradictions = {
            evidence_id
            for hypothesis in result.hypotheses
            for evidence_id in hypothesis.contradicting_evidence_ids
        }
        contradictions.append(
            len(expected_contradictions & cited_contradictions) / len(expected_contradictions)
            if expected_contradictions
            else 1.0
        )
        human_review.append(float(result.requires_human_review))
    count = max(len(cases), 1)
    return {
        "cases": len(cases),
        "root_cause_recall": round(sum(recalls) / count, 4),
        "evidence_precision": round(sum(precisions) / count, 4),
        "unsupported_claim_rate": round(sum(unsupported) / count, 4),
        "contradiction_awareness": round(sum(contradictions) / count, 4),
        "mean_investigation_seconds": round(sum(durations) / count, 6),
        "human_review_rate": round(sum(human_review) / count, 4),
    }


def run_baseline_evaluation(dataset_path: str | Path) -> dict[str, Any]:
    cases = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise ValueError("Evaluation dataset must be a JSON list.")
    all_types = {"LOG", "COMMIT", "DEPLOYMENT", "ISSUE", "METRIC", "PREVIOUS_INCIDENT"}
    tiers = {
        "timeline_logs": {"LOG"},
        "logs_commits_deployments": {"LOG", "COMMIT", "DEPLOYMENT"},
        "all_evidence": all_types,
    }
    ablations = {}
    # Time-only is an explicit negative-control slice: keep event times/types but remove
    # evidence wording and metadata so it cannot disclose the root-cause label.
    time_only = [
        {
            **case,
            "evidence": [
                {**item, "content": "", "metadata": {}}
                for item in case["evidence"]
            ],
        }
        for case in cases
    ]
    ablations["timeline_only"] = evaluate_result(
        time_only,
        lambda incident, evidence: analyze_deterministically(incident, evidence),
    )
    for tier, allowed in tiers.items():
        ablations[tier] = evaluate_result(
            cases,
            lambda incident, evidence: analyze_deterministically(incident, evidence),
            lambda item, allowed=allowed: item.get("type") in allowed,
        )
    result = {
        "dataset": "synthetic",
        "dataset_cases": len(cases),
        "baseline": ablations["all_evidence"],
        "ablation": ablations,
        "gemini": {"status": "not_run", "reason": "Live Gemini evaluation requires an explicit --with-gemini run."},
        "disclaimer": "Synthetic evaluation only; these results are not production performance claims.",
    }
    return result
