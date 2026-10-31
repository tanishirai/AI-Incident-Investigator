from __future__ import annotations

import json
import logging
import time
from typing import Any

from backend.schemas import InvestigationResult

logger = logging.getLogger("incident_investigator")


class GeminiUnavailable(RuntimeError):
    pass


def _parse_json_response(text: str) -> InvestigationResult:
    clean = text.strip()
    if clean.startswith("```"):
        clean = clean.removeprefix("```json").removeprefix("```").strip()
        if clean.endswith("```"):
            clean = clean[:-3].strip()
    try:
        return InvestigationResult.model_validate_json(clean)
    except Exception as exc:
        # Recover only a JSON object when a provider wraps it in short prose.
        start, end = clean.find("{"), clean.rfind("}")
        if start >= 0 and end > start:
            try:
                return InvestigationResult.model_validate(json.loads(clean[start : end + 1]))
            except Exception:
                pass
        raise GeminiUnavailable("Gemini returned a response that did not match the required JSON schema.") from exc


def analyze_with_gemini(
    api_key: str,
    model: str,
    system_instruction: str,
    user_prompt: str,
    retries: int = 2,
) -> tuple[InvestigationResult, dict[str, int]]:
    if not api_key:
        raise GeminiUnavailable("No Gemini API key is configured.")
    try:
        from google import genai
        from google.genai import types
    except ImportError as exc:
        raise GeminiUnavailable("The google-genai package is not installed.") from exc

    client = genai.Client(api_key=api_key, http_options=types.HttpOptions(timeout=45_000))
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    response_mime_type="application/json",
                    response_schema=InvestigationResult,
                    temperature=0.15,
                    max_output_tokens=4096,
                ),
            )
            text = getattr(response, "text", None)
            if not text or not text.strip():
                raise GeminiUnavailable("Gemini returned an empty response.")
            result = _parse_json_response(text)
            usage = getattr(response, "usage_metadata", None)
            input_tokens = int(getattr(usage, "prompt_token_count", 0) or 0)
            output_tokens = int(getattr(usage, "candidates_token_count", 0) or 0)
            return result, {"input_tokens": input_tokens, "output_tokens": output_tokens}
        except GeminiUnavailable:
            raise
        except Exception as exc:
            last_error = exc
            message = str(exc).casefold()
            retryable = any(term in message for term in ("429", "503", "timeout", "temporarily", "resource_exhausted"))
            if attempt >= retries or not retryable:
                break
            time.sleep(min(0.5 * (2**attempt), 2.0))
    logger.warning("Gemini request failed: %s", type(last_error).__name__ if last_error else "unknown error")
    raise GeminiUnavailable(_safe_provider_error(last_error)) from last_error


def _safe_provider_error(error: Exception | None) -> str:
    if error is None:
        return "Gemini request failed."
    message = str(error)
    for marker in ("AIza", "Bearer "):
        if marker in message:
            message = message.split(marker, 1)[0] + "[redacted]"
    message = " ".join(message.split())
    if any(term in message.casefold() for term in ("404", "not found", "model")):
        return "The configured Gemini model is unavailable. Check GEMINI_MODEL against Google's current model list."
    if any(term in message.casefold() for term in ("429", "resource_exhausted", "quota")):
        return "Gemini rate or quota limit was reached. Wait and retry; active limits vary by project and model."
    if "timeout" in message.casefold():
        return "Gemini did not respond before the request timed out."
    return f"Gemini request failed ({type(error).__name__}). Check the key, model, network, and developer logs."
