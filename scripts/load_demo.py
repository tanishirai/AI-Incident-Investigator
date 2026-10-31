from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings  # noqa: E402
from backend.database import Database  # noqa: E402
from backend.schemas import EvidenceInput, EvidenceType, IncidentInput  # noqa: E402


def load_demo() -> str:
    db = Database(settings.database_path)
    start = datetime.now(timezone.utc).replace(microsecond=0)
    incident_id = f"INC-DEMO-{uuid4().hex[:6].upper()}"
    incident = IncidentInput(
        title="Checkout latency spike after query plan release",
        description=(
            "DEMO DATA — elevated checkout latency began shortly after a query change. "
            "The evidence includes supporting signals, a counter-signal, and unrelated distractors."
        ),
        severity="HIGH",
        service="checkout-api",
        endpoint="/checkout",
        started_at=start,
        status="OPEN",
    )
    db.create_incident(incident, incident_id)
    records = [
        ("LOG", "demo:application.log", "ERROR database query timeout on /checkout; query duration 2.8s", {"level": "ERROR", "service": "checkout-api", "endpoint": "/checkout", "status_code": 504, "error_type": "QueryTimeout"}, 2),
        ("COMMIT", "github:acme/shop", "Commit a81c04f2: optimize checkout query join and change index selection", {"sha": "a81c04f2", "author": "Demo Engineer", "changed_files": ["services/checkout/queries.py"]}, -5),
        ("DEPLOYMENT", "deployments:production", "Deployment v4.2.1 completed for checkout-api; changed services/checkout/queries.py", {"version": "v4.2.1", "environment": "production", "status": "complete", "service": "checkout-api"}, -3),
        ("METRIC", "metrics:checkout-api", "metric_name=latency_p95_ms value=2430 service=checkout-api; baseline was 210ms", {"metric_name": "latency_p95_ms", "value": 2430, "service": "checkout-api"}, 3),
        ("METRIC", "metrics:checkout-api", "metric_name=db_cpu_percent value=18 service=checkout-api; database CPU is healthy and unchanged", {"metric_name": "db_cpu_percent", "value": 18, "service": "checkout-api"}, 4),
        ("ISSUE", "github:acme/shop", "Issue #142: checkout latency after query plan optimization", {"issue_id": 142, "status": "open", "labels": ["incident"]}, 6),
        ("PREVIOUS_INCIDENT", "incident-history.csv", "Previous incident INC-2024-019: QueryTimeout after missing checkout index; restored index and compared plan", {"incident_id": "INC-2024-019", "error_type": "QueryTimeout", "root_cause": "Missing query index"}, -200),
        ("LOG", "demo:application.log", "INFO product image cache hit rate normal", {"level": "INFO", "service": "media-api"}, 5),
        ("ISSUE", "github:acme/shop", "Issue #139: email preview typography needs adjustment", {"issue_id": 139, "status": "open", "labels": ["design"]}, -12),
    ]
    for kind, source, content, metadata, offset in records:
        db.add_evidence(
            EvidenceInput(
                incident_id=incident_id,
                type=EvidenceType(kind),
                source=source,
                timestamp=start.replace(microsecond=0) + __import__("datetime").timedelta(minutes=offset),
                content=content,
                metadata={"synthetic_demo": True, **metadata},
            )
        )
    print(f"Loaded DEMO DATA incident {incident_id} with {len(records)} evidence items.")
    return incident_id


if __name__ == "__main__":
    load_demo()
