from collections import Counter
from statistics import mean

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import PipelineRun, RiskAssessment
from app.schemas import Recommendation, RiskAssessmentResponse


def _clean_value(value: object) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.lower() in {"none", "unknown", "null"}:
        return ""
    return text


def _most_common(values: list[object], default: str = "unknown") -> str:
    cleaned = [_clean_value(value) for value in values]
    cleaned = [value for value in cleaned if value]
    if not cleaned:
        return default
    return Counter(cleaned).most_common(1)[0][0]


def _first_non_empty(values: list[object], default: str = "unknown") -> str:
    for value in values:
        cleaned = _clean_value(value)
        if cleaned:
            return cleaned
    return default


def _categorize_branch(branch: str) -> tuple[int, str]:
    branch_value = branch.lower()
    if branch_value in {"main", "master"} or branch_value.startswith(("main/", "master/")):
        return 10, "Main or master branches carry stronger release risk."
    if branch_value.startswith(("release", "hotfix")):
        return 12, "Release and hotfix branches are treated as production-adjacent."
    if branch_value.startswith(("feature", "feat", "bugfix", "experiment", "experimental")):
        return -4, "Feature-style branches are usually lower risk for deployment decisions."
    return 0, "Branch context was not strong enough to adjust risk."


def _categorize_environment(environment: str) -> tuple[int, str]:
    environment_value = environment.lower()
    if environment_value in {"production", "prod"} or "prod" in environment_value:
        return 12, "Production workloads get stricter risk handling."
    if environment_value in {"staging", "stage", "preprod", "uat"}:
        return 5, "Pre-production environments get moderate risk handling."
    if environment_value in {"dev", "development", "test", "qa"}:
        return -3, "Non-production environments are slightly relaxed."
    return 0, "Environment context was not strong enough to adjust risk."


def _load_recent_history(
    db: Session | None,
    repository_id: str,
    pipeline_id: str,
    run_id: str,
    limit: int = 5,
) -> list[dict]:
    if db is None or repository_id == "unknown" or pipeline_id == "unknown":
        return []

    rows = db.execute(
        select(
            PipelineRun.run_id,
            PipelineRun.total_duration_ms,
            PipelineRun.status,
            RiskAssessment.risk_score,
        )
        .join(RiskAssessment, RiskAssessment.run_id == PipelineRun.run_id, isouter=True)
        .where(PipelineRun.repository_id == repository_id)
        .where(PipelineRun.pipeline_id == pipeline_id)
        .where(PipelineRun.run_id != run_id)
        .order_by(PipelineRun.created_at.desc())
        .limit(limit)
    ).all()

    history: list[dict] = []
    for history_run_id, total_duration_ms, status, risk_score in rows:
        history.append(
            {
                "run_id": history_run_id,
                "total_duration_ms": int(total_duration_ms or 0),
                "status": _clean_value(status) or "unknown",
                "risk_score": int(risk_score) if risk_score is not None else None,
            }
        )
    return history


def _trend_adjustment(current_score: int, current_duration: int, recent_history: list[dict]) -> tuple[int, dict]:
    if not recent_history:
        return 0, {
            "recent_runs_count": 0,
            "summary": "No historical runs were found for trend comparison.",
        }

    recent_scores = [item["risk_score"] for item in recent_history if item["risk_score"] is not None]
    recent_durations = [item["total_duration_ms"] for item in recent_history if item["total_duration_ms"] > 0]

    trend_score = 0
    trend_notes: list[str] = []
    trend_details: dict[str, object] = {
        "recent_runs_count": len(recent_history),
        "recent_run_ids": [item["run_id"] for item in recent_history],
    }

    if recent_scores:
        average_score = mean(recent_scores)
        trend_details["average_recent_risk_score"] = round(average_score, 2)
        if current_score >= average_score + 15:
            trend_score += 8
            trend_notes.append("Current run is riskier than the recent average.")
        elif current_score <= average_score - 15:
            trend_score -= 5
            trend_notes.append("Current run is healthier than the recent average.")

    if recent_durations:
        average_duration = mean(recent_durations)
        trend_details["average_recent_duration_ms"] = int(round(average_duration))
        if average_duration > 0 and current_duration >= average_duration * 1.25:
            trend_score += 6
            trend_notes.append("Current run duration is worse than recent history.")
        elif average_duration > 0 and current_duration <= average_duration * 0.8:
            trend_score -= 3
            trend_notes.append("Current run duration is better than recent history.")

    if not trend_notes:
        trend_notes.append("Current run is broadly aligned with recent history.")

    trend_details["summary"] = " ".join(trend_notes)
    trend_details["impact"] = trend_score
    return trend_score, trend_details


def _recommendation_from_score(score: int) -> Recommendation:
    if score >= settings.risk_block_threshold:
        return "block"
    if score >= settings.risk_delay_threshold:
        return "delay"
    if score >= settings.risk_canary_threshold:
        return "canary"
    return "deploy"


def calculate_risk(events: list[dict], db: Session | None = None) -> RiskAssessmentResponse:
    if not events:
        return RiskAssessmentResponse(
            run_id="unknown",
            risk_score=0,
            recommendation="deploy",
            confidence=0.0,
            reasons={"info": "No events found for run."},
        )

    run_id = _first_non_empty([e.get("run_id") for e in events], default="unknown")
    repository_id = _most_common([e.get("repository_id") for e in events])
    pipeline_id = _most_common([e.get("pipeline_id") for e in events])
    source_system = _most_common([e.get("source_system") for e in events])
    branch = _most_common([e.get("branch") for e in events])
    environment = _most_common([e.get("environment") for e in events])
    statuses = Counter((e.get("status") or "").lower() for e in events)
    failures = statuses.get("failed", 0)
    retries = sum(int(e.get("retry_count") or 0) for e in events)
    total_duration = sum(int(e.get("duration_ms") or 0) for e in events)
    max_duration = max(int(e.get("duration_ms") or 0) for e in events)

    score = 0
    factor_breakdown: dict[str, int] = {}
    factors: list[dict[str, object]] = []

    def add_factor(name: str, impact: int, detail: str) -> None:
        factor_breakdown[name] = impact
        factors.append({"name": name, "impact": impact, "detail": detail})

    failure_impact = min(failures * 15, 45)
    retry_impact = min(retries * 8, 24)
    score += failure_impact
    score += retry_impact
    add_factor("failed_events", failure_impact, f"{failures} failed event(s) observed.")
    add_factor("retry_events", retry_impact, f"{retries} retry attempt(s) observed.")

    duration_impact = 0
    if max_duration > 900000:
        duration_impact = 20
    elif max_duration > 420000:
        duration_impact = 12
    elif max_duration > 180000:
        duration_impact = 6
    score += duration_impact
    add_factor("max_stage_duration", duration_impact, f"Longest stage duration was {max_duration} ms.")

    total_duration_impact = 0
    if total_duration > 1800000:
        total_duration_impact = 15
    elif total_duration > 900000:
        total_duration_impact = 8
    score += total_duration_impact
    add_factor("total_run_duration", total_duration_impact, f"Total run duration was {total_duration} ms.")

    branch_impact, branch_detail = _categorize_branch(branch)
    score += branch_impact
    add_factor("branch_criticality", branch_impact, f"Branch '{branch}' -> {branch_detail}")

    environment_impact, environment_detail = _categorize_environment(environment)
    score += environment_impact
    add_factor("environment_criticality", environment_impact, f"Environment '{environment}' -> {environment_detail}")

    recent_history = _load_recent_history(db, repository_id, pipeline_id, run_id)
    pretrend_score = score
    trend_impact, trend_details = _trend_adjustment(pretrend_score, total_duration, recent_history)
    score += trend_impact
    add_factor("trend_adjustment", trend_impact, str(trend_details.get("summary", "Trend comparison completed.")))

    score = min(score, 100)
    score = max(score, 0)

    known_context_fields = sum(1 for value in (repository_id, pipeline_id, branch, environment) if value != "unknown")
    context_completeness = known_context_fields / 4
    branch_values = {_clean_value(e.get("branch")).lower() for e in events if _clean_value(e.get("branch"))}
    environment_values = {
        _clean_value(e.get("environment")).lower() for e in events if _clean_value(e.get("environment"))
    }
    repository_values = {_clean_value(e.get("repository_id")) for e in events if _clean_value(e.get("repository_id"))}
    pipeline_values = {_clean_value(e.get("pipeline_id")) for e in events if _clean_value(e.get("pipeline_id"))}
    signal_consistency = (
        (1 if len(branch_values) <= 1 else 0)
        + (1 if len(environment_values) <= 1 else 0)
        + (1 if len(repository_values) <= 1 else 0)
        + (1 if len(pipeline_values) <= 1 else 0)
    ) / 4
    history_factor = min(len(recent_history) / 5, 1.0)
    confidence = 0.40
    confidence += min(len(events) / 100, 0.20)
    confidence += 0.15 * context_completeness
    confidence += 0.12 * signal_consistency
    confidence += 0.13 * history_factor
    confidence = min(confidence, 0.95)
    recommendation = _recommendation_from_score(score)

    summary_parts = [
        f"Base signals from {failures} failed event(s), {retries} retry attempt(s), max duration {max_duration} ms, and total duration {total_duration} ms.",
        f"Branch context '{branch}' adjusted risk by {branch_impact}.",
        f"Environment context '{environment}' adjusted risk by {environment_impact}.",
    ]
    if recent_history:
        summary_parts.append(f"Trend comparison used {len(recent_history)} recent run(s) and adjusted risk by {trend_impact}.")
    summary_parts.append(f"Final recommendation: {recommendation}.")

    reasons = {
        "summary": " ".join(summary_parts),
        "failed_events": failures,
        "retry_events": retries,
        "max_stage_duration_ms": max_duration,
        "total_run_duration_ms": total_duration,
        "status_counts": dict(statuses),
        "context": {
            "source_system": source_system,
            "repository_id": repository_id,
            "pipeline_id": pipeline_id,
            "branch": branch,
            "environment": environment,
        },
        "factor_breakdown": factor_breakdown,
        "factors": factors,
        "trend": trend_details,
        "confidence_components": {
            "event_volume": round(min(len(events) / 100, 0.20), 2),
            "context_completeness": round(0.15 * context_completeness, 2),
            "signal_consistency": round(0.12 * signal_consistency, 2),
            "history_factor": round(0.13 * history_factor, 2),
        },
    }

    return RiskAssessmentResponse(
        run_id=run_id,
        risk_score=score,
        recommendation=recommendation,
        confidence=round(confidence, 2),
        reasons=reasons,
    )
