"""
Flaky Test Quarantine Service.

Automatically detects flaky steps from build history and:
  1. Records them in quarantined_steps table
  2. Triggers CI/CD client to remove/disable the step
  3. Provides unquarantine endpoint when step is fixed
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineEvent, QuarantinedStep


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

FLAKY_FAIL_RATE_THRESHOLD = 0.30   # fails >30% of the time
FLAKY_MIN_RUNS = 5                  # need at least 5 runs to judge
FLAKY_CONFIDENCE_THRESHOLD = 0.55  # minimum confidence to auto-quarantine


def detect_flaky_steps(
    db: Session,
    repository_id: str | None = None,
    pipeline_id: str | None = None,
) -> list[dict]:
    """
    Scan PipelineEvent history and return steps that are flaky.
    A step is flaky if it fails intermittently (not always, not never).
    """
    query = select(PipelineEvent)
    if repository_id:
        query = query.where(PipelineEvent.repository_id == repository_id)
    if pipeline_id:
        query = query.where(PipelineEvent.pipeline_id == pipeline_id)
    query = query.order_by(PipelineEvent.created_at.desc()).limit(5000)

    rows = db.execute(query).scalars().all()
    if not rows:
        return []

    # Group by (repo, pipeline, step)
    from collections import defaultdict
    step_data: dict[tuple, list] = defaultdict(list)
    for r in rows:
        if not r.stage_name:
            continue
        key = (r.source_system, r.repository_id, r.pipeline_id, r.stage_name)
        step_data[key].append({
            "status": r.status,
            "duration_ms": r.duration_ms,
            "retry_count": r.retry_count,
        })

    flaky = []
    for (source, repo, pipeline, step), history in step_data.items():
        if len(history) < FLAKY_MIN_RUNS:
            continue

        total = len(history)
        failed = sum(1 for h in history if h["status"] in {"failed", "failure"})
        skipped = sum(1 for h in history if h["status"] == "skipped")
        retries = sum(h["retry_count"] for h in history)

        fail_rate = failed / total
        skip_rate = skipped / total

        # Flaky = fails sometimes but not always, AND has retries
        is_flaky = (
            FLAKY_FAIL_RATE_THRESHOLD <= fail_rate <= 0.85
            and skip_rate < 0.5
        )
        # Also flaky if high retry count even when passing
        high_retry_flaky = (retries / total) >= 1.5 and fail_rate > 0.1

        if not (is_flaky or high_retry_flaky):
            continue

        # Confidence: higher with more runs and clearer signal
        confidence = min(
            (total / 20) * 0.5 + fail_rate * 0.5,
            1.0
        )

        if confidence < FLAKY_CONFIDENCE_THRESHOLD:
            continue

        flaky.append({
            "source_system": source,
            "repository_id": repo,
            "pipeline_id": pipeline,
            "step_name": step,
            "fail_rate": round(fail_rate, 3),
            "skip_rate": round(skip_rate, 3),
            "avg_retries": round(retries / total, 2),
            "total_runs": total,
            "confidence": round(confidence, 2),
            "reason": (
                f"Fails {int(fail_rate * 100)}% of the time across {total} runs "
                f"with avg {round(retries/total, 1)} retries/run — intermittent failure pattern"
            ),
        })

    return sorted(flaky, key=lambda x: x["confidence"], reverse=True)


# ---------------------------------------------------------------------------
# Quarantine actions
# ---------------------------------------------------------------------------

def quarantine_step(
    db: Session,
    source_system: str,
    repository_id: str,
    pipeline_id: str,
    step_name: str,
    reason: str,
    fail_rate: float,
    confidence: float,
    quarantined_by: str = "auto-optimizer",
    cicd_client=None,
    repo_owner: str | None = None,
    repo_name: str | None = None,
    workflow_path: str | None = None,
    gitlab_project_id: str | None = None,
    jenkins_job_name: str | None = None,
) -> dict[str, Any]:
    """
    Quarantine a flaky step:
    1. Record in DB
    2. Optionally remove from CI/CD config via client
    """
    result: dict[str, Any] = {
        "step_name": step_name,
        "source_system": source_system,
        "repository_id": repository_id,
        "pipeline_id": pipeline_id,
        "db_recorded": False,
        "cicd_applied": False,
        "cicd_result": None,
        "error": None,
    }

    # Check if already quarantined
    existing = db.execute(
        select(QuarantinedStep)
        .where(QuarantinedStep.repository_id == repository_id)
        .where(QuarantinedStep.pipeline_id == pipeline_id)
        .where(QuarantinedStep.step_name == step_name)
        .where(QuarantinedStep.active == True)
    ).scalar_one_or_none()

    if existing:
        result["db_recorded"] = True
        result["note"] = "Already quarantined"
        return result

    # Record in DB
    try:
        db.add(QuarantinedStep(
            source_system=source_system,
            repository_id=repository_id,
            pipeline_id=pipeline_id,
            step_name=step_name,
            reason=reason,
            fail_rate=fail_rate,
            confidence=confidence,
            quarantined_by=quarantined_by,
        ))
        db.commit()
        result["db_recorded"] = True
    except Exception as e:
        result["error"] = f"DB error: {e}"
        return result

    # Apply via CI/CD client
    if cicd_client is None:
        result["cicd_applied"] = False
        result["note"] = "No CI/CD client configured — recorded in DB only"
        return result

    try:
        if source_system == "github_actions" and repo_owner and repo_name and workflow_path:
            cicd_result = cicd_client.quarantine_test(repo_owner, repo_name, workflow_path, step_name)
            result["cicd_applied"] = cicd_result.get("status_code") in {200, 201}
            result["cicd_result"] = cicd_result

        elif source_system == "gitlab_ci" and gitlab_project_id:
            cicd_result = cicd_client.quarantine_test(gitlab_project_id, step_name)
            result["cicd_applied"] = True
            result["cicd_result"] = cicd_result

        elif source_system == "jenkins" and jenkins_job_name:
            cicd_result = cicd_client.quarantine_test(jenkins_job_name, step_name)
            result["cicd_applied"] = True
            result["cicd_result"] = cicd_result

        else:
            result["note"] = "Missing required params for CI/CD apply — recorded in DB only"

    except Exception as e:
        result["error"] = f"CI/CD apply error: {e}"

    return result


def unquarantine_step(
    db: Session,
    repository_id: str,
    pipeline_id: str,
    step_name: str,
) -> dict[str, Any]:
    """Mark a quarantined step as resolved (fixed)."""
    record = db.execute(
        select(QuarantinedStep)
        .where(QuarantinedStep.repository_id == repository_id)
        .where(QuarantinedStep.pipeline_id == pipeline_id)
        .where(QuarantinedStep.step_name == step_name)
        .where(QuarantinedStep.active == True)
    ).scalar_one_or_none()

    if not record:
        return {"success": False, "message": "No active quarantine found for this step"}

    record.active = False
    record.resolved_at = datetime.utcnow()
    db.commit()
    return {"success": True, "step_name": step_name, "resolved_at": record.resolved_at.isoformat()}


def get_quarantine_report(db: Session, repository_id: str | None = None) -> dict[str, Any]:
    """Get full quarantine status report."""
    query = select(QuarantinedStep)
    if repository_id:
        query = query.where(QuarantinedStep.repository_id == repository_id)

    all_records = db.execute(query.order_by(QuarantinedStep.created_at.desc())).scalars().all()

    active = [r for r in all_records if r.active]
    resolved = [r for r in all_records if not r.active]

    return {
        "total_quarantined": len(active),
        "total_resolved": len(resolved),
        "active_quarantines": [
            {
                "step_name": r.step_name,
                "source_system": r.source_system,
                "repository_id": r.repository_id,
                "pipeline_id": r.pipeline_id,
                "fail_rate": r.fail_rate,
                "confidence": r.confidence,
                "reason": r.reason,
                "quarantined_by": r.quarantined_by,
                "quarantined_at": r.created_at.isoformat(),
            }
            for r in active
        ],
        "resolved_quarantines": [
            {
                "step_name": r.step_name,
                "repository_id": r.repository_id,
                "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
            }
            for r in resolved
        ],
    }


def auto_quarantine_all(
    db: Session,
    cicd_clients: dict | None = None,
) -> dict[str, Any]:
    """
    Scan all pipelines, detect flaky steps, and auto-quarantine them.
    Called automatically after scoring if confidence is high enough.
    """
    flaky_steps = detect_flaky_steps(db)
    results = []

    for step in flaky_steps:
        client = None
        if cicd_clients:
            client = cicd_clients.get(step["source_system"])

        r = quarantine_step(
            db=db,
            source_system=step["source_system"],
            repository_id=step["repository_id"],
            pipeline_id=step["pipeline_id"],
            step_name=step["step_name"],
            reason=step["reason"],
            fail_rate=step["fail_rate"],
            confidence=step["confidence"],
            quarantined_by="auto-optimizer",
            cicd_client=client,
        )
        results.append(r)

    return {
        "flaky_detected": len(flaky_steps),
        "quarantined": sum(1 for r in results if r.get("db_recorded")),
        "cicd_applied": sum(1 for r in results if r.get("cicd_applied")),
        "details": results,
    }
