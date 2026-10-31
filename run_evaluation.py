from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from backend.config import settings
from backend.evaluation import evaluate_result, run_baseline_evaluation
from backend.investigation import run_investigation
from backend.database import Database
from backend.schemas import IncidentInput

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "evaluation" / "dataset" / "synthetic_incidents.json"


def evaluate_gemini_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Use an isolated temporary SQLite store so benchmark runs cannot touch user data."""
    with TemporaryDirectory(prefix="incident-investigator-eval-") as directory:
        db = Database(Path(directory) / "evaluation.sqlite")

        def gemini_analyzer(incident: dict[str, Any], evidence: list[dict[str, Any]]):
            incident_id = str(incident["id"])
            if db.get_incident(incident_id):
                raise ValueError(f"Duplicate evaluation incident ID: {incident_id}")
            db.create_incident(
                IncidentInput.model_validate(
                    {
                        key: incident[key]
                        for key in IncidentInput.model_fields
                        if key in incident
                    }
                ),
                incident_id,
            )
            _, result = run_investigation(
                db,
                incident,
                evidence,
                use_ai=True,
                gemini_retries=0,
            )
            return result

        return evaluate_result(cases, gemini_analyzer)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the deterministic baseline and optionally Gemini.")
    parser.add_argument("--with-gemini", action="store_true", help="Run live Gemini once per case; uses API quota.")
    parser.add_argument("--output", default=str(ROOT / "evaluation" / "results.json"))
    args = parser.parse_args()
    cases: list[dict[str, Any]] = json.loads(DATASET.read_text(encoding="utf-8"))
    report = run_baseline_evaluation(DATASET)
    if args.with_gemini:
        if not settings.gemini_api_key:
            report["gemini"] = {"status": "skipped", "reason": "GEMINI_API_KEY is not configured."}
        else:
            report["gemini"] = {
                "status": "measured",
                "metrics": evaluate_gemini_cases(cases),
                "note": "Each case uses a separate incident packet. Cache reuse depends on identical evidence.",
            }
    else:
        report["gemini"] = {
            "status": "not_run",
            "reason": "Pass --with-gemini to make live model calls. This avoids accidental quota use.",
        }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2), encoding="utf-8")
    baseline = report["baseline"]
    lines = [
        "# Measured evaluation report",
        "",
        f"- Dataset: **{report['dataset']}**, {report['dataset_cases']} synthetic cases.",
        f"- Root-cause recall: {baseline['root_cause_recall']:.1%}",
        f"- Evidence precision: {baseline['evidence_precision']:.1%}",
        f"- Unsupported-claim rate: {baseline['unsupported_claim_rate']:.1%}",
        f"- Contradiction awareness: {baseline['contradiction_awareness']:.1%}",
        f"- Mean processing time: {baseline['mean_investigation_seconds'] * 1000:.3f} ms/case on this run's machine.",
        f"- Human-review rate: {baseline['human_review_rate']:.1%}",
        "",
        "## Evidence ablations",
        "",
        "| Slice | Recall | Evidence precision | Unsupported rate | Contradiction awareness |",
        "|---|---:|---:|---:|---:|",
    ]
    for name, metrics in report["ablation"].items():
        lines.append(
            f"| {name.replace('_', ' ').title()} | {metrics['root_cause_recall']:.1%} "
            f"| {metrics['evidence_precision']:.1%} | {metrics['unsupported_claim_rate']:.1%} "
            f"| {metrics['contradiction_awareness']:.1%} |"
        )
    lines.extend(
        [
            "",
            f"## Gemini: {report['gemini']['status']}",
            "",
            report["gemini"].get("reason") or report["gemini"].get("note", ""),
            "",
            report["disclaimer"],
            "Synthetic benchmark metrics are not production accuracy or engineer time saved.",
        ]
    )
    (ROOT / "evaluation" / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
