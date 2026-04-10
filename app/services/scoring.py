from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineEvent, RiskAssessment
from app.risk import calculate_risk
from app.schemas import RiskAssessmentResponse


def score_and_persist_run(db: Session, run_id: str) -> RiskAssessmentResponse | None:
    events = db.execute(select(PipelineEvent).where(PipelineEvent.run_id == run_id)).scalars().all()
    if not events:
        return None

    data = [
        {
            "run_id": e.run_id,
            "status": e.status,
            "retry_count": e.retry_count,
            "duration_ms": e.duration_ms,
        }
        for e in events
    ]

    assessment = calculate_risk(data)
    db_obj = RiskAssessment(
        run_id=assessment.run_id,
        risk_score=assessment.risk_score,
        recommendation=assessment.recommendation,
        confidence=assessment.confidence,
        reasons=assessment.reasons,
    )
    db.add(db_obj)
    db.commit()
    return assessment
