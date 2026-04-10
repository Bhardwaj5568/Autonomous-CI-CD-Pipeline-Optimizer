from datetime import datetime, timezone

from app.connectors.base import SourceMapper
from app.schemas import NormalizedEvent


class GitLabCIMapper(SourceMapper):
    def map_to_normalized_events(self, payload: dict) -> list[NormalizedEvent]:
        pipeline = payload.get("pipeline", {})
        jobs = payload.get("jobs", [])

        run_id = str(pipeline.get("id", payload.get("run_id", "gl-run-unknown")))
        repository_id = str(payload.get("project_id", payload.get("repository_id", "unknown-project")))
        pipeline_id = str(pipeline.get("id", payload.get("pipeline_id", run_id)))

        if not jobs:
            jobs = [
                {
                    "id": "run",
                    "name": "pipeline",
                    "status": pipeline.get("status", "success"),
                    "duration_ms": int(float(pipeline.get("duration", 0)) * 1000),
                }
            ]

        events: list[NormalizedEvent] = []
        now = datetime.now(timezone.utc)
        for job in jobs:
            events.append(
                NormalizedEvent(
                    source_system="gitlab_ci",
                    tenant_id=str(payload.get("tenant_id", "default-tenant")),
                    repository_id=repository_id,
                    pipeline_id=pipeline_id,
                    run_id=run_id,
                    job_id=str(job.get("id", "")),
                    stage_name=str(job.get("stage", job.get("name", "job"))),
                    event_type="completed",
                    event_ts_utc=now,
                    duration_ms=int(job.get("duration_ms", 0) or int(float(job.get("duration", 0)) * 1000)),
                    status=str(job.get("status", "success")),
                    branch=str(payload.get("ref", "unknown")),
                    commit_sha=str(payload.get("sha", "unknown")),
                    actor=str(payload.get("user_name", "gitlab-ci")),
                    environment=str(job.get("environment", "")),
                    retry_count=int(job.get("retry", 0)),
                    failure_signature=str(job.get("failure_reason", "")) or None,
                    log_excerpt_hash=str(job.get("trace_hash", "")) or None,
                    metadata=job,
                )
            )

        return events
