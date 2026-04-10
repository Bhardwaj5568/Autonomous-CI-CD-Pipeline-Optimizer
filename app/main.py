import asyncio
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException, Header
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import require_role
from app.connectors.factory import get_mapper
from app.config import settings
from app.database import Base, engine, get_db
from app.models import AuditLog, PipelineRun, RecommendationFeedback, RiskAssessment
from app.queue_worker import enqueue_source_payload, queue_stats, queue_worker_loop
from app.schemas import (
    AuditLogResponse,
    FeedbackRequest,
    FeedbackResponse,
    IngestResponse,
    KPIResponse,
    NormalizedEvent,
    PipelineRunResponse,
    QueueStatusResponse,
    RiskAssessmentResponse,
    SourceEventRequest,
)
from app.services.audit import write_audit_log
from app.services.ingestion import ingest_events
from app.services.metrics import compute_kpis
from app.services.scoring import score_and_persist_run

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Autonomous CI/CD Pipeline Optimizer API", version="0.1.0")


def _compute_live_checks(db: Session) -> list[dict]:
    worker_task = getattr(app.state, "worker_task", None)
    worker_running = bool(worker_task and not worker_task.done())

    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False

    total_runs = db.execute(select(func.count()).select_from(PipelineRun)).scalar_one()
    total_assessments = db.execute(select(func.count()).select_from(RiskAssessment)).scalar_one()

    checks = [
        {
            "name": "API process active",
            "passed": True,
            "detail": "Request reached application successfully.",
        },
        {
            "name": "Background worker running",
            "passed": worker_running,
            "detail": "Queue worker task is active." if worker_running else "Queue worker is not active.",
        },
        {
            "name": "Database connection",
            "passed": db_ok,
            "detail": "Database responded to SELECT 1." if db_ok else "Database health query failed.",
        },
        {
            "name": "Pipeline runs available",
            "passed": total_runs > 0,
            "detail": f"Found {total_runs} run(s).",
        },
        {
            "name": "Risk assessments available",
            "passed": total_assessments > 0,
            "detail": f"Found {total_assessments} assessment(s).",
        },
        {
            "name": "Queue error free",
            "passed": queue_stats.get("failed", 0) == 0,
            "detail": f"Queue failed count: {queue_stats.get('failed', 0)}.",
        },
    ]

    return checks


@app.on_event("startup")
async def startup_event() -> None:
    app.state.worker_task = asyncio.create_task(queue_worker_loop())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    worker_task = getattr(app.state, "worker_task", None)
    if worker_task:
        worker_task.cancel()


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "project": settings.app_name}


@app.get("/status/checks")
def status_checks(db: Session = Depends(get_db)) -> dict:
        checks = _compute_live_checks(db)
        all_passed = all(item["passed"] for item in checks)
        return {
                "project": settings.app_name,
                "all_passed": all_passed,
                "checks": checks,
                "queue": queue_stats,
        }


@app.get("/status-ui", response_class=HTMLResponse)
def status_ui(db: Session = Depends(get_db)) -> str:
        checks = _compute_live_checks(db)
    all_passed = all(item["passed"] for item in checks)
    overall_class = "overall-pass" if all_passed else "overall-fail"
    overall_label = "OVERALL PASS" if all_passed else "OVERALL FAIL"
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        rows = []
        for item in checks:
                dot_class = "dot-pass" if item["passed"] else "dot-fail"
                label = "PASS" if item["passed"] else "FAIL"
                rows.append(
                        f"<tr><td><span class='dot {dot_class}'></span> {label}</td><td>{item['name']}</td><td>{item['detail']}</td></tr>"
                )

        table_rows = "".join(rows)
        html = f"""
<!doctype html>
<html>
<head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>{settings.app_name} - Live Status</title>
    <style>
        body {{ font-family: Segoe UI, Tahoma, sans-serif; margin: 24px; background: #f5f7fb; color: #1f2a37; }}
        .card {{ background: #ffffff; border: 1px solid #d7dee9; border-radius: 12px; padding: 16px; max-width: 980px; }}
        h1 {{ margin: 0 0 8px 0; font-size: 24px; }}
        .sub {{ margin-bottom: 14px; color: #50617a; }}
        .overall {{ margin: 12px 0 16px; padding: 12px; border-radius: 10px; font-weight: 700; font-size: 20px; text-align: center; }}
        .overall-pass {{ background: #dbeafe; color: #1d4ed8; border: 1px solid #93c5fd; }}
        .overall-fail {{ background: #fee2e2; color: #b91c1c; border: 1px solid #fca5a5; }}
        table {{ width: 100%; border-collapse: collapse; }}
        th, td {{ border-bottom: 1px solid #e6ecf3; padding: 10px; text-align: left; font-size: 14px; }}
        .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }}
        .dot-pass {{ background: #2563eb; }}
        .dot-fail {{ background: #dc2626; }}
        .meta {{ margin-top: 12px; color: #6b7c93; font-size: 13px; }}
    </style>
</head>
<body>
    <div class='card'>
        <h1>{settings.app_name}</h1>
        <div class='sub'>Live verification status (Blue = PASS, Red = FAIL). Auto-refresh every 5 seconds.</div>
        <div class='overall {overall_class}'>{overall_label}</div>
        <table>
            <thead>
                <tr><th>Status</th><th>Check</th><th>Detail</th></tr>
            </thead>
            <tbody>
                {table_rows}
            </tbody>
        </table>
        <div class='meta'>Queue stats: queued={queue_stats.get('queued', 0)}, processed={queue_stats.get('processed', 0)}, failed={queue_stats.get('failed', 0)} | Last updated: {now_utc}</div>
    </div>
    <script>
        setTimeout(function() {{ window.location.reload(); }}, 5000);
    </script>
</body>
</html>
"""
    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.post("/ingest/events", response_model=IngestResponse)
def ingest_normalized_events(
    payload: list[NormalizedEvent],
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    run_ids = ingest_events(db, payload)
    for run_id in run_ids:
        score_and_persist_run(db, run_id)
    write_audit_log(db, "ingest_normalized_events", "api-user", {"run_ids": run_ids, "count": len(payload)})
    db.commit()
    return IngestResponse(ingested_count=len(payload), run_ids=run_ids)


@app.post("/ingest/source-event", response_model=IngestResponse)
def ingest_source_event(
    request: SourceEventRequest,
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    mapper = get_mapper(request.source_system)
    normalized = mapper.map_to_normalized_events(request.payload)
    run_ids = ingest_events(db, normalized)
    for run_id in run_ids:
        score_and_persist_run(db, run_id)
    write_audit_log(db, "ingest_source_event", "api-user", {"source": request.source_system, "run_ids": run_ids})
    db.commit()
    return IngestResponse(ingested_count=len(normalized), run_ids=run_ids)


@app.post("/score/run/{run_id}", response_model=RiskAssessmentResponse)
def score_run(
    run_id: str,
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    assessment = score_and_persist_run(db, run_id)
    if not assessment:
        raise HTTPException(status_code=404, detail="Run not found")
    write_audit_log(db, "score_run", "api-user", {"run_id": run_id, "risk_score": assessment.risk_score})
    db.commit()
    return assessment


@app.get("/runs", response_model=list[PipelineRunResponse])
def list_runs(_: dict = Depends(require_role({"viewer", "operator", "admin"})), db: Session = Depends(get_db)):
    runs = db.execute(select(PipelineRun).order_by(PipelineRun.created_at.desc())).scalars().all()
    return [
        PipelineRunResponse(
            run_id=r.run_id,
            source_system=r.source_system,
            repository_id=r.repository_id,
            pipeline_id=r.pipeline_id,
            branch=r.branch,
            commit_sha=r.commit_sha,
            status=r.status,
            total_duration_ms=r.total_duration_ms,
            event_count=r.event_count,
        )
        for r in runs
    ]


@app.get("/assessments", response_model=list[RiskAssessmentResponse])
def list_assessments(_: dict = Depends(require_role({"viewer", "operator", "admin"})), db: Session = Depends(get_db)):
    items = db.execute(select(RiskAssessment).order_by(RiskAssessment.created_at.desc())).scalars().all()
    return [
        RiskAssessmentResponse(
            run_id=i.run_id,
            risk_score=i.risk_score,
            recommendation=i.recommendation,
            confidence=i.confidence,
            reasons=i.reasons,
        )
        for i in items
    ]


@app.post("/webhooks/github-actions")
async def github_actions_webhook(
    payload: dict,
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    await enqueue_source_payload("github_actions", payload)
    return {"queued": True, "source": "github_actions"}


@app.post("/webhooks/gitlab-ci")
async def gitlab_ci_webhook(
    payload: dict,
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    await enqueue_source_payload("gitlab_ci", payload)
    return {"queued": True, "source": "gitlab_ci"}


@app.post("/webhooks/jenkins")
async def jenkins_webhook(
    payload: dict,
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    await enqueue_source_payload("jenkins", payload)
    return {"queued": True, "source": "jenkins"}


@app.get("/queue/status", response_model=QueueStatusResponse)
def get_queue_status(_: dict = Depends(require_role({"viewer", "operator", "admin"}))) -> QueueStatusResponse:
    return QueueStatusResponse(**queue_stats)


@app.post("/feedback/run/{run_id}", response_model=FeedbackResponse)
def submit_feedback(
    run_id: str,
    feedback: FeedbackRequest,
    x_user: str | None = Header(default="anonymous"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    db.add(
        RecommendationFeedback(
            run_id=run_id,
            vote=feedback.vote,
            comment=feedback.comment,
            actor=x_user or "anonymous",
        )
    )
    write_audit_log(db, "feedback_submitted", x_user or "anonymous", {"run_id": run_id, "vote": feedback.vote})
    db.commit()
    return FeedbackResponse(run_id=run_id, vote=feedback.vote, actor=x_user or "anonymous")


@app.get("/kpis", response_model=KPIResponse)
def get_kpis(_: dict = Depends(require_role({"viewer", "operator", "admin"})), db: Session = Depends(get_db)):
    return KPIResponse(**compute_kpis(db))


@app.get("/audit-logs", response_model=list[AuditLogResponse])
def list_audit_logs(_: dict = Depends(require_role({"admin"})), db: Session = Depends(get_db)):
    logs = db.execute(select(AuditLog).order_by(AuditLog.created_at.desc())).scalars().all()
    return [AuditLogResponse(action_type=l.action_type, actor=l.actor, details=l.details) for l in logs]
