from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from backend.baseline import analyze_deterministically, attach_evidence_context
from backend.config import settings
from backend.database import Database
from backend.gemini import GeminiUnavailable, analyze_with_gemini
from backend.prompts import (
    PROMPT_VERSION,
    SYSTEM_INSTRUCTION,
    build_evidence_packet,
    build_user_prompt,
    cache_fingerprint,
)
from backend.schemas import InvestigationResult

logger = logging.getLogger("incident_investigator")


def _token_estimate(text: str) -> int:
    return max(1, len(text) // 4)


def run_investigation(
    db: Database,
    incident: dict[str, Any],
    evidence: list[dict[str, Any]],
    use_ai: bool = True,
    max_packet_chars: int | None = None,
    gemini_retries: int = 2,
) -> tuple[str, InvestigationResult]:
    started = time.perf_counter()
    logger.info("Investigation started incident=%s evidence_count=%d", incident["id"], len(evidence))
    baseline = analyze_deterministically(incident, evidence)
    fallback_notice = "AI investigation unavailable. Showing deterministic analysis."
    result: InvestigationResult
    if use_ai:
        if not settings.gemini_api_key:
            db.log_usage("fallback", incident["id"], details="No Gemini API key configured")
            logger.info("Deterministic fallback incident=%s reason=no_key", incident["id"])
            result = baseline.model_copy(deep=True)
            result.error_notice = fallback_notice
        else:
            packet, _included_ids = build_evidence_packet(
                incident,
                evidence,
                baseline.model_dump(),
                max_packet_chars or settings.max_packet_chars,
            )
            model = settings.gemini_model or "gemini-3.8-flash"
            key = cache_fingerprint(packet, model)
            cached = db.get_cache(key)
            if cached:
                db.log_usage("cache_hit", incident["id"], cache_hit=True, details=model)
                result = InvestigationResult.model_validate(cached["result"])
                result.method = "gemini_cached"
                result.model = model
                logger.info("Gemini cache hit incident=%s", incident["id"])
            else:
                db.log_usage("cache_miss", incident["id"], details=model)
                prompt = build_user_prompt(
                    packet,
                    json.dumps(InvestigationResult.model_json_schema(), ensure_ascii=False),
                )
                try:
                    result, usage = analyze_with_gemini(
                        settings.gemini_api_key,
                        model,
                        SYSTEM_INSTRUCTION,
                        prompt,
                        retries=gemini_retries,
                    )
                    db.log_usage(
                        "gemini_call",
                        incident["id"],
                        usage.get("input_tokens", 0) or _token_estimate(prompt),
                        usage.get("output_tokens", 0),
                        details=model,
                    )
                    result.method = "gemini"
                    result.model = model
                    result.usage = {
                        "input_tokens": usage.get("input_tokens", 0) or _token_estimate(prompt),
                        "output_tokens": usage.get("output_tokens", 0),
                    }
                    db.put_cache(key, model, {"result": result.model_dump(), "usage": result.usage})
                except GeminiUnavailable as exc:
                    db.log_usage("gemini_failure", incident["id"], details=str(exc))
                    db.log_usage("fallback", incident["id"], details="Gemini request failed")
                    logger.warning("Gemini fallback incident=%s: %s", incident["id"], str(exc))
                    result = baseline.model_copy(deep=True)
                    result.error_notice = fallback_notice
                    result.unknowns.append(f"Gemini unavailable: {exc}")
            if result.method.startswith("gemini") and not result.usage:
                result.usage = cached.get("usage", {}) if cached else {}
    else:
        result = baseline
    result = attach_evidence_context(result, evidence, incident)
    result.analysis_seconds = round(time.perf_counter() - started, 4)
    investigation_id = f"INV-{uuid.uuid4().hex[:12].upper()}"
    db.save_investigation(investigation_id, incident["id"], result.model_dump(), result.analysis_seconds)
    if incident.get("status") == "OPEN":
        db.update_incident_status(incident["id"], "INVESTIGATING")
    logger.info(
        "Investigation completed incident=%s method=%s duration=%.3fs",
        incident["id"],
        result.method,
        result.analysis_seconds,
    )
    return investigation_id, result
