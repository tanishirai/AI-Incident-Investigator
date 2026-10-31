from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SCENARIOS = {
    "database_regression": {
        "service": "checkout-api",
        "title": "Checkout latency spike after query change",
        "description": "Checkout requests slow down shortly after a query optimization release.",
        "logs": [
            {"level": "INFO", "message": "Checkout request completed", "status_code": 200, "endpoint": "/checkout"},
            {"level": "ERROR", "message": "database query timeout after 2800ms", "error_type": "QueryTimeout", "status_code": 500, "endpoint": "/checkout"},
            {"level": "ERROR", "message": "slow query plan changed after index migration", "error_type": "QueryRegression", "status_code": 504, "endpoint": "/checkout"},
            {"level": "INFO", "message": "database CPU healthy and unchanged"},
        ],
        "deployments": [{"version": "v4.2.1", "status": "complete", "changed_files": ["services/checkout/queries.py"]}],
        "commits": [{"sha": "a81c04f2", "author": "Demo Engineer", "message": "Optimize checkout query join plan", "changed_files": ["services/checkout/queries.py"]}],
        "metrics": [
            {"metric_name": "latency_p95_ms", "value": 210, "service": "checkout-api"},
            {"metric_name": "latency_p95_ms", "value": 2430, "service": "checkout-api"},
            {"metric_name": "db_cpu_percent", "value": 18, "service": "checkout-api"},
        ],
        "issues": [{"issue_id": 142, "title": "Checkout query regression", "body": "Latency rose after query plan optimization; compare the previous plan.", "status": "open", "labels": ["incident"]}],
        "previous": [{"incident_id": "INC-2024-019", "title": "Checkout database query regression", "error_type": "QueryTimeout", "root_cause": "Missing query index after schema change", "resolution": "Restore index and compare execution plan"}],
    },
    "configuration_error": {
        "service": "payments-api",
        "title": "Payment requests fail after timeout configuration update",
        "description": "Requests to the payment provider fail after a deployment changed the timeout setting.",
        "logs": [
            {"level": "ERROR", "message": "configuration error: PAYMENT_TIMEOUT must be at least 10 seconds", "error_type": "InvalidConfiguration", "status_code": 500},
            {"level": "ERROR", "message": "upstream timeout setting is invalid", "error_type": "ConfigError"},
            {"level": "INFO", "message": "cache hit rate remains normal"},
        ],
        "deployments": [{"version": "v2.8.0", "status": "complete", "changed_files": ["deploy/payments.yaml"]}],
        "commits": [{"sha": "b72d190a", "author": "Demo Engineer", "message": "Rename PAYMENT_TIMEOUT environment setting", "changed_files": ["deploy/payments.yaml"]}],
        "metrics": [{"metric_name": "error_rate", "value": 0.38, "service": "payments-api"}, {"metric_name": "cpu", "value": 24, "service": "payments-api"}],
        "issues": [],
        "previous": [],
    },
    "external_api_failure": {
        "service": "orders-api",
        "title": "Order confirmation delays from provider errors",
        "description": "The order service sees elevated upstream errors during confirmation.",
        "logs": [
            {"level": "ERROR", "message": "external API returned HTTP 503 from shipping-provider", "error_type": "UpstreamUnavailable", "status_code": 503},
            {"level": "ERROR", "message": "provider request timed out after 5s", "error_type": "ProviderTimeout"},
            {"level": "INFO", "message": "local database healthy"},
        ],
        "deployments": [],
        "commits": [],
        "metrics": [{"metric_name": "upstream_latency_ms", "value": 4200, "service": "orders-api"}, {"metric_name": "error_rate", "value": 0.31, "service": "orders-api"}],
        "issues": [{"issue_id": 77, "title": "Shipping provider intermittent outage", "body": "Several requests time out in eu-west.", "status": "open", "labels": ["provider"]}],
        "previous": [],
    },
    "connection_exhaustion": {
        "service": "orders-api",
        "title": "Order API cannot acquire database connections",
        "description": "Requests queue waiting for the database connection pool.",
        "logs": [
            {"level": "ERROR", "message": "database connection pool timeout; connections exhausted", "error_type": "PoolTimeout", "status_code": 503},
            {"level": "ERROR", "message": "too many connections active for orders-api"},
            {"level": "INFO", "message": "database connection count is 148"},
        ],
        "deployments": [],
        "commits": [],
        "metrics": [{"metric_name": "db_connections", "value": 148, "service": "orders-api"}, {"metric_name": "request_count", "value": 910, "service": "orders-api"}],
        "issues": [],
        "previous": [],
    },
    "memory_leak": {
        "service": "profile-api",
        "title": "Profile API restarts after sustained memory growth",
        "description": "Process memory rises continuously and containers restart.",
        "logs": [
            {"level": "ERROR", "message": "container terminated after out of memory kill", "error_type": "OOMKilled", "status_code": 500},
            {"level": "ERROR", "message": "heap retention observed in response cache"},
            {"level": "INFO", "message": "memory usage reached 93 percent"},
        ],
        "deployments": [{"version": "v3.4.0", "status": "complete", "changed_files": ["services/profile/cache.py"]}],
        "commits": [{"sha": "c113ad72", "author": "Demo Engineer", "message": "Add in-memory profile response cache", "changed_files": ["services/profile/cache.py"]}],
        "metrics": [{"metric_name": "memory_percent", "value": 93, "service": "profile-api"}, {"metric_name": "restarts", "value": 5, "service": "profile-api"}],
        "issues": [],
        "previous": [],
    },
    "feature_flag_error": {
        "service": "checkout-api",
        "title": "Checkout flow fails after feature rollout",
        "description": "A newly enabled flow causes errors for a wider audience than expected.",
        "logs": [
            {"level": "ERROR", "message": "feature flag checkout_v2 selected unsupported payment path", "error_type": "FlagPathError", "status_code": 500},
            {"level": "INFO", "message": "checkout_v2 flag enabled for tenant"},
        ],
        "deployments": [{"version": "v5.1.0", "status": "complete", "changed_files": ["flags/checkout.yaml"]}],
        "commits": [{"sha": "d9013c70", "author": "Demo Engineer", "message": "Increase checkout_v2 rollout to 100 percent", "changed_files": ["flags/checkout.yaml"]}],
        "metrics": [{"metric_name": "error_rate", "value": 0.22, "service": "checkout-api"}],
        "issues": [{"issue_id": 210, "title": "Checkout v2 flag enabled unexpectedly", "body": "Unexpected tenants report errors.", "status": "open", "labels": ["feature-flag"]}],
        "previous": [],
    },
}


def generate(scenario: str, output_dir: Path) -> Path:
    if scenario not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario}")
    data = SCENARIOS[scenario]
    started = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=15)
    incident_id = f"INC-{uuid.uuid4().hex[:8].upper()}"
    output_dir.mkdir(parents=True, exist_ok=True)
    incident = {
        "id": incident_id,
        "title": data["title"],
        "description": data["description"],
        "service": data["service"],
        "endpoint": "/api/example",
        "severity": "HIGH",
        "status": "OPEN",
        "started_at": started.isoformat(),
    }
    files = {
        "incident.json": incident,
        "logs.json": [
            {"timestamp": (started + timedelta(minutes=index + 1)).isoformat(), "service": data["service"], **row}
            for index, row in enumerate(data["logs"])
        ],
        "deployments.json": [
            {"timestamp": (started - timedelta(minutes=2)).isoformat(), "service": data["service"], **row}
            for row in data["deployments"]
        ],
        "commits.json": [
            {"timestamp": (started - timedelta(minutes=5)).isoformat(), "service": data["service"], **row}
            for row in data["commits"]
        ],
        "metrics.json": [
            {"timestamp": (started + timedelta(minutes=index)).isoformat(), **row}
            for index, row in enumerate(data["metrics"])
        ],
        "issues.json": data["issues"],
        "previous_incidents.json": data["previous"],
    }
    for filename, value in files.items():
        (output_dir / filename).write_text(json.dumps(value, indent=2), encoding="utf-8")
    print(f"Generated {scenario} synthetic evidence in {output_dir}")
    return output_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a synthetic incident and evidence files.")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="database_regression")
    parser.add_argument("--output", default=str(ROOT / "sample_data" / "generated"))
    args = parser.parse_args()
    generate(args.scenario, Path(args.output))


if __name__ == "__main__":
    main()
