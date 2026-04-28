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
            "source_system": e.source_system,
            "repository_id": e.repository_id,
            "pipeline_id": e.pipeline_id,
            "status": e.status,
            "retry_count": e.retry_count,
            "duration_ms": e.duration_ms,
            "branch": e.branch,
            "environment": e.environment,
            "event_ts_utc": e.event_ts_utc,
        }
        for e in events
    ]

    assessment = calculate_risk(data, db=db)
    db_obj = RiskAssessment(
        run_id=assessment.run_id,
        risk_score=assessment.risk_score,
        recommendation=assessment.recommendation,
        confidence=assessment.confidence,
        reasons=assessment.reasons,
    )
    db.add(db_obj)
    db.commit()

    # --- AutoOptimizer Integration ---

    try:
        from app.services.auto_optimizer import AutoOptimizer, AuditLogger
        from app.services.quarantine import auto_quarantine_all
        from app.config import settings
        from app.connectors.github_actions_client import GitHubActionsClient
        from app.connectors.gitlab_ci_client import GitLabCIClient
        from app.connectors.jenkins_client import JenkinsClient

        cicd_clients = {}
        # GitHub Actions client
        if settings.github_token and settings.github_owner and settings.github_repo:
            cicd_clients["github_actions"] = GitHubActionsClient(settings.github_token)
            cicd_clients["github_actions"].owner = settings.github_owner
            cicd_clients["github_actions"].repo = settings.github_repo
        # GitLab CI client
        if settings.gitlab_token and settings.gitlab_project_id:
            cicd_clients["gitlab_ci"] = GitLabCIClient(settings.gitlab_token)
            cicd_clients["gitlab_ci"].project_id = settings.gitlab_project_id
        # Jenkins client
        if settings.jenkins_url and settings.jenkins_user and settings.jenkins_api_token:
            cicd_clients["jenkins"] = JenkinsClient(settings.jenkins_url, settings.jenkins_user, settings.jenkins_api_token)

        audit_logger = AuditLogger(db=db)
        auto_optimizer = AutoOptimizer(cicd_clients, audit_logger, db=db)
        recommendation_payload = {
            "action": assessment.recommendation,
            "system": data[0]["source_system"] if data else "unknown",
            "run_id": run_id,
        }
        auto_optimizer.handle_recommendation(recommendation_payload)

        # Auto-quarantine flaky tests (runs async, doesn't block scoring)
        if assessment.risk_score >= 60:  # Only for risky runs
            try:
                auto_quarantine_all(db, cicd_clients)
            except Exception as qe:
                print(f"[Quarantine] Error: {qe}")

    except Exception as e:
        print(f"[AutoOptimizer] Error during auto-action: {e}")

    return assessment
