# Technical interview preparation

These answers describe the implementation in this repository; they are not claims of production-grade incident automation.

1. **Why Gemini?**  
   The project requires a Gemini-based semantic pass. The app uses Google's official `google-genai` SDK, makes the model configurable, and keeps the core deterministic path independent of credentials.

2. **Why Streamlit?**  
   The primary goal is an understandable Python-only investigation workspace. Streamlit keeps the UI and service calls in one process without introducing a separate web stack or REST API.

3. **Why deterministic preprocessing before the LLM?**  
   Parsing, timestamp normalization, duplicate removal, error summaries, service matching, and candidate ranking are reproducible and testable. This both bounds the packet and preserves a usable no-key fallback.

4. **Why not send raw log files directly to Gemini?**  
   Raw files can be large, repetitive, sensitive, and noisy. The app parses them locally, redacts common credential-like strings, selects candidates, truncates content, and caps the evidence packet.

5. **How are hallucinations reduced?**  
   The prompt restricts claims to supplied evidence and stable IDs. Pydantic rejects malformed shapes; citation IDs are checked against incident evidence; unknowns and contradictory evidence are requested and displayed. These controls reduce risk but do not prove semantic correctness.

6. **What does evidence grounding mean here?**  
   Each evidence item has a persisted ID such as `EVID-0001`. Hypotheses cite supporting and contradictory IDs, and the UI expands the source content for human inspection.

7. **How are contradictions handled?**  
   The baseline marks matched items with explicit counter-signal cues. Gemini receives the evidence packet and is instructed to return counter-evidence IDs. Both classes are shown side by side rather than suppressing contrary evidence.

8. **How is confidence calculated?**  
   It is a heuristic ranking signal blending model confidence, the number of supporting items, source diversity, temporal/service context, and a contradiction penalty. It is explicitly not a calibrated probability.

9. **Why cache model responses?**  
   The key includes normalized evidence packet, prompt version, and model. Matching repeated investigations can avoid a second API call and conserve quota; cache hits/misses are visible.

10. **How are tokens optimized?**  
    Deduplicate, aggregate log patterns, select baseline candidates, include a compact timeline, truncate each evidence excerpt, cap packet characters, and reuse responses from the cache.

11. **How is the benchmark measured?**  
    Forty labeled synthetic cases across ten categories measure root-cause recall, evidence precision, unsupported-citation proxy, contradiction awareness, processing time, and human-review rate. Each limitation is documented.

12. **What is the baseline?**  
    A rule-driven scorer using lexical cause cues, temporal proximity to incident start, service matches, evidence-source diversity, support count, and contradiction cues. It is transparent and does not need a model or key.

13. **What does the evaluation not prove?**  
    Synthetic cases are small and share vocabulary with the baseline. They do not estimate production accuracy, entailment, engineer time saved, or cost savings.

14. **What happens when Gemini fails?**  
    A missing key, quota error, timeout, unavailable model, empty result, or invalid JSON is logged without exposing credentials. The UI shows a clear notice and persists the deterministic result.

15. **Why require human review?**  
    Incident remediation has operational impact. The app records `CONFIRM`, `REJECT`, or `UNRESOLVED`, notes, and timestamp, but never confirms automatically or executes a remediation.

16. **Why synthetic examples?**  
    They make the demo and offline evaluation repeatable without exposing real incident data. They are labeled synthetic and must not be mistaken for observed production behavior.

17. **What are the security boundaries?**  
    Inputs are bounded and parsed as data, not code. API keys remain in environment configuration, common secret patterns are redacted, uploads have a size limit, GitHub repository paths are validated, and no shell commands from the model are executed.

18. **Why SQLite?**  
    It satisfies the local MVP requirement with no separate service, is sufficient for one local operator, and makes setup straightforward. It is not a multi-user, high-concurrency, hosted system-of-record choice.

19. **How would the project scale?**  
    First add stronger persistence and access control, explicit tenant separation, durable background processing, observability, and robust provider quota management. Those would be architecture changes, not hidden dependencies in this demo.

20. **What would improve the baseline next?**  
    Add domain-specific parsers and semantic retrieval only after collecting a consented, representative, labeled incident corpus; calibrate confidence on held-out data; and measure semantic citation entailment with human adjudication.
