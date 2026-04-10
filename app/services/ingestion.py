from collections import defaultdict
import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineEvent, PipelineRun
from app.schemas import NormalizedEvent


def ingest_events(db: Session, events: list[NormalizedEvent]) -> list[str]:
    if not events:
        return []

    run_map: dict[str, dict] = defaultdict(lambda: {"duration": 0, "count": 0})

    for ev in events:
        dedupe_key = hashlib.sha1(
            f"{ev.source_system}|{ev.run_id}|{ev.job_id}|{ev.event_type}|{ev.event_ts_utc.isoformat()}".encode("utf-8")
        ).hexdigest()

        existing = db.execute(
            select(PipelineEvent.id).where(PipelineEvent.log_excerpt_hash == dedupe_key)
        ).scalar_one_or_none()
        if existing:
            continue

        db_event = PipelineEvent(
            source_system=ev.source_system,
            tenant_id=ev.tenant_id,
            repository_id=ev.repository_id,
            pipeline_id=ev.pipeline_id,
            run_id=ev.run_id,
            job_id=ev.job_id or "",
            stage_name=ev.stage_name,
            event_type=ev.event_type,
            event_ts_utc=ev.event_ts_utc,
            duration_ms=ev.duration_ms,
            status=ev.status,
            branch=ev.branch,
            commit_sha=ev.commit_sha,
            actor=ev.actor,
            environment=ev.environment,
            retry_count=ev.retry_count,
            failure_signature=ev.failure_signature or "",
            log_excerpt_hash=dedupe_key,
            metadata_version=ev.metadata_version,
            metadata_json=ev.metadata,
        )
        db.add(db_event)

        run_map[ev.run_id]["duration"] += ev.duration_ms
        run_map[ev.run_id]["count"] += 1
        run_map[ev.run_id]["source_system"] = ev.source_system
        run_map[ev.run_id]["repository_id"] = ev.repository_id
        run_map[ev.run_id]["pipeline_id"] = ev.pipeline_id
        run_map[ev.run_id]["branch"] = ev.branch
        run_map[ev.run_id]["commit_sha"] = ev.commit_sha
        run_map[ev.run_id]["status"] = ev.status

    run_ids = list(run_map.keys())
    for run_id in run_ids:
        data = run_map[run_id]
        existing = db.get(PipelineRun, run_id)
        if existing:
            existing.total_duration_ms = data["duration"]
            existing.event_count = data["count"]
            existing.status = data["status"]
        else:
            db.add(
                PipelineRun(
                    run_id=run_id,
                    source_system=data["source_system"],
                    repository_id=data["repository_id"],
                    pipeline_id=data["pipeline_id"],
                    branch=data["branch"],
                    commit_sha=data["commit_sha"],
                    status=data["status"],
                    total_duration_ms=data["duration"],
                    event_count=data["count"],
                )
            )

    db.commit()
    return run_ids
