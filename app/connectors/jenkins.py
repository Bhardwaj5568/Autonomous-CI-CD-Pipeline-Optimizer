from datetime import datetime, timezone

from app.connectors.base import SourceMapper
from app.schemas import NormalizedEvent


class JenkinsMapper(SourceMapper):
    def map_to_normalized_events(self, payload: dict) -> list[NormalizedEvent]:
        build = payload.get("build", {})
        stages = payload.get("stages", [])
        build_console_hash = str(build.get("log_excerpt_hash", payload.get("log_excerpt_hash", ""))) or None
        build_failure_signature = str(build.get("failure_signature", payload.get("failure_signature", ""))) or None

        run_id = str(build.get("number", payload.get("run_id", "jk-run-unknown")))
        repository_id = str(payload.get("job_name", payload.get("repository_id", "unknown-job")))
        pipeline_id = str(payload.get("pipeline_id", repository_id))

        if not stages:
            stages = [
                {
                    "id": "run",
                    "name": "build",
                    "status": build.get("result", "SUCCESS"),
                    "duration_ms": build.get("duration", 0),
                }
            ]

        events: list[NormalizedEvent] = []
        now = datetime.now(timezone.utc)
        for stage in stages:
            status = str(stage.get("status", "SUCCESS")).lower()
            if status == "success":
                status = "completed"
            elif status in {"failure", "failed"}:
                status = "failed"

            events.append(
                NormalizedEvent(
                    source_system="jenkins",
                    tenant_id=str(payload.get("tenant_id", "default-tenant")),
                    repository_id=repository_id,
                    pipeline_id=pipeline_id,
                    run_id=run_id,
                    job_id=str(stage.get("id", "")),
                    stage_name=str(stage.get("name", "stage")),
                    event_type="completed",
                    event_ts_utc=now,
                    duration_ms=int(stage.get("duration_ms", 0)),
                    status=status,
                    branch=str(payload.get("branch", "unknown")),
                    commit_sha=str(payload.get("commit_sha", "unknown")),
                    actor=str(payload.get("actor", "jenkins")),
                    environment=str(payload.get("environment", "")),
                    retry_count=int(stage.get("retries", 0)),
                    failure_signature=str(stage.get("failure_signature", build_failure_signature or "")) or None,
                    log_excerpt_hash=str(stage.get("log_excerpt_hash", build_console_hash or "")) or None,
                    metadata=stage,
                )
            )

        return events
