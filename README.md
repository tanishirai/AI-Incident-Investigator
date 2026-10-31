# AI Incident Investigator

**An evidence-grounded AI investigation system that correlates heterogeneous incident evidence and generates auditable root-cause hypotheses for human review.**

This Python-first project reduces repetitive incident triage work without presenting model output as fact. It combines deterministic parsing, timeline and baseline analysis with an optional Google Gemini semantic pass. Every AI hypothesis is expected to cite stable evidence IDs, show counter-evidence, name unknowns, and leave the decision to a human.

## What it solves

During an incident, engineers often manually jump among logs, deploy histories, commits, metrics, issue trackers, and old postmortems. The evidence is fragmented and time-consuming to correlate. This workspace normalizes those sources, builds a deterministic event timeline, ranks rule-based candidate causes, and optionally asks Gemini to synthesize a compact evidence packet into structured, attributable hypotheses.

It is **not a debugging chatbot**, does not run commands, and does not make production changes.

## Capabilities

- SQLite records for incidents, evidence, investigations, reviews, cached AI responses, and token/call events.
- Evidence ingestion from `.txt`, `.log`, `.json`, and `.csv`; robust log parsing, deterministic error summaries, row warnings, duplicate suppression, and a 4 MB default upload cap.
- Structured evidence records for logs, deployments, commits, GitHub issues, metrics, and previous incidents.
- Public GitHub repository retrieval for recent commits/issues. A token is optional and is never required for the demo.
- Chronological timeline, metric outlier detection, and rule-based investigator that works without Gemini.
- Gemini JSON/Pydantic output; compact and capped evidence packets; evidence ID validation; contradiction attribution; short retry for transient provider failures; deterministic fallback.
- Response cache keyed by normalized packet, prompt version, and model; provider token tracking with an explicitly labeled character estimate if usage metadata is absent.
- Human `CONFIRM`, `REJECT`, and `UNRESOLVED` decisions with notes and timestamps. The application never auto-confirms a hypothesis.
- Eight Streamlit workspace pages: Dashboard, Incidents, Evidence Explorer, Timeline, AI Investigation, Human Review, Evaluation, and Settings.
- Forty synthetic labeled evaluation cases across ten root-cause categories, with support, contradiction, and distractor evidence.
- Evaluation metrics for root-cause recall, evidence precision, unsupported-claim rate, contradiction awareness, processing time, and human-review rate, plus evidence ablations.

## Quick start — Windows PowerShell

Install Python 3.11 or newer. From the extracted `AI-Incident-Investigator` folder:

```powershell
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python evaluation\generate_dataset.py
python scripts\load_demo.py
streamlit run app.py
```

Or use the included scripts:

```powershell
.\setup.ps1
.\run.ps1
```

If PowerShell blocks activation, run this once in the current terminal:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
```

The app opens at `http://localhost:8501`. Demo records are explicitly labeled **DEMO DATA**.

### Gemini configuration

Local deterministic investigations work without a Gemini key. To enable live Gemini analysis:

1. Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Put it in the local `.env` file:

```text
GEMINI_API_KEY=your-key-here
GEMINI_MODEL=gemini-3.8-flash
```

3. Restart Streamlit.

No key is included in the ZIP, source files, or UI. You can change `GEMINI_MODEL` to a model available to your Google AI Studio project. Limits depend on model and account/project status; view the active quota in Google AI Studio rather than relying on a hard-coded number. Free-tier use is currently available for selected models, but eligibility, model availability, quotas, and terms can change. Google's current free-tier terms state that submitted content may be used to improve products; do not send sensitive production evidence unless permitted by your data policy.

## One-command demo

After installing dependencies and activating the virtual environment:

```powershell
python scripts\load_demo.py
streamlit run app.py
```

The sample contains a synthetic checkout API latency event with related logs, a commit, deployment, metrics, issue, prior incident, a counter-signal, and unrelated distractors.

## Evaluation and tests

Regenerate the deterministic synthetic dataset:

```powershell
python evaluation\generate_dataset.py
```

Run baseline metrics and the evidence ablation:

```powershell
python run_evaluation.py
```

Run the 40-case Gemini benchmark only when you intentionally want to spend requests/quota:

```powershell
python run_evaluation.py --with-gemini
```

Run the automated suite; Gemini calls are mocked:

```powershell
pytest -q
```

The evaluation writes locally to `evaluation/results.json`; it does not claim real-world performance. `mean_investigation_seconds` measures local function execution on the current machine. An explicitly requested live Gemini run evaluates up to 40 cases, with one request per uncached case and retries disabled to cap quota use.

## Architecture

See [docs/architecture.md](docs/architecture.md), [docs/gemini-usage.md](docs/gemini-usage.md), and [docs/evaluation.md](docs/evaluation.md). In brief:

```text
Streamlit UI
  → SQLite incident/evidence store
  → deterministic parsing + deduplication + timeline
  → rule-based candidate ranking
  → compact, bounded evidence packet
  → optional Gemini structured reasoning
  → Pydantic validation + citation filtering
  → visible support, contradictions, unknowns, and diagnostics
  → persisted human review
```

SQLite is initialized automatically at startup. `DATABASE_URL` accepts only local SQLite URLs; no PostgreSQL, vector store, queue, or external database is needed.

## Security and data handling

- Keep `.env` local and do not commit it. `.gitignore` excludes it.
- API credentials are read from environment variables only; the app never prints their values.
- Upload types and file sizes are checked, data is parsed as text/JSON/CSV, and no uploaded content is executed.
- Displayed evidence uses Streamlit's escaping defaults and code/text display.
- API keys, password-like values, and bearer strings in evidence content are redacted before AI packets.
- GitHub fetch accepts only `owner/repository` and requests public repository endpoints with a timeout.
- Gemini does not receive evidence unless a key is configured and the user starts an investigation.
- The system does not generate or execute shell commands and does not perform automatic remediation.

## Deployment

The app is suitable for local use without any deployment. Current Streamlit Community Cloud documentation describes a free GitHub-connected deployment path; see [docs/deployment.md](docs/deployment.md) for setup and important SQLite persistence limits. Deployment is optional and not required for the demo.

## Limitations

- The deterministic baseline is a transparent lexical and temporal ranking heuristic, not a causal inference engine.
- Gemini citations are filtered against real evidence IDs, but attribution correctness still requires human inspection.
- Confidence is an application-level ranking signal, **not a calibrated probability**.
- The built-in anomaly detector is intentionally simple and needs enough numeric samples per metric.
- Public GitHub access is subject to current unauthenticated API limits; private repositories are not supported by this importer.
- The synthetic benchmark is useful for regression testing only; it does not represent production incident distributions.
- Streamlit Community Cloud's local filesystem should not be treated as durable storage for incidents or reviews.

## Future work

- Add corpus-specific parsers and validate evidence attribution with human-adjudicated examples.
- Measure confidence calibration on representative held-out incidents before interpreting scores probabilistically.
- If hosted, add authentication and an approved durable database; the current single-operator SQLite app is intentionally local-first.

## Project tree

```text
AI-Incident-Investigator/
├── app.py
├── backend/                 # SQLite, parsers, baseline, timeline, Gemini, evaluation
├── evaluation/              # 40-case synthetic dataset and benchmark artifacts
├── scripts/                 # demo loader, synthetic incident generator, DB reset
├── tests/                   # offline pytest suite; Gemini is mocked
├── docs/                    # architecture, model, evaluation, deployment, interview prep
├── .env.example
├── requirements.txt
├── setup.ps1 / run.ps1
└── setup.sh / run.sh
```

## Resume bullet options

These use the actual checked-in run in `evaluation/results.json`. Results are from synthetic evaluation only, not production accuracy.

1. Built a deterministic incident-investigation baseline and measured **100% root-cause recall**, **90% evidence precision**, and **80% contradiction awareness** across 40 synthetic cases in 10 categories.
2. Compared four evidence ablations on the same 40-case synthetic set: root-cause recall was **0%** for the time-only negative control and **100%** for the log-only, log/commit/deployment, and full-evidence slices.
3. Verified evidence-ID filtering, contradiction handling, SQLite response-cache reuse, and missing-key fallback with **23 passing offline tests**; Gemini was mocked and no live model call was made.

See [docs/resume_bullets.md](docs/resume_bullets.md) for concise versions and the evaluation caveats.

## License

MIT. See [LICENSE](LICENSE).
