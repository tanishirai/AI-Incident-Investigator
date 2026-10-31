# Measured evaluation report

- Dataset: **synthetic**, 40 synthetic cases.
- Root-cause recall: 100.0%
- Evidence precision: 90.0%
- Unsupported-claim rate: 3.3%
- Contradiction awareness: 80.0%
- Mean processing time: 0.145 ms/case on this run's machine.
- Human-review rate: 100.0%

## Evidence ablations

| Slice | Recall | Evidence precision | Unsupported rate | Contradiction awareness |
|---|---:|---:|---:|---:|
| Timeline Only | 0.0% | 0.0% | 0.0% | 0.0% |
| Timeline Logs | 100.0% | 90.0% | 3.3% | 90.0% |
| Logs Commits Deployments | 100.0% | 85.0% | 3.3% | 90.0% |
| All Evidence | 100.0% | 90.0% | 3.3% | 80.0% |

## Gemini: skipped

GEMINI_API_KEY is not configured.

Synthetic evaluation only; these results are not production performance claims.
Synthetic benchmark metrics are not production accuracy or engineer time saved.
