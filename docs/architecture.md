# Architecture and data flow

## Design goals

The project keeps all orchestration inside one understandable Python process. Streamlit calls application services directly; SQLite persists incident state; deterministic code does all parsing and preprocessing. Gemini is an optional, bounded semantic layer rather than the only way to get a result.

## Main pipeline

1. **Incident intake** validates severity/status and stores a UTC start time.
2. **Evidence ingestion** parses supported text, log, JSON, and CSV records. It redacts common credential-shaped strings, normalizes timestamps, records source metadata, and assigns stable database IDs such as `EVID-0001`. Duplicate rows are skipped by a SHA-256 content fingerprint.
3. **Deterministic processing** summarizes error-like log lines, creates a sorted timeline, calculates simple per-metric outliers, and ranks cause candidates using lexical matches, proximity to incident start, service matches, evidence-source diversity, prior-incident overlap, metric anomalies, and contradictory cues.
4. **Candidate selection and compression** begins with evidence cited by baseline candidates, then adds bounded contextual records. Repeated normalized content is removed, text fields are truncated, and the packet has a configurable character budget.
5. **Optional Gemini reasoning** sends one compact packet per investigation. The prompt treats packet text as untrusted data, requires structured JSON, insists on valid evidence IDs, asks for contradictions/unknowns, and prohibits treating time correlation as causal proof.
6. **Validation** parses the structured response with Pydantic and filters all cited IDs against the evidence available for that incident. An invalid/empty/provider-failed response falls back to the deterministic result.
7. **Human review** persists a decision, note, and timestamp. The application never changes an AI hypothesis to confirmed automatically.

## Storage

`backend/database.py` uses Python's built-in SQLite driver, foreign keys, a write timeout, and short-lived connections. Startup creates the schema. Tables store incidents, evidence, investigations, human reviews, response-cache entries, and usage events. The configured database URL must be local `sqlite:///...`.

The schema uses stable human-readable evidence IDs with a transactional sequence. An evidence fingerprint is unique per incident, so repeated file imports do not create extra rows. Review decisions are unique per investigation/hypothesis and are updated in place.

## Components

- `backend/schemas.py`: enums and Pydantic input/output validation.
- `backend/ingestion.py`: log and structured file parsing, log summaries, metric outliers, and safe public GitHub REST requests.
- `backend/timeline.py`: timestamp normalization and chronological event ordering.
- `backend/baseline.py`: deterministic root-cause categories, signal scoring, evidence links, and heuristic confidence.
- `backend/prompts.py`: evidence selection, compact packet construction, prompt instructions, prompt version, and cache key.
- `backend/gemini.py`: official Google GenAI Python SDK call, Pydantic structured output, token metadata, bounded retry, and safe provider errors.
- `backend/investigation.py`: baseline-first orchestration, SQLite cache, fallback handling, metrics, and persistence.
- `backend/evaluation.py`: measured synthetic metrics and evidence-slice ablations.
- `app.py`: eight-page Streamlit investigation workspace.

## Confidence and claim boundaries

The deterministic cause score uses weighted lexical-term coverage (0.27), distinct evidence-type corroboration (0.17), event proximity (0.15; linear decay to zero at three hours), service matches (0.10), supporting-item count (0.10), lexical match to prior incidents (0.08), and type-specific metric outliers (0.13). Contradictory items subtract up to 0.18; the final score is bounded. Commit relevance is based on matched text/metadata and deployments contribute when their content matches a cause and their event time is near the incident. Prior-incident matching is lexical, not embedding-based. These transparent rules are ranking signals, not probabilities.

The displayed application confidence is separately calculated as `0.40 × model confidence + 0.15 × supporting-count signal + 0.15 × source-diversity signal + 0.15 × temporal signal + 0.15 × service-match share − contradiction penalty` and clipped to 1–99%. The contradiction penalty is up to 0.25. It is a **heuristic ranking signal, not a calibrated probability**. A human must inspect source content and may reject a hypothesis.

Evidence links are validated for existence, not semantic truth. An existing evidence ID can still be a weak or misinterpreted citation. The UI displays full original content so a reviewer can check it.

## External services and privacy

The normal demo and test suite are offline. Gemini receives one compact packet only after the operator starts an AI investigation and configures a key. Public GitHub import is user-triggered. No evidence is sent to GitHub. No external vector database, embeddings service, or paid API is required.
