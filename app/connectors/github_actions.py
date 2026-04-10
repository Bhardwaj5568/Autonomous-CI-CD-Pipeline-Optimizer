from datetime import datetime, timezone

from app.connectors.base import SourceMapper
from app.schemas import NormalizedEvent


class GitHubActionsMapper(SourceMapper):
    def map_to_normalized_events(self, payload: dict) -> list[NormalizedEvent]:
        run = payload.get("workflow_run", {})
        jobs = payload.get("jobs", [])

        run_id = str(run.get("id", payload.get("run_id", "gh-run-unknown")))
        repository_id = str(run.get("repository_id", payload.get("repository_id", "unknown-repo")))
        pipeline_id = str(run.get("workflow_id", payload.get("pipeline_id", "unknown-pipeline")))

        if not jobs:
            jobs = [
                {
                    "id": "run",
                    "name": "workflow_run",
                    "status": run.get("conclusion", "completed"),
                    "duration_ms": payload.get("duration_ms", 0),
                    "run_attempt": run.get("run_attempt", 0),
                }
            ]

        events: list[NormalizedEvent] = []
        now = datetime.now(timezone.utc)
        for job in jobs:
            events.append(
                NormalizedEvent(
                    source_system="github_actions",
                    tenant_id=str(payload.get("tenant_id", "default-tenant")),
                    repository_id=repository_id,
                    pipeline_id=pipeline_id,
                    run_id=run_id,
                    job_id=str(job.get("id", "")),
                    stage_name=str(job.get("name", "job")),
                    event_type="completed",
                    event_ts_utc=now,
                    duration_ms=int(job.get("duration_ms", 0)),
                    status=str(job.get("status", "completed")),
                    branch=str(run.get("head_branch", payload.get("branch", "unknown"))),
                    commit_sha=str(run.get("head_sha", payload.get("commit_sha", "unknown"))),
                    actor=str(payload.get("actor", "github-actions")),
                    environment=str(payload.get("environment", "")),
                    retry_count=int(job.get("run_attempt", 0)),
                    failure_signature=str(job.get("failure_signature", "")) or None,
                    log_excerpt_hash=str(job.get("log_excerpt_hash", "")) or None,
                    metadata=job,
                )
            )

        return events
