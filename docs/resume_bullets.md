# Resume bullet options

These are based on the actual local run in `evaluation/results.json`, generated with the deterministic baseline on 40 synthetic cases. Do not describe the synthetic scores as production accuracy.

1. Built an evidence-grounded incident investigation baseline and measured **100% root-cause recall**, **90% evidence precision**, and **80% contradiction awareness** across 40 synthetic incidents spanning 10 root-cause categories.
2. Evaluated four evidence ablations on the same 40-case synthetic set: the time-only negative control reached **0% recall**, while log-only, log/commit/deployment, and full-evidence slices each measured **100% recall**.
3. Verified evidence-ID filtering, contradiction handling, SQLite response-cache reuse, and no-key deterministic fallback with **23 passing offline tests**; Gemini was mocked and no live model request was made.

Metric definitions, ablation results, and limitations are in `evaluation/report.md` and `docs/evaluation.md`.
