from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SourceSystem = Literal["github_actions", "gitlab_ci", "jenkins"]
Recommendation = Literal["deploy", "canary", "delay", "block"]


class NormalizedEvent(BaseModel):
    source_system: SourceSystem
    tenant_id: str = Field(default="default-tenant", examples=["team-a"])
    repository_id: str = Field(examples=["repo-123"])
    pipeline_id: str = Field(examples=["pipeline-456"])
    run_id: str = Field(examples=["run-789"])
    job_id: str | None = Field(default=None, examples=["job-001"])
    stage_name: str = Field(default="", examples=["build"])
    event_type: str = Field(examples=["job.completed"])
    event_ts_utc: datetime = Field(examples=["2026-04-13T10:15:30Z"])
    duration_ms: int = Field(ge=0, examples=[1450])
    status: str = Field(examples=["success"])
    branch: str = Field(default="unknown", examples=["main"])
    commit_sha: str = Field(default="unknown", examples=["a1b2c3d4e5f6"])
    actor: str = Field(default="system", examples=["github-actions[bot]"])
    environment: str = Field(default="", examples=["production"])
    retry_count: int = Field(default=0, ge=0, examples=[0])
    failure_signature: str | None = Field(default=None, examples=["test-failure"])
    log_excerpt_hash: str | None = Field(default=None, examples=["sha256:abc123"])
    metadata_version: str = Field(default="v1", examples=["v1"])
    metadata: dict[str, Any] = Field(default_factory=dict, examples=[{"service": "api", "region": "us-east-1"}])

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system": "github_actions",
                "tenant_id": "team-a",
                "repository_id": "repo-123",
                "pipeline_id": "pipeline-456",
                "run_id": "run-789",
                "job_id": "job-001",
                "stage_name": "build",
                "event_type": "job.completed",
                "event_ts_utc": "2026-04-13T10:15:30Z",
                "duration_ms": 1450,
                "status": "success",
                "branch": "main",
                "commit_sha": "a1b2c3d4e5f6",
                "actor": "github-actions[bot]",
                "environment": "production",
                "retry_count": 0,
                "failure_signature": None,
                "log_excerpt_hash": "sha256:abc123",
                "metadata_version": "v1",
                "metadata": {"service": "api", "region": "us-east-1"},
            }
        }
    }


class SourceEventRequest(BaseModel):
    source_system: SourceSystem
    payload: dict[str, Any]

    model_config = {
        "json_schema_extra": {
            "example": {
                "source_system": "github_actions",
                "payload": {
                    "workflow_run": {
                        "id": 123456789,
                        "name": "CI",
                        "head_branch": "main",
                        "head_sha": "a1b2c3d4e5f6",
                        "conclusion": "success",
                    },
                    "repository": {
                        "full_name": "example-org/example-repo",
                        "id": 987654321,
                    },
                },
            }
        }
    }


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
    vote: Literal["up", "down"] = Field(examples=["up"])
    comment: str = Field(default="", examples=["Build passed after retry."])

    model_config = {
        "json_schema_extra": {
            "example": {
                "vote": "up",
                "comment": "Build passed after retry.",
            }
        }
    }


class FeedbackResponse(BaseModel):
    run_id: str
    vote: str
    actor: str


class QueueStatusResponse(BaseModel):
    queued: int
    processed: int
    failed: int
    duplicate_deliveries: int = 0
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
