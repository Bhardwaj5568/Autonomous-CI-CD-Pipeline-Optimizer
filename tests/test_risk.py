import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import Base
from app.models import PipelineRun, RiskAssessment
from app.risk import calculate_risk


def _make_session() -> Session:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    return SessionLocal()


def test_calculate_risk_returns_block_for_high_failure_pattern():
    events = [
        {"run_id": "r1", "status": "failed", "retry_count": 2, "duration_ms": 600000},
        {"run_id": "r1", "status": "failed", "retry_count": 1, "duration_ms": 500000},
        {"run_id": "r1", "status": "completed", "retry_count": 0, "duration_ms": 450000},
    ]

    result = calculate_risk(events)

    assert result.run_id == "r1"
    assert result.risk_score >= 70
    assert result.recommendation in {"delay", "block"}


def test_calculate_risk_returns_deploy_for_clean_pattern():
    events = [
        {"run_id": "r2", "status": "completed", "retry_count": 0, "duration_ms": 60000},
        {"run_id": "r2", "status": "completed", "retry_count": 0, "duration_ms": 45000},
    ]

    result = calculate_risk(events)

    assert result.run_id == "r2"
    assert result.risk_score < 50
    assert result.recommendation == "deploy"


def test_calculate_risk_uses_branch_and_environment_context():
    feature_events = [
        {
            "run_id": "r3",
            "repository_id": "repo-1",
            "pipeline_id": "pipe-1",
            "branch": "feature/login",
            "environment": "dev",
            "status": "completed",
            "retry_count": 0,
            "duration_ms": 60000,
        },
        {
            "run_id": "r3",
            "repository_id": "repo-1",
            "pipeline_id": "pipe-1",
            "branch": "feature/login",
            "environment": "dev",
            "status": "failed",
            "retry_count": 1,
            "duration_ms": 120000,
        },
    ]

    prod_events = [dict(item, branch="main", environment="production") for item in feature_events]

    feature_result = calculate_risk(feature_events)
    prod_result = calculate_risk(prod_events)

    assert prod_result.risk_score > feature_result.risk_score
    assert prod_result.reasons["factor_breakdown"]["branch_criticality"] > feature_result.reasons["factor_breakdown"]["branch_criticality"]
    assert prod_result.reasons["factor_breakdown"]["environment_criticality"] > feature_result.reasons["factor_breakdown"]["environment_criticality"]
    assert "Branch context" in prod_result.reasons["summary"]


def test_calculate_risk_uses_recent_history_for_trend_adjustment():
    db = _make_session()

    for index in range(3):
        run_id = f"hist-{index}"
        db.add(
            PipelineRun(
                run_id=run_id,
                source_system="github_actions",
                repository_id="repo-2",
                pipeline_id="pipe-2",
                branch="main",
                commit_sha=f"sha-{index}",
                status="success",
                total_duration_ms=60000,
                event_count=2,
            )
        )
        db.add(
            RiskAssessment(
                run_id=run_id,
                risk_score=18,
                recommendation="deploy",
                confidence=0.8,
                reasons={"summary": "historical baseline"},
            )
        )

    db.commit()

    current_events = [
        {
            "run_id": "current-1",
            "repository_id": "repo-2",
            "pipeline_id": "pipe-2",
            "branch": "main",
            "environment": "production",
            "status": "failed",
            "retry_count": 2,
            "duration_ms": 300000,
        },
        {
            "run_id": "current-1",
            "repository_id": "repo-2",
            "pipeline_id": "pipe-2",
            "branch": "main",
            "environment": "production",
            "status": "failed",
            "retry_count": 0,
            "duration_ms": 240000,
        },
    ]

    score_without_history = calculate_risk(current_events)
    score_with_history = calculate_risk(current_events, db=db)

    assert score_with_history.risk_score > score_without_history.risk_score
    assert score_with_history.reasons["trend"]["recent_runs_count"] == 3
    assert score_with_history.reasons["factor_breakdown"]["trend_adjustment"] > 0
    assert "Trend comparison" in score_with_history.reasons["summary"]
