import asyncio
from datetime import datetime, timezone

from app.connectors.factory import get_mapper
from app.database import SessionLocal
from app.services.audit import write_audit_log
from app.services.ingestion import ingest_events
from app.services.scoring import score_and_persist_run

queue: asyncio.Queue[dict] = asyncio.Queue()
queue_stats = {
    "queued": 0,
    "processed": 0,
    "failed": 0,
    "duplicate_deliveries": 0,
    "last_error": "",
}


async def enqueue_source_payload(source_system: str, payload: dict) -> None:
    await queue.put({"source_system": source_system, "payload": payload})
    queue_stats["queued"] += 1


async def queue_worker_loop() -> None:
    while True:
        item = await queue.get()
        db = SessionLocal()
        try:
            mapper = get_mapper(item["source_system"])
            normalized = mapper.map_to_normalized_events(item["payload"])
            run_ids = ingest_events(db, normalized)

            for run_id in run_ids:
                assessment = score_and_persist_run(db, run_id)
                if assessment:
                    write_audit_log(
                        db,
                        action_type="auto_scoring",
                        actor="queue-worker",
                        details={
                            "run_id": run_id,
                            "risk_score": assessment.risk_score,
                            "recommendation": assessment.recommendation,
                            "processed_at": datetime.now(timezone.utc).isoformat(),
                        },
                    )
            db.commit()
            queue_stats["processed"] += 1
        except Exception as exc:
            queue_stats["failed"] += 1
            queue_stats["last_error"] = str(exc)
        finally:
            db.close()
            queue.task_done()
