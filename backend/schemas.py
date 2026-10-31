from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    INVESTIGATING = "INVESTIGATING"


class EvidenceType(str, Enum):
    LOG = "LOG"
    COMMIT = "COMMIT"
    DEPLOYMENT = "DEPLOYMENT"
    ISSUE = "ISSUE"
    METRIC = "METRIC"
    PREVIOUS_INCIDENT = "PREVIOUS_INCIDENT"


class IncidentInput(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    description: str = Field(default="", max_length=5000)
    severity: Severity = Severity.MEDIUM
    service: str = Field(default="unknown", max_length=160)
    endpoint: str = Field(default="", max_length=240)
    started_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None
    status: IncidentStatus = IncidentStatus.OPEN

    @field_validator("started_at", "resolved_at")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class EvidenceInput(BaseModel):
    incident_id: str
    type: EvidenceType
    source: str = Field(min_length=1, max_length=400)
    timestamp: datetime | None = None
    content: str = Field(min_length=1, max_length=12000)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def ensure_timezone(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class Hypothesis(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=5, max_length=2000)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    reasoning: str = Field(default="", max_length=2000)
    suggested_diagnostic: str = Field(default="", max_length=1000)
    severity_if_true: Severity = Severity.MEDIUM
    hypothesis_id: str = ""
    heuristic_confidence: float | None = Field(default=None, ge=0, le=1)


class InvestigationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    investigation_summary: str = Field(min_length=1, max_length=3000)
    hypotheses: list[Hypothesis] = Field(default_factory=list, max_length=8)
    unknowns: list[str] = Field(default_factory=list, max_length=15)
    recommended_next_steps: list[str] = Field(default_factory=list, max_length=15)
    requires_human_review: bool = True
    evidence_status: str = "SUFFICIENT_EVIDENCE"
    method: str = "deterministic"
    model: str | None = None
    error_notice: str | None = None
    usage: dict[str, int] = Field(default_factory=dict)
    analysis_seconds: float = 0

    @field_validator("evidence_status")
    @classmethod
    def validate_evidence_status(cls, value: str) -> str:
        allowed = {"SUFFICIENT_EVIDENCE", "INSUFFICIENT_EVIDENCE"}
        normalized = value.upper().strip()
        if normalized not in allowed:
            raise ValueError(f"evidence_status must be one of {sorted(allowed)}")
        return normalized

    @model_validator(mode="after")
    def require_review_for_hypotheses(self) -> "InvestigationResult":
        if self.hypotheses:
            self.requires_human_review = True
        return self


class ReviewInput(BaseModel):
    investigation_id: str
    hypothesis_id: str
    decision: str
    notes: str = Field(default="", max_length=4000)

    @field_validator("decision")
    @classmethod
    def validate_decision(cls, value: str) -> str:
        normalized = value.upper().strip()
        if normalized not in {"CONFIRM", "REJECT", "UNRESOLVED"}:
            raise ValueError("decision must be CONFIRM, REJECT, or UNRESOLVED")
        return normalized
