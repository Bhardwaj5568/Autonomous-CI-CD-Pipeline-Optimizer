from collections import Counter

from app.config import settings
from app.schemas import Recommendation, RiskAssessmentResponse


def _recommendation_from_score(score: int) -> Recommendation:
    if score >= settings.risk_block_threshold:
        return "block"
    if score >= settings.risk_delay_threshold:
        return "delay"
    if score >= settings.risk_canary_threshold:
        return "canary"
    return "deploy"


def calculate_risk(events: list[dict]) -> RiskAssessmentResponse:
    if not events:
        return RiskAssessmentResponse(
            run_id="unknown",
            risk_score=0,
            recommendation="deploy",
            confidence=0.0,
            reasons={"info": "No events found for run."},
        )

    run_id = events[0]["run_id"]
    statuses = Counter((e.get("status") or "").lower() for e in events)
    failures = statuses.get("failed", 0)
    retries = sum(int(e.get("retry_count") or 0) for e in events)
    total_duration = sum(int(e.get("duration_ms") or 0) for e in events)
    max_duration = max(int(e.get("duration_ms") or 0) for e in events)

    score = 0
    score += min(failures * 15, 45)
    score += min(retries * 8, 24)
    if max_duration > 900000:
        score += 20
    elif max_duration > 420000:
        score += 12
    elif max_duration > 180000:
        score += 6

    if total_duration > 1800000:
        score += 15
    elif total_duration > 900000:
        score += 8

    score = min(score, 100)

    confidence = min(0.55 + (len(events) / 80), 0.95)
    recommendation = _recommendation_from_score(score)

    reasons = {
        "failed_events": failures,
        "retry_events": retries,
        "max_stage_duration_ms": max_duration,
        "total_run_duration_ms": total_duration,
        "status_counts": dict(statuses),
    }

    return RiskAssessmentResponse(
        run_id=run_id,
        risk_score=score,
        recommendation=recommendation,
        confidence=round(confidence, 2),
        reasons=reasons,
    )
