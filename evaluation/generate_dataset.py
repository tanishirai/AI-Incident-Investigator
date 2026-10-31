from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCENARIOS = {
    "database_regression": {
        "support": [
            ("LOG", "checkout-api ERROR Database query timeout on /checkout; query duration increased after index change", "checkout-api query"),
            ("COMMIT", "perf: replace checkout query join; changed_files=services/checkout/sql.py", "commit query change"),
            ("METRIC", "metric_name=latency value=2.8 service=checkout-api; p95 latency is above baseline", "latency"),
        ],
        "contradiction": ("METRIC", "metric_name=db_cpu value=18 service=checkout-api; database CPU is healthy and unchanged", "database CPU healthy"),
        "distractors": [("LOG", "INFO Cache hit rate is normal for cart-summary", "normal cache"),
                        ("ISSUE", "Issue: email preview has a minor layout mismatch", "unrelated issue")],
    },
    "configuration_error": {
        "support": [
            ("DEPLOYMENT", "deployment version=v4.2 changed DATABASE_TIMEOUT from 30s to 3s; service=checkout-api", "timeout config"),
            ("LOG", "ERROR configuration validation: upstream timeout setting is invalid", "config validation"),
            ("COMMIT", "chore: rename environment variable DB_TIMEOUT to DATABASE_TIMEOUT", "environment variable"),
        ],
        "contradiction": ("LOG", "INFO active configuration matches last known-good values; no config drift detected", "configuration matches"),
        "distractors": [("METRIC", "metric_name=cpu value=37 service=checkout-api", "cpu 37"),
                        ("ISSUE", "Issue: add an export option to the admin report", "unrelated issue")],
    },
    "external_api_failure": {
        "support": [
            ("LOG", "ERROR upstream api returned HTTP 503 from payment-provider", "upstream api 503"),
            ("ISSUE", "Issue: payment provider intermittent outage; requests timing out", "provider outage"),
            ("METRIC", "metric_name=latency value=4.1 service=checkout-api; external call latency elevated", "external latency"),
        ],
        "contradiction": ("ISSUE", "Status update: provider reports healthy and no incidents in the region", "provider healthy"),
        "distractors": [("COMMIT", "docs: clarify retry policy examples", "documentation"),
                        ("METRIC", "metric_name=memory value=59 service=checkout-api", "memory")],
    },
    "connection_exhaustion": {
        "support": [
            ("LOG", "ERROR database connection pool timeout; too many connections active", "pool timeout"),
            ("METRIC", "metric_name=db_connections value=148 service=orders-api; above configured pool limit", "db connections"),
            ("LOG", "ERROR connections exhausted while opening transaction for /orders", "connections exhausted"),
        ],
        "contradiction": ("METRIC", "metric_name=db_connections value=12 service=orders-api; connections are healthy and within pool limit", "connections healthy"),
        "distractors": [("DEPLOYMENT", "deployment static asset bundle for docs site succeeded", "assets"),
                        ("ISSUE", "Issue: product image alt text missing", "unrelated issue")],
    },
    "feature_flag_error": {
        "support": [
            ("ISSUE", "Issue: new pricing feature flag enabled for all checkout users unexpectedly", "feature flag enabled"),
            ("LOG", "ERROR feature flag checkout_v2 selected unsupported path for tenant", "flag selected"),
            ("DEPLOYMENT", "deployment rollout=100% enabled checkout_v2 toggle", "rollout enabled"),
        ],
        "contradiction": ("LOG", "INFO feature flag checkout_v2 was disabled for all affected tenants", "flag disabled"),
        "distractors": [("METRIC", "metric_name=cpu value=24 service=checkout-api", "cpu 24"),
                        ("COMMIT", "docs: update flag naming examples", "documentation")],
    },
    "memory_leak": {
        "support": [
            ("METRIC", "metric_name=memory value=93 service=profile-api; RSS grows continuously", "memory usage"),
            ("LOG", "ERROR process restarted after out of memory kill; heap retention observed", "out of memory"),
            ("COMMIT", "feat: add long-lived in-memory response cache without eviction", "memory cache"),
        ],
        "contradiction": ("METRIC", "metric_name=memory value=42 service=profile-api; memory is healthy and stable", "memory stable"),
        "distractors": [("ISSUE", "Issue: improve spacing on profile settings screen", "unrelated issue"),
                        ("METRIC", "metric_name=latency value=0.2 service=profile-api", "latency")],
    },
    "incorrect_deployment": {
        "support": [
            ("DEPLOYMENT", "deployment version=v7.0 status=complete service=inventory-api; release began at incident time", "deployment release"),
            ("LOG", "ERROR unsupported module version loaded after deployment", "version module"),
            ("COMMIT", "fix: dependency manifest now pins a breaking major release", "dependency version"),
        ],
        "contradiction": ("DEPLOYMENT", "previous deployment version=v6.9 healthy; current release artifact checksum matches", "previous healthy"),
        "distractors": [("METRIC", "metric_name=cpu value=31 service=inventory-api", "cpu 31"),
                        ("ISSUE", "Issue: update inventory dashboard label", "unrelated issue")],
    },
    "authentication_failure": {
        "support": [
            ("LOG", "ERROR authentication failed: token expired for requests to /account", "token expired"),
            ("ISSUE", "Issue: OAuth signing key rotation caused 401 responses", "oauth key"),
            ("METRIC", "metric_name=error_rate value=0.42 service=identity-api; 401 and 403 errors increased", "401 403"),
        ],
        "contradiction": ("LOG", "INFO authentication succeeded for all sampled requests; no auth errors", "auth succeeded"),
        "distractors": [("COMMIT", "docs: clarify login page instructions", "documentation"),
                        ("METRIC", "metric_name=memory value=44 service=identity-api", "memory")],
    },
    "network_timeout": {
        "support": [
            ("LOG", "ERROR network timeout connecting to fulfillment-api; connection reset", "network timeout"),
            ("METRIC", "metric_name=latency value=5.0 service=orders-api; service-to-service latency spike", "latency"),
            ("ISSUE", "Issue: DNS resolution intermittently fails between orders and fulfillment", "dns"),
        ],
        "contradiction": ("METRIC", "metric_name=latency value=0.1 service=orders-api; network latency is normal", "latency normal"),
        "distractors": [("DEPLOYMENT", "deployment customer web content only", "web content"),
                        ("LOG", "INFO cache hit for product image", "cache hit")],
    },
    "cache_invalidation_issue": {
        "support": [
            ("LOG", "ERROR stale cache value served after invalidation event for catalog item", "cache stale"),
            ("METRIC", "metric_name=cache_miss_rate value=0.01 service=catalog-api; invalidation miss rate unexpectedly low", "cache miss"),
            ("ISSUE", "Issue: users see stale catalog data after update; cache TTL not refreshed", "cache ttl"),
        ],
        "contradiction": ("LOG", "INFO cache invalidation completed and source data is fresh; no stale responses", "cache fresh"),
        "distractors": [("COMMIT", "docs: update catalog API reference", "documentation"),
                        ("METRIC", "metric_name=cpu value=28 service=catalog-api", "cpu")],
    },
}


def make_dataset(count_per_scenario: int = 4) -> list[dict]:
    cases = []
    next_evidence = 1
    for scenario, template in SCENARIOS.items():
        for variation in range(1, count_per_scenario + 1):
            incident_no = len(cases) + 1
            started = datetime(2025, 1, 1, 14, 0, tzinfo=timezone.utc) + timedelta(days=incident_no)
            service = {
                "database_regression": "checkout-api",
                "configuration_error": "checkout-api",
                "external_api_failure": "checkout-api",
                "connection_exhaustion": "orders-api",
                "feature_flag_error": "checkout-api",
                "memory_leak": "profile-api",
                "incorrect_deployment": "inventory-api",
                "authentication_failure": "identity-api",
                "network_timeout": "orders-api",
                "cache_invalidation_issue": "catalog-api",
            }[scenario]
            evidence = []
            groups: dict[str, list[str]] = {"support": [], "contradiction": [], "distractor": []}
            records = [
                ("support", entry) for entry in template["support"]
            ] + [("contradiction", template["contradiction"])] + [
                ("distractor", entry) for entry in template["distractors"]
            ]
            for i, (group, (kind, content, source)) in enumerate(records):
                eid = f"EVID-{next_evidence:04d}"
                next_evidence += 1
                stamp = started + timedelta(minutes=i * 2 + variation)
                item = {
                    "id": eid,
                    "type": kind,
                    "source": source,
                    "timestamp": stamp.isoformat(),
                    "content": f"{content}; synthetic_case={incident_no}; variant={variation}",
                    "metadata": {"service": service, "synthetic": True},
                }
                evidence.append(item)
                groups[group].append(eid)
            title = scenario.replace("_", " ").title()
            cases.append(
                {
                    "case_id": f"SYN-{incident_no:03d}",
                    "incident": {
                        "id": f"INC-SYN-{incident_no:03d}",
                        "title": f"{title} investigation case {variation}",
                        "description": "Synthetic evaluation case with relevant, contradictory, and distractor evidence.",
                        "severity": "HIGH",
                        "service": service,
                        "endpoint": "/api/example",
                        "started_at": started.isoformat(),
                        "status": "RESOLVED",
                    },
                    "ground_truth": scenario,
                    "ground_truth_terms": [scenario.replace("_", " "), title, *scenario.split("_")],
                    "evidence": evidence,
                    "supporting_evidence_ids": groups["support"],
                    "contradictory_evidence_ids": groups["contradiction"],
                    "distractor_evidence_ids": groups["distractor"],
                }
            )
    return cases


if __name__ == "__main__":
    destination = Path(__file__).with_name("dataset") / "synthetic_incidents.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    cases = make_dataset()
    destination.write_text(json.dumps(cases, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(cases)} SYNTHETIC EVALUATION DATA cases to {destination}")
