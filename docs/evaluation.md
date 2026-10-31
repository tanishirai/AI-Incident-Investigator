# Evaluation methodology

## Dataset

`evaluation/dataset/synthetic_incidents.json` contains 40 generated cases: four variants for each of ten root-cause categories. Each record includes incident metadata, ground-truth category, supporting IDs, contradictory IDs, distractor IDs, and all evidence. The generator is deterministic. These cases are synthetic and do not estimate performance on real incidents.

## Metrics

- **Root-cause recall:** share of cases where a generated hypothesis title/description matches the labeled root cause or a labeled term.
- **Evidence precision:** number of cited supporting IDs divided by the number of unique supporting citations across hypotheses, macro-averaged by case. Cites to valid distractors are not relevant support.
- **Unsupported-claim rate:** share of generated hypotheses without any citation that resolves to evidence available in that case. This is a citation-presence proxy, not a semantic entailment judge.
- **Contradiction awareness:** share of known contradictory IDs present in hypothesis counter-evidence links, macro-averaged. A case with no contradictory items is counted as fully covered for this metric.
- **Mean investigation time:** measured local Python function wall time for each baseline case on the machine that runs evaluation.
- **Human-review rate:** share of results that request human review. The current safety design requires review for every result containing hypotheses.

## Baseline and ablations

`python run_evaluation.py` runs the deterministic investigator on all 40 cases and measures these slices:

1. **Timeline only:** incident start time and metadata with no evidence text. It is a deliberately weak lower bound.
2. **Timeline + logs:** only log evidence.
3. **Logs + commits/deployments:** those three evidence categories.
4. **All evidence:** all six evidence categories.

The same metric code is used for each slice. Run it locally to generate `evaluation/results.json`; the checked-in report states what was actually executed in the build environment. It does not include invented Gemini results.

## Gemini comparison

The optional command `python run_evaluation.py --with-gemini` runs the same labeled cases through the live Gemini investigation path if a key is configured. It can send up to 40 requests (fewer if cache keys repeat); batch retries are disabled to cap quota use. It is not used in normal tests. A report marks the Gemini section as skipped/not-run when no key is configured.

## Interpretation

The baseline uses known lexical cues, so a synthetic dataset whose examples contain those cues is an intentionally limited benchmark. High synthetic recall would not establish general incident quality. Evidence precision is not an entailment score, contradiction awareness is citation recall rather than logical reasoning accuracy, and investigation time is not a measure of engineer time saved. Human review remains mandatory.
