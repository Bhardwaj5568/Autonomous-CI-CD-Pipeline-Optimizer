from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    source_system: Mapped[str] = mapped_column(String(32), index=True)
    repository_id: Mapped[str] = mapped_column(String(128), index=True)
    pipeline_id: Mapped[str] = mapped_column(String(128), index=True)
    branch: Mapped[str] = mapped_column(String(128), default="unknown")
    commit_sha: Mapped[str] = mapped_column(String(128), default="unknown")
    status: Mapped[str] = mapped_column(String(32), default="unknown")
    total_duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PipelineEvent(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_system: Mapped[str] = mapped_column(String(32), index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    repository_id: Mapped[str] = mapped_column(String(128), index=True)
    pipeline_id: Mapped[str] = mapped_column(String(128), index=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    job_id: Mapped[str] = mapped_column(String(128), default="")
    stage_name: Mapped[str] = mapped_column(String(128), default="")
    event_type: Mapped[str] = mapped_column(String(32), default="")
    event_ts_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="")
    branch: Mapped[str] = mapped_column(String(128), default="")
    commit_sha: Mapped[str] = mapped_column(String(128), default="")
    actor: Mapped[str] = mapped_column(String(128), default="")
    environment: Mapped[str] = mapped_column(String(64), default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    failure_signature: Mapped[str] = mapped_column(String(256), default="")
    log_excerpt_hash: Mapped[str] = mapped_column(String(256), default="")
    metadata_version: Mapped[str] = mapped_column(String(16), default="v1")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RiskAssessment(Base):
    __tablename__ = "risk_assessments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    risk_score: Mapped[int] = mapped_column(Integer, index=True)
    recommendation: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reasons: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class RecommendationFeedback(Base):
    __tablename__ = "recommendation_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(128), index=True)
    vote: Mapped[str] = mapped_column(String(16), index=True)
    comment: Mapped[str] = mapped_column(String(512), default="")
    actor: Mapped[str] = mapped_column(String(128), default="unknown")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_type: Mapped[str] = mapped_column(String(64), index=True)
    actor: Mapped[str] = mapped_column(String(128), index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
