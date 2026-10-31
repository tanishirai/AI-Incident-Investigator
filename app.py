from __future__ import annotations

import json
import html
import logging
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.database import Database  # noqa: E402
from backend.ingestion import (  # noqa: E402
    IngestionError,
    github_repo_evidence,
    metric_anomalies,
    parse_log_upload,
    parse_structured_upload,
)
from backend.investigation import run_investigation  # noqa: E402
from backend.schemas import (  # noqa: E402
    EvidenceInput,
    EvidenceType,
    IncidentInput,
    IncidentStatus,
    ReviewInput,
    Severity,
)
from backend.timeline import build_timeline  # noqa: E402
from backend.evaluation import run_baseline_evaluation  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[logging.FileHandler(ROOT / "application.log", encoding="utf-8"), logging.StreamHandler()],
)
logger = logging.getLogger("incident_investigator")

st.set_page_config(
    page_title="AI Incident Investigator",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Manrope:wght@400;500;600;700;800&display=swap');
      :root { --mint: #67E8C4; --muted: #9BAEB8; --line: rgba(170,201,212,.14); }
      html, body, [class*="css"] { font-family: 'Manrope', sans-serif; }
      .stApp { background: radial-gradient(ellipse at 85% 0%, #162b30 0, #0b1116 47%); }
      [data-testid="stSidebar"] { background: #0a1015; border-right: 1px solid var(--line); }
      .brand { font-size: 1.25rem; font-weight: 800; letter-spacing: -.04em; margin: .25rem 0 0; }
      .brand-mark { color: var(--mint); font-family: 'DM Mono', monospace; margin-right: .45rem; }
      .eyebrow { color: var(--mint); font: 500 .72rem 'DM Mono', monospace; letter-spacing: .14em; text-transform: uppercase; }
      .muted { color: var(--muted); }
      .panel { padding: 1rem 1.1rem; border: 1px solid var(--line); border-radius: 14px; background: rgba(17,27,34,.78); }
      .hypothesis { border: 1px solid var(--line); background: linear-gradient(135deg, rgba(20,35,42,.95), rgba(13,20,25,.92)); border-radius: 15px; padding: 1.15rem; margin: .75rem 0 1rem; }
      .tag { display: inline-block; padding: .25rem .55rem; border: 1px solid var(--line); border-radius: 99px; color: #c4d3d9; font: .72rem 'DM Mono', monospace; }
      .mono { font-family: 'DM Mono', monospace; }
      div[data-testid="stMetric"] { border: 1px solid var(--line); padding: .8rem 1rem; border-radius: 12px; background: rgba(17,27,34,.62); }
      div[data-testid="stMetricLabel"] { color: var(--muted); }
      div[data-testid="stMetricValue"] { font-weight: 700; }
      .stButton > button[kind="primary"] { background: var(--mint); color: #07140f; border-color: var(--mint); font-weight: 800; }
      .stButton > button { border-radius: 9px; }
      hr { border-color: var(--line); }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_db() -> Database:
    return Database(settings.database_path)


db: Database | None = None
NAV_ITEMS = [
    "Dashboard",
    "Incidents",
    "Evidence Explorer",
    "Timeline",
    "AI Investigation",
    "Human Review",
    "Evaluation",
    "Settings",
]


def heading(kicker: str, title: str, subhead: str = "") -> None:
    st.markdown(f'<div class="eyebrow">{kicker}</div>', unsafe_allow_html=True)
    st.title(title)
    if subhead:
        st.caption(subhead)


def get_selected_incident(incidents: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not incidents:
        return None
    labels = {
        f"{item['title']} · {item['id']}": item
        for item in incidents
    }
    chosen = st.sidebar.selectbox("Active incident", list(labels.keys()), key="active_incident")
    return labels[chosen]


def evidence_counts(evidence: list[dict[str, Any]]) -> dict[str, int]:
    return {kind.value: sum(1 for item in evidence if item["type"] == kind.value) for kind in EvidenceType}


def render_evidence_item(item: dict[str, Any]) -> None:
    stamp = item.get("timestamp") or "time unknown"
    with st.expander(f"{item['id']} · {item['type']} · {stamp} · {item['source']}"):
        st.code(item["content"], language="text")
        metadata = item.get("metadata", {})
        if metadata:
            st.json(metadata, expanded=False)


def render_hypotheses(
    incident: dict[str, Any],
    investigation: dict[str, Any],
    result: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> None:
    st.markdown(f'<div class="eyebrow">HUMAN REVIEW REQUIRED · {result.get("method", "deterministic").upper()}</div>', unsafe_allow_html=True)
    st.subheader(incident["title"])
    st.write(result.get("investigation_summary", ""))
    if result.get("error_notice"):
        st.warning(
            f"{result['error_notice']} "
            "The deterministic investigator is shown below."
        )
    if result.get("evidence_status") == "INSUFFICIENT_EVIDENCE":
        st.info("INSUFFICIENT EVIDENCE — the system will not claim a root cause from this packet.")
    by_id = {item["id"]: item for item in evidence}
    for index, hypothesis in enumerate(result.get("hypotheses", []), start=1):
        heuristic = hypothesis.get("heuristic_confidence")
        confidence = heuristic if heuristic is not None else hypothesis.get("confidence", 0)
        with st.container(border=True):
            left, right = st.columns([4, 1])
            with left:
                st.markdown(f'<div class="eyebrow">HYPOTHESIS {index}</div>', unsafe_allow_html=True)
                st.subheader(hypothesis["title"])
                st.write(hypothesis.get("description", ""))
            with right:
                st.metric("Confidence signal", f"{confidence:.0%}")
                st.caption("Heuristic ranking signal, not a calibrated probability.")
            st.markdown("**Reasoning**")
            st.write(hypothesis.get("reasoning") or "No reasoning text supplied.")
            support_col, contra_col = st.columns(2)
            with support_col:
                st.markdown("**SUPPORTING EVIDENCE**")
                ids = hypothesis.get("evidence_ids", [])
                if not ids:
                    st.caption("No linked supporting evidence.")
                for evidence_id in ids:
                    if evidence_id in by_id:
                        render_evidence_item(by_id[evidence_id])
            with contra_col:
                st.markdown("**CONTRADICTING EVIDENCE**")
                ids = hypothesis.get("contradicting_evidence_ids", [])
                if not ids:
                    st.caption("No contradictory evidence was attributed. This is not proof none exists.")
                for evidence_id in ids:
                    if evidence_id in by_id:
                        render_evidence_item(by_id[evidence_id])
            st.markdown("**CONTEXT**")
            st.caption(f"Service: {incident.get('service', 'unknown')} · Severity if true: {hypothesis.get('severity_if_true', 'MEDIUM')}")
            st.markdown("**Suggested diagnostic**")
            st.write(hypothesis.get("suggested_diagnostic", "Verify the hypothesis with service telemetry and an owner."))
            if hypothesis.get("hypothesis_id"):
                st.caption(f"Hypothesis reference: {hypothesis['hypothesis_id']}")
    if not result.get("hypotheses"):
        st.info("No root-cause hypothesis cleared the evidence threshold.")
    if result.get("unknowns"):
        with st.expander("What remains unknown"):
            for unknown in result["unknowns"]:
                st.markdown(f"- {unknown}")
    if result.get("recommended_next_steps"):
        with st.expander("Recommended diagnostics and next steps", expanded=True):
            for step in result["recommended_next_steps"]:
                st.markdown(f"- {step}")
    usage = result.get("usage", {})
    if usage:
        st.caption(
            f"Token usage · input {usage.get('input_tokens', 0):,} · "
            f"output {usage.get('output_tokens', 0):,} · model {result.get('model') or 'cached'}"
        )


def page_dashboard() -> None:
    heading("OPERATIONS OVERVIEW", "Incident intelligence", "A traceable view of active incidents, evidence, and investigation activity.")
    stats = db.dashboard_stats()
    usage = stats["usage"]
    methods = stats["methods"]
    reviews = stats["reviews"]
    total_cache = usage["hits"] + usage["misses"]
    cache_rate = usage["hits"] / total_cache if total_cache else 0
    ai_count = methods.get("gemini", 0) + methods.get("gemini_cached", 0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total incidents", stats["incidents"])
    c2.metric("Investigations", sum(methods.values()))
    c3.metric("AI investigations", ai_count, help="Only live or cached Gemini investigations.")
    c4.metric("Deterministic", methods.get("deterministic", 0))
    c5, c6, c7, c8 = st.columns(4)
    c5.metric("Confirmed hypotheses", reviews.get("CONFIRM", 0))
    c6.metric("Rejected hypotheses", reviews.get("REJECT", 0))
    c7.metric("Unresolved reviews", reviews.get("UNRESOLVED", 0))
    c8.metric("Average analysis time", f"{stats['average_seconds']:.2f}s")
    st.divider()
    c9, c10, c11 = st.columns(3)
    c9.metric("Gemini calls", usage["calls"])
    c10.metric("Cache hit rate", f"{cache_rate:.0%}")
    c11.metric("Fallbacks", usage["fallbacks"])
    recent = db.list_investigations()
    st.subheader("Recent investigations")
    if recent:
        frame = pd.DataFrame(
            [
                {
                    "Incident": row["incident_title"],
                    "Method": row["method"],
                    "Model": row["model"] or "—",
                    "Duration": f"{row['duration_seconds']:.2f}s",
                    "Created": row["created_at"][:19].replace("T", " "),
                }
                for row in recent
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
    else:
        st.info("No investigations yet. Add demo data or create an incident to get started.")
    st.caption("Synthetic examples are labeled DEMO DATA. Metrics shown above are read from this local SQLite database.")


def page_incidents() -> None:
    heading("CASE MANAGEMENT", "Incidents", "Create incident records and attach logs, deployments, commits, issues, metrics, and prior cases.")
    with st.expander("Create incident", expanded=not bool(db.list_incidents())):
        with st.form("create_incident"):
            title = st.text_input("Incident title", placeholder="e.g. Checkout latency spike")
            description = st.text_area("Description", max_chars=5000)
            left, right = st.columns(2)
            with left:
                severity = st.selectbox("Severity", [v.value for v in Severity], index=2)
                service = st.text_input("Service", placeholder="checkout-api")
                endpoint = st.text_input("Endpoint", placeholder="/checkout")
            with right:
                started_date = st.date_input("Started date", value=date.today())
                started_time = st.time_input("Started time", value=time(14, 0))
                status = st.selectbox("Status", [v.value for v in IncidentStatus])
            submitted = st.form_submit_button("Create incident", type="primary")
        if submitted:
            try:
                started = datetime.combine(started_date, started_time).replace(tzinfo=timezone.utc)
                payload = IncidentInput(
                    title=title,
                    description=description,
                    severity=severity,
                    service=service or "unknown",
                    endpoint=endpoint,
                    started_at=started,
                    status=status,
                )
                incident_id = f"INC-{datetime.now(timezone.utc).strftime('%y%m%d')}-{__import__('uuid').uuid4().hex[:6].upper()}"
                db.create_incident(payload, incident_id)
                logger.info("Incident created id=%s", incident_id)
                st.success(f"Incident {incident_id} created.")
                st.rerun()
            except Exception as exc:
                st.error(f"Could not create incident: {exc}")

    incident = get_selected_incident(db.list_incidents())
    if not incident:
        st.info("No incident records found. Load the labeled demo incident with `python scripts/load_demo.py`.")
        return
    evidence = db.list_evidence(incident["id"])
    st.subheader(incident["title"])
    st.caption(f"{incident['id']} · {incident['status']} · {incident['severity']} · {incident['service']} · started {incident['started_at']}")
    st.write(incident["description"])
    counts = evidence_counts(evidence)
    cols = st.columns(6)
    for col, (label, kind) in zip(
        cols,
        [
            ("Logs", "LOG"),
            ("Deployments", "DEPLOYMENT"),
            ("Commits", "COMMIT"),
            ("Issues", "ISSUE"),
            ("Metrics", "METRIC"),
            ("Prior cases", "PREVIOUS_INCIDENT"),
        ],
    ):
        col.metric(label, counts[kind])
    if any(item.get("metadata", {}).get("synthetic_demo") for item in evidence):
        st.warning("DEMO DATA — generated synthetic evidence for demonstration.")
    st.divider()
    st.markdown("### Add evidence")
    kind = st.selectbox(
        "Evidence type",
        [item.value for item in EvidenceType],
        format_func=lambda value: value.replace("_", " ").title(),
        key="evidence_type",
    )
    source = st.text_input("Evidence source", value="manual upload")
    uploaded = st.file_uploader(
        "Choose .txt, .log, .json, or .csv",
        type=["txt", "log", "json", "csv"],
        key=f"evidence-upload-{kind}",
    )
    if uploaded is not None:
        st.caption(f"{uploaded.name} · {uploaded.size:,} bytes · limit {settings.max_upload_mb} MB")
        if st.button("Ingest evidence file", type="primary"):
            try:
                raw = uploaded.getvalue()
                if kind == EvidenceType.LOG.value:
                    items, summary, warnings = parse_log_upload(
                        uploaded.name,
                        raw,
                        incident["id"],
                        settings.max_upload_mb * 1024 * 1024,
                    )
                    st.session_state["last_log_summary"] = summary
                else:
                    items, warnings = parse_structured_upload(
                        uploaded.name,
                        raw,
                        incident["id"],
                        EvidenceType(kind),
                        settings.max_upload_mb * 1024 * 1024,
                    )
                created = 0
                for item in items:
                    item.source = source or item.source
                    _, was_created = db.add_evidence(item)
                    created += int(was_created)
                logger.info("Evidence ingestion incident=%s count=%d", incident["id"], created)
                st.success(f"Added {created} unique evidence item(s). Duplicate rows were skipped.")
                for warning in warnings:
                    st.warning(warning)
                if kind == EvidenceType.LOG.value:
                    st.json(summary)
                st.rerun()
            except IngestionError as exc:
                st.error(str(exc))
            except Exception as exc:
                logger.exception("Evidence ingestion failed")
                st.error(f"Evidence could not be imported: {type(exc).__name__}. Check the file format.")
    with st.expander("Import from a public GitHub repository"):
        repo = st.text_input("Repository (owner/name)", placeholder="example/service")
        st.caption("Imports up to 10 public commits (including changed files and line counts) and 10 issues. Uses up to 12 GitHub requests; an optional GITHUB_TOKEN provides higher limits.")
        if st.button("Fetch GitHub evidence"):
            try:
                items = github_repo_evidence(
                    incident["id"],
                    repo,
                    settings.github_token,
                    settings.max_evidence_items,
                )
                created = sum(db.add_evidence(item)[1] for item in items)
                st.success(f"Imported {created} unique GitHub evidence item(s).")
                st.rerun()
            except IngestionError as exc:
                st.error(str(exc))
    if "last_log_summary" in st.session_state:
        with st.expander("Latest deterministic log summary"):
            st.json(st.session_state["last_log_summary"])


def page_evidence() -> None:
    heading("EVIDENCE INDEX", "Evidence explorer", "Filter, inspect, and verify the original source material behind every hypothesis.")
    incident = get_selected_incident(db.list_incidents())
    if not incident:
        st.info("Create an incident or load the demo data first.")
        return
    evidence = db.list_evidence(incident["id"])
    if not evidence:
        st.info("No evidence has been attached to this incident.")
        return
    a, b = st.columns(2)
    kind_options = ["All types"] + sorted({item["type"] for item in evidence})
    source_options = ["All sources"] + sorted({item["source"] for item in evidence})
    selected_kind = a.selectbox("Type", kind_options)
    selected_source = b.selectbox("Source", source_options)
    services = sorted(
        {
            str(item.get("metadata", {}).get("service"))
            for item in evidence
            if item.get("metadata", {}).get("service")
        }
    )
    service_choices = ["All services"] + services
    selected_service = st.selectbox("Service", service_choices)
    filtered = [
        item
        for item in evidence
        if (selected_kind == "All types" or item["type"] == selected_kind)
        and (selected_source == "All sources" or item["source"] == selected_source)
        and (
            selected_service == "All services"
            or str(item.get("metadata", {}).get("service", "")) == selected_service
        )
    ]
    stamps = [item["timestamp"][:10] for item in filtered if item.get("timestamp")]
    if stamps:
        date_range = st.date_input(
            "Timestamp range",
            value=(date.fromisoformat(min(stamps)), date.fromisoformat(max(stamps))),
            key="evidence_date_range",
        )
        if isinstance(date_range, tuple) and len(date_range) == 2:
            lower, upper = date_range
            filtered = [
                item for item in filtered
                if not item.get("timestamp")
                or lower.isoformat() <= item["timestamp"][:10] <= upper.isoformat()
            ]
    st.caption(f"{len(filtered)} of {len(evidence)} evidence items")
    anomalies = metric_anomalies(evidence)
    if anomalies:
        with st.expander(f"Deterministic metric anomalies · {len(anomalies)}", expanded=True):
            st.caption("Population z-score ≥ 2 with at least three samples per metric. A signal to inspect, not a diagnosis.")
            st.dataframe(pd.DataFrame(anomalies), use_container_width=True, hide_index=True)
    else:
        st.caption("No metric outlier met the simple sample-size and z-score threshold.")
    if filtered:
        frame = pd.DataFrame(
            [
                {
                    "Evidence ID": item["id"],
                    "Type": item["type"],
                    "Timestamp": item.get("timestamp") or "unknown",
                    "Source": item["source"],
                    "Content": item["content"][:220],
                }
                for item in filtered
            ]
        )
        st.dataframe(frame, use_container_width=True, hide_index=True)
        st.markdown("#### Original content")
        for item in filtered:
            render_evidence_item(item)


def page_timeline() -> None:
    heading("TEMPORAL CORRELATION", "Incident timeline", "Chronological evidence is a lead, not proof of causation.")
    incident = get_selected_incident(db.list_incidents())
    if not incident:
        st.info("Create an incident or load demo data first.")
        return
    events = build_timeline(incident, db.list_evidence(incident["id"]))
    if not events:
        st.info("No timestamped incident or evidence events are available.")
        return
    all_types = sorted({event["type"] for event in events})
    selected = st.multiselect("Show event types", all_types, default=all_types)
    events = [event for event in events if event["type"] in selected]
    if not events:
        st.info("No timeline events match the selected types.")
        return
    frame = pd.DataFrame(events)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    fig = px.scatter(
        frame,
        x="timestamp",
        y="type",
        color="type",
        hover_name="label",
        hover_data={"evidence_id": True, "source": True, "timestamp": True, "type": False},
        title=f"{incident['title']} · {len(events)} events",
    )
    fig.update_traces(marker={"size": 13, "line": {"width": 1, "color": "#0B1116"}})
    fig.update_layout(
        height=max(380, 78 * min(len(all_types), 8)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#DDE9ED",
        legend_title_text="Evidence type",
        margin={"l": 20, "r": 20, "t": 55, "b": 20},
    )
    st.plotly_chart(fig, use_container_width=True)
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Time (UTC)": event["timestamp"],
                    "Type": event["type"],
                    "Evidence ID": event["evidence_id"] or "—",
                    "Event": event["label"],
                    "Source": event["source"],
                }
                for event in events
            ]
        ),
        use_container_width=True,
        hide_index=True,
    )


def page_investigation() -> None:
    heading("EVIDENCE-GROUNDED ANALYSIS", "AI investigation", "Rank hypotheses, audit citations, and inspect the evidence that supports or weakens each explanation.")
    incident = get_selected_incident(db.list_incidents())
    if not incident:
        st.info("Create an incident or load demo data first.")
        return
    evidence = db.list_evidence(incident["id"])
    status_line = f"{incident['severity']} · {incident['service']} · {incident['endpoint'] or 'endpoint not set'} · {len(evidence)} evidence items"
    safe_title = html.escape(str(incident["title"]))
    safe_status = html.escape(status_line)
    st.markdown(f'<div class="panel"><span class="eyebrow">INCIDENT WORKSPACE</span><br/><strong>{safe_title}</strong><br/><span class="muted">{safe_status}</span></div>', unsafe_allow_html=True)
    if any(item.get("metadata", {}).get("synthetic_demo") for item in evidence):
        st.warning("DEMO DATA — these example results are not a real production incident.")
    if not evidence:
        st.info("Attach evidence before starting an investigation. The deterministic path needs evidence to rank hypotheses.")
    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("Start investigation", type="primary", disabled=not bool(evidence)):
            with st.spinner("Correlating evidence and building a grounded investigation..."):
                investigation_id, result = run_investigation(db, incident, evidence, use_ai=True)
            st.session_state["last_investigation_id"] = investigation_id
            st.success(f"Investigation saved · {result.method}")
            st.rerun()
    with c2:
        st.caption(
            "Gemini is used only when GEMINI_API_KEY is configured. Otherwise, the local deterministic analysis runs without external calls."
        )
    record = db.latest_investigation(incident["id"])
    if not record:
        st.info("Start an investigation to generate the first evidence-grounded analysis.")
        return
    st.caption(
        f"Investigation {record['id']} · {record['method']} · {record['created_at'][:19].replace('T', ' ')} UTC · "
        f"{record['duration_seconds']:.2f}s"
    )
    render_hypotheses(incident, record, record["result"], evidence)


def page_review() -> None:
    heading("HUMAN-IN-THE-LOOP", "Review decisions", "Only a human can confirm or reject a cause. AI output is never automatically treated as fact.")
    incident = get_selected_incident(db.list_incidents())
    if not incident:
        st.info("Create an incident and run an investigation first.")
        return
    record = db.latest_investigation(incident["id"])
    if not record:
        st.info("No saved investigation exists for this incident.")
        return
    hypotheses = record["result"].get("hypotheses", [])
    if not hypotheses:
        st.info("No hypotheses are available to review. Check the listed unknowns and add relevant evidence.")
        return
    st.caption(f"Reviewing investigation {record['id']} · {record['method']}")
    for index, hypothesis in enumerate(hypotheses, start=1):
        hypothesis_id = hypothesis.get("hypothesis_id") or f"HYP-{index}"
        current = db.review_for(record["id"], hypothesis_id)
        with st.container(border=True):
            st.markdown(f"#### {index}. {hypothesis['title']}")
            st.write(hypothesis.get("description", ""))
            with st.form(f"review-{record['id']}-{hypothesis_id}"):
                default = current["decision"] if current else "UNRESOLVED"
                decision = st.radio(
                    "Human decision",
                    ["UNRESOLVED", "CONFIRM", "REJECT"],
                    index=["UNRESOLVED", "CONFIRM", "REJECT"].index(default),
                    horizontal=True,
                    key=f"review-decision-{record['id']}-{hypothesis_id}",
                )
                notes = st.text_area(
                    "Reviewer notes",
                    value=current["notes"] if current else "",
                    max_chars=4000,
                    key=f"review-notes-{record['id']}-{hypothesis_id}",
                )
                if st.form_submit_button("Save review"):
                    db.save_review(
                        ReviewInput(
                            investigation_id=record["id"],
                            hypothesis_id=hypothesis_id,
                            decision=decision,
                            notes=notes,
                        )
                    )
                    logger.info(
                        "Human review saved investigation=%s hypothesis=%s decision=%s",
                        record["id"],
                        hypothesis_id,
                        decision,
                    )
                    st.success("Review saved.")
                    st.rerun()
            if current:
                st.caption(f"Last saved {current['reviewed_at'][:19].replace('T', ' ')} UTC")


def page_evaluation() -> None:
    heading("MODEL QUALITY", "Evaluation lab", "Benchmark the deterministic baseline on labeled synthetic cases. Live Gemini evaluation is opt-in and uses API quota.")
    dataset_path = ROOT / "evaluation" / "dataset" / "synthetic_incidents.json"
    if not dataset_path.exists():
        st.warning("Evaluation data not found. Run `python evaluation/generate_dataset.py`.")
        return
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))
    st.info(f"SYNTHETIC EVALUATION DATA · {len(cases)} cases · 10 root-cause categories · distractors and contradictory items included.")
    st.caption(
        "Metrics are computed from the checked-in dataset at runtime. Synthetic recall is not a real-world accuracy claim."
    )
    if st.button("Run deterministic benchmark", type="primary"):
        try:
            report = run_baseline_evaluation(dataset_path)
            output = ROOT / "evaluation" / "results.json"
            output.write_text(json.dumps(report, indent=2), encoding="utf-8")
            db.log_usage("evaluation", details=f"{len(cases)} synthetic cases")
            logger.info("Evaluation executed cases=%d", len(cases))
            st.session_state["evaluation_report"] = report
        except Exception as exc:
            logger.exception("Evaluation failed")
            st.error(f"Evaluation could not be completed: {type(exc).__name__}. Check developer logs.")
    report = st.session_state.get("evaluation_report")
    if report:
        baseline = report["baseline"]
        columns = st.columns(4)
        columns[0].metric("Root-cause recall", f"{baseline['root_cause_recall']:.0%}")
        columns[1].metric("Evidence precision", f"{baseline['evidence_precision']:.0%}")
        columns[2].metric("Unsupported-claim rate", f"{baseline['unsupported_claim_rate']:.0%}")
        columns[3].metric("Contradiction awareness", f"{baseline['contradiction_awareness']:.0%}")
        st.caption(
            f"Measured on {baseline['cases']} synthetic cases · mean processing "
            f"{baseline['mean_investigation_seconds'] * 1000:.3f} ms/case · "
            f"human review rate {baseline['human_review_rate']:.0%}."
        )
        st.markdown("#### Ablation results")
        st.dataframe(
            pd.DataFrame(
                [
                    {"Evidence slice": name.replace("_", " ").title(), **metrics}
                    for name, metrics in report["ablation"].items()
                ]
            ),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "Download measured results.json",
            json.dumps(report, indent=2),
            file_name="incident-investigation-evaluation.json",
            mime="application/json",
        )
    with st.expander("Run Gemini on this dataset"):
        st.warning("This can make up to 40 live Gemini requests. Each call is subject to your project's current model quota and may use paid quota if your account is configured for paid use.")
        confirmed = st.checkbox("I understand this will send each synthetic case to Gemini.", key="confirm_live_eval")
        if st.button("Run live Gemini benchmark", disabled=not (confirmed and settings.gemini_api_key)):
            st.info("Use `python run_evaluation.py --with-gemini` to run the quota-consuming benchmark and save its measured report.")
        if not settings.gemini_api_key:
            st.caption("GEMINI_API_KEY is not configured, so live Gemini evaluation is unavailable.")


def page_settings() -> None:
    heading("CONFIGURATION", "Settings", "Local configuration, cache, and provider usage. Secrets are never shown here.")
    st.subheader("Runtime")
    c1, c2 = st.columns(2)
    c1.metric("Gemini key", "Configured" if settings.gemini_api_key else "Not configured")
    c2.metric("Model", settings.gemini_model)
    st.code(
        "DATABASE_URL=sqlite:///incident_investigator.db\n"
        f"MAX_UPLOAD_MB={settings.max_upload_mb}\n"
        f"MAX_EVIDENCE_ITEMS={settings.max_evidence_items}\n"
        f"MAX_PACKET_CHARS={settings.max_packet_chars}",
        language="text",
    )
    st.markdown(
        "Set `GEMINI_API_KEY` and `GEMINI_MODEL` in your local `.env`, then restart the app. "
        "A Gemini API key is not bundled with this project."
    )
    st.caption(f"Prompt version: v1.3 · Database: {settings.database_path.name} · Upload cap: {settings.max_upload_mb} MB/file")
    st.subheader("Provider and cache activity")
    stats = db.dashboard_stats()["usage"]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Gemini calls", stats["calls"])
    c2.metric("Cache hits", stats["hits"])
    c3.metric("Cache misses", stats["misses"])
    c4.metric("Token estimate recorded", f"{stats['input_tokens'] + stats['output_tokens']:,}")
    st.caption("Token counts use provider usage metadata when available, with a character-based estimate only when the provider omits usage fields.")
    if st.button("Clear Gemini response cache"):
        count = db.clear_cache()
        logger.info("Gemini response cache cleared count=%d", count)
        st.success(f"Removed {count} cached response(s). This does not delete incidents or reviews.")
    st.markdown("#### Data handling")
    st.write(
        "Uploads are parsed locally and saved in SQLite. Only the compact selected evidence packet is sent to Gemini, "
        "and only when an API key is configured. Free-tier Gemini terms may allow submitted content to be used to improve Google's products; "
        "avoid sending sensitive production data unless your organization's policy permits it."
    )


def main() -> None:
    global db
    db = get_db()
    st.sidebar.markdown('<div class="brand"><span class="brand-mark">◈</span>Incident Investigator</div>', unsafe_allow_html=True)
    st.sidebar.caption("Evidence-grounded incident analysis")
    st.sidebar.markdown("---")
    page = st.sidebar.radio("Workspace", NAV_ITEMS, label_visibility="collapsed")
    st.sidebar.markdown("---")
    st.sidebar.markdown('<span class="tag">LOCAL SQLITE</span>', unsafe_allow_html=True)
    st.sidebar.caption("AI suggestions require human review.")
    if settings.database_warning:
        st.sidebar.warning(settings.database_warning)
    if page == "Dashboard":
        page_dashboard()
    elif page == "Incidents":
        page_incidents()
    elif page == "Evidence Explorer":
        page_evidence()
    elif page == "Timeline":
        page_timeline()
    elif page == "AI Investigation":
        page_investigation()
    elif page == "Human Review":
        page_review()
    elif page == "Evaluation":
        page_evaluation()
    else:
        page_settings()


if __name__ == "__main__":
    main()
