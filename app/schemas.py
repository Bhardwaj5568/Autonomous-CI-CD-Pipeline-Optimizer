from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceSystem = Literal["github_actions", "gitlab_ci", "jenkins"]
Recommendation = Literal["deploy", "canary", "delay", "block"]


class NormalizedEvent(BaseModel):
    source_system: SourceSystem
    tenant_id: str = "default-tenant"
    repository_id: str
    pipeline_id: str
    run_id: str
    job_id: str | None = None
    stage_name: str = ""
    event_type: str
    event_ts_utc: datetime
    duration_ms: int = Field(ge=0)
    status: str
    branch: str = "unknown"
    commit_sha: str = "unknown"
    actor: str = "system"
    environment: str = ""
    retry_count: int = Field(default=0, ge=0)
    failure_signature: str | None = None
    log_excerpt_hash: str | None = None
    metadata_version: str = "v1"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceEventRequest(BaseModel):
    source_system: SourceSystem
    payload: dict[str, Any]


class IngestResponse(BaseModel):
    ingested_count: int
    run_ids: list[str]


class RiskAssessmentResponse(BaseModel):
    run_id: str
    risk_score: int
    recommendation: Recommendation
    confidence: float
    reasons: dict[str, Any]


class PipelineRunResponse(BaseModel):
    run_id: str
    source_system: str
    repository_id: str
    pipeline_id: str
    branch: str
    commit_sha: str
    status: str
    total_duration_ms: int
    event_count: int


class FeedbackRequest(BaseModel):
    vote: Literal["up", "down"]
    comment: str = ""


class FeedbackResponse(BaseModel):
    run_id: str
    vote: str
    actor: str


class QueueStatusResponse(BaseModel):
    queued: int
    processed: int
    failed: int
    last_error: str


class KPIResponse(BaseModel):
    total_runs: int
    avg_pipeline_duration_ms: int
    total_assessments: int
    avg_risk_score: float
    high_risk_rate_percent: float
    feedback_total: int
    feedback_positive_percent: float


class AuditLogResponse(BaseModel):
    action_type: str
    actor: str
    details: dict[str, Any]
