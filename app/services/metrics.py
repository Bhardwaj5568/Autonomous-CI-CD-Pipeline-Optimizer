from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PipelineRun, RiskAssessment, RecommendationFeedback


def compute_kpis(db: Session) -> dict:
    total_runs = db.execute(select(func.count()).select_from(PipelineRun)).scalar_one()
    avg_duration = db.execute(select(func.avg(PipelineRun.total_duration_ms))).scalar() or 0

    avg_risk = db.execute(select(func.avg(RiskAssessment.risk_score))).scalar() or 0
    total_assessments = db.execute(select(func.count()).select_from(RiskAssessment)).scalar_one()
    high_risk = db.execute(
        select(func.count()).select_from(RiskAssessment).where(RiskAssessment.risk_score >= 70)
    ).scalar_one()

    feedback_total = db.execute(select(func.count()).select_from(RecommendationFeedback)).scalar_one()
    feedback_positive = db.execute(
        select(func.count()).select_from(RecommendationFeedback).where(RecommendationFeedback.vote == "up")
    ).scalar_one()

    precision = 0.0
    if feedback_total:
        precision = round((feedback_positive / feedback_total) * 100, 2)

    high_risk_rate = 0.0
    if total_assessments:
        high_risk_rate = round((high_risk / total_assessments) * 100, 2)

    return {
        "total_runs": total_runs,
        "avg_pipeline_duration_ms": int(avg_duration),
        "total_assessments": total_assessments,
        "avg_risk_score": round(float(avg_risk), 2),
        "high_risk_rate_percent": high_risk_rate,
        "feedback_total": feedback_total,
        "feedback_positive_percent": precision,
    }
