# Gemini integration and quota strategy

## SDK and model selection

This project uses Google's official `google-genai` Python SDK (`from google import genai`), the SDK Google currently recommends for Gemini API work. It calls the `generate_content` API with JSON response mode and a Pydantic response schema. The model is configured by `GEMINI_MODEL`; the packaged example uses `gemini-3.8-flash`, which appeared in Google's current official model and pricing documentation when this project was built. Model IDs and access change; use the current [model list](https://ai.google.dev/gemini-api/docs/models) and set a model enabled for your AI Studio project.

## Free-tier note

Google's official Gemini Developer API pricing page currently lists free-of-charge input and output tokens for selected models and limits the models/features available on the free tier. Its free-tier terms state that submitted content may be used to improve Google products. Active rate limits are model-, project-, and account-dependent and can change; inspect the project's live [AI Studio rate limits](https://aistudio.google.com/rate-limit) before running a batch benchmark. This application does not assume fixed quota numbers and does not make live calls in tests.

## What is sent

For a live investigation, the system sends:

- Incident title, service, endpoint, severity, start time, and a bounded description.
- A short deterministic signal summary and compact timeline.
- Up to 60 selected evidence records, each with a stable ID, timestamp, type, abbreviated source, selected metadata, and a truncated content excerpt, subject to `MAX_PACKET_CHARS`.
- System instructions and the expected JSON schema.

`MAX_PACKET_CHARS` defaults to 18,000 characters. That is a conservative text-size bound, not an exact token counter. The request also caps generated output at 4,096 tokens. Input/context ceilings and free-tier quotas vary by model and account, so the configured model's current model card and AI Studio rate-limit page are authoritative; this project does not hard-code a stale context-window number.

It does not send entire uploaded files without parsing or packet selection. Repeated normalized records are deduplicated. Common key/token/password/bearer-shaped strings are redacted before prompt construction. Redaction is best-effort, not a guarantee; do not send sensitive evidence without approval.

## Prompt protections

The system instruction says to treat evidence as untrusted data, use only supplied information, separate observation from inference, cite stable evidence IDs, list both supporting and contradicting evidence, acknowledge unknowns, and allow `INSUFFICIENT_EVIDENCE`. It explicitly prohibits fabricated commits, metrics, timestamps, evidence IDs, and causal claims based only on chronology.

The result must validate as `InvestigationResult`. Cited IDs that do not belong to the incident are discarded. Invalid JSON, empty output, timeouts, rate limits, and provider errors produce the visible fallback notice and a saved deterministic analysis.

## Caching and accounting

The cache key hashes normalized packet text, `PROMPT_VERSION`, and model name. A change to any of these creates a miss. Cache hits and misses, calls, input/output tokens, failures, and fallback use are stored in SQLite. Provider token usage is preferred; if metadata is omitted, the application records a character-based estimate (roughly characters divided by four) and labels it as an estimate in Settings.

An uncached interactive investigation starts with one Gemini request and retries up to two times only for transient provider failures; malformed output is not retried. The opt-in 40-case benchmark disables retries to cap its request count at one per case (fewer if cache entries repeat).

## Configure

Set `GEMINI_API_KEY` and `GEMINI_MODEL` in the local `.env`. Never commit `.env`, paste a key into incident evidence, or share screenshots containing secrets. A missing key is expected and supported: deterministic analysis continues without contacting Google.

## Verification status

Live Gemini API verification was not run for this build because no `GEMINI_API_KEY` was available. The installed SDK's structured-output configuration, Pydantic schema, mocked cache path, malformed-response handling, and no-key fallback were tested locally. A live request requires the user's own Gemini API key and an enabled model in their Google AI Studio project.

Official references:

- [Google GenAI SDK libraries](https://ai.google.dev/gemini-api/docs/libraries)
- [Gemini models](https://ai.google.dev/gemini-api/docs/models)
- [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Pricing and free tier](https://ai.google.dev/gemini-api/docs/pricing)
