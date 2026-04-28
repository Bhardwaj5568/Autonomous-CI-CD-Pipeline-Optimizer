
import asyncio
from copy import deepcopy
from collections import deque
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Header, Query, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.responses import FileResponse
import tempfile
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.auth import require_role
from app.connectors.github_actions import GitHubActionsMapper
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

app = FastAPI(
    title="Autonomous CI/CD Pipeline Optimizer API",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    swagger_ui_parameters={
        "deepLinking": True,
        "displayRequestDuration": True,
        "docExpansion": "none",
        "filter": True,
        "persistAuthorization": True,
        "defaultModelsExpandDepth": -1,
        "syntaxHighlight.theme": "agate",
    },
)

# Build Time Trend plot endpoint
@app.get("/report/build-time-trend", response_class=FileResponse)
def build_time_trend(repo_id: str, pipeline_id: str, db: Session = Depends(get_db)):
    from app.services.reporting import plot_build_time_trend
    tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tf.close()
    save_path = tf.name
    result = plot_build_time_trend(db, repo_id, pipeline_id, save_path)
    if not result:
        raise HTTPException(status_code=404, detail="No pipeline runs found for this repo/pipeline.")
    return FileResponse(save_path, media_type="image/png")

_MAX_GITHUB_DELIVERY_CACHE = 2000
_github_delivery_ids: set[str] = set()
_github_delivery_order: deque[str] = deque()

_MAX_GITLAB_DELIVERY_CACHE = 2000
_gitlab_delivery_ids: set[str] = set()
_gitlab_delivery_order: deque[str] = deque()

_MAX_JENKINS_DELIVERY_CACHE = 2000
_jenkins_delivery_ids: set[str] = set()
_jenkins_delivery_order: deque[str] = deque()

_latest_github_actions_payload: dict[str, Any] | None = None


_GITHUB_ACTIONS_FALLBACK_PAYLOAD: dict[str, Any] = {
    "repository": "example-org/example-repo",
    "repository_id": "987654321",
    "repository_owner": "example-org",
    "repository_owner_id": "11111111",
    "repository_visibility": "public",
    "repository_url": "https://github.com/example-org/example-repo",
    "branch": "main",
    "ref": "refs/heads/main",
    "ref_name": "main",
    "ref_type": "branch",
    "default_branch": "main",
    "sha": "a1b2c3d4e5f6",
    "commit_sha": "a1b2c3d4e5f6",
    "run_id": "123456789",
    "run_number": "42",
    "run_attempt": "1",
    "workflow": "CI",
    "workflow_ref": "example-org/example-repo/.github/workflows/ci.yml@refs/heads/main",
    "workflow_sha": "a1b2c3d4e5f6",
    "actor": "octocat",
    "actor_id": "22222222",
    "triggering_actor": "octocat",
    "event_name": "push",
    "event_action": "",
    "server_url": "https://github.com",
    "api_url": "https://api.github.com",
    "graphql_url": "https://api.github.com/graphql",
    "delivery_id": "gh-bridge-123456789-1",
    "status": "completed",
    "conclusion": "success",
    "started_at": "2026-04-13T10:15:30Z",
    "completed_at": "2026-04-13T10:15:35Z",
    "commit": {
        "sha": "a1b2c3d4e5f6",
        "message": "Demo commit",
        "author": "octocat",
        "compare_url": "https://github.com/example-org/example-repo/compare/a1b2c3d4e5f6...HEAD",
    },
    "pull_request": {
        "number": None,
        "title": None,
        "base_ref": None,
        "head_ref": None,
    },
    "release": {
        "tag_name": None,
        "name": None,
        "prerelease": False,
        "published_at": None,
    },
    "workflow_run": {
        "id": 123456789,
        "workflow_id": 42,
        "run_attempt": 1,
        "head_branch": "main",
        "head_sha": "a1b2c3d4e5f6",
        "event": "push",
        "status": "completed",
        "conclusion": "success",
        "repository_id": "987654321",
        "created_at": "2026-04-13T10:15:30Z",
        "updated_at": "2026-04-13T10:15:35Z",
    },
    "jobs": [
        {
            "id": "send-to-optimizer",
            "name": "send-to-optimizer",
            "status": "completed",
            "conclusion": "success",
            "started_at": "2026-04-13T10:15:30Z",
            "completed_at": "2026-04-13T10:15:35Z",
            "run_attempt": 1,
            "duration_ms": 0,
            "runner": {"os": "ubuntu-latest"},
        }
    ],
}


def _fallback_github_actions_payload() -> dict[str, Any]:
    return deepcopy(_GITHUB_ACTIONS_FALLBACK_PAYLOAD)


def _current_github_actions_payload() -> dict[str, Any]:
    if _latest_github_actions_payload:
        print("[DEBUG] Using live GitHub Actions payload for Swagger example.")
        payload = deepcopy(_latest_github_actions_payload)
    else:
        print("[DEBUG] Using fallback/demo GitHub Actions payload for Swagger example.")
        payload = _fallback_github_actions_payload()

    workflow_run = payload.get("workflow_run") if isinstance(payload.get("workflow_run"), dict) else {}
    payload.setdefault("branch", payload.get("ref_name") or workflow_run.get("head_branch") or "main")
    payload.setdefault("ref_name", payload.get("branch") or workflow_run.get("head_branch") or "main")
    payload.setdefault("ref", payload.get("ref") or f"refs/heads/{payload.get('branch') or workflow_run.get('head_branch') or 'main'}")
    payload.setdefault("commit_sha", payload.get("sha") or workflow_run.get("head_sha") or "unknown")
    payload.setdefault("repository_id", payload.get("repository_id") or workflow_run.get("repository_id") or "unknown")
    payload.setdefault("run_id", payload.get("run_id") or str(workflow_run.get("id") or "unknown"))
    payload.setdefault("run_attempt", payload.get("run_attempt") or str(workflow_run.get("run_attempt") or "1"))
    return payload


def _current_ingest_events_example() -> list[dict[str, Any]]:
    payload = _current_github_actions_payload()
    normalized_events = GitHubActionsMapper().map_to_normalized_events(payload)
    return [event.model_dump(mode="json") for event in normalized_events]


def _github_source_event_example() -> dict[str, Any]:
    return {
        "source_system": "github_actions",
        "payload": _current_github_actions_payload(),
    }


def _update_openapi_examples(schema: dict[str, Any]) -> dict[str, Any]:
    try:
        paths = schema.get("paths", {})

        github_route = paths.get("/webhooks/github-actions", {})
        github_post = github_route.get("post", {})
        github_request = github_post.get("requestBody", {})
        github_content = github_request.get("content", {}).get("application/json", {})
        github_content["example"] = _current_github_actions_payload()
        github_content.setdefault("examples", {})["workflow-completed"] = {
            "summary": "Live GitHub workflow payload from the latest run",
            "value": _current_github_actions_payload(),
        }

        source_route = paths.get("/ingest/source-event", {})
        source_post = source_route.get("post", {})
        source_request = source_post.get("requestBody", {})
        source_content = source_request.get("content", {}).get("application/json", {})
        source_content["example"] = _github_source_event_example()
        source_content.setdefault("examples", {})["github-actions-live"] = {
            "summary": "Live GitHub source-event payload from the latest run",
            "value": _github_source_event_example(),
        }

        ingest_route = paths.get("/ingest/events", {})
        ingest_post = ingest_route.get("post", {})
        ingest_request = ingest_post.get("requestBody", {})
        ingest_content = ingest_request.get("content", {}).get("application/json", {})
        ingest_content["example"] = _current_ingest_events_example()
        ingest_content.setdefault("examples", {})["github-actions-live"] = {
            "summary": "Live normalized events derived from the latest GitHub payload",
            "value": _current_ingest_events_example(),
        }
    except Exception:
        return schema

    return schema


def custom_openapi() -> dict[str, Any]:
    if app.openapi_schema:
        return app.openapi_schema

    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    app.openapi_schema = _update_openapi_examples(schema)
    return app.openapi_schema


app.openapi = custom_openapi


def _is_duplicate_github_delivery(delivery_id: str | None) -> bool:
    """
    Detect and memoize GitHub webhook duplicates.
    
    GitHub guarantees at-least-once webhook delivery. If a delivery fails,
    GitHub retries and assigns a new X-GitHub-Delivery ID.
    However, network issues can cause duplicate deliveries with the same ID.
    
    This function maintains a bounded cache (FIFO) of recent delivery IDs
    to detect and reject duplicates.
    
    Args:
        delivery_id: X-GitHub-Delivery header value (unique per webhook attempt)
    
    Returns:
        True if this delivery_id has been seen before (duplicate)
        False if this is the first time seeing this delivery_id
    
    Side Effects:
        - Adds new delivery_id to cache
        - Evicts oldest ID if cache exceeds MAX_GITHUB_DELIVERY_CACHE (FIFO)
    """    # Missing delivery ID means we cannot deduplicate; treat as new
    if not delivery_id:
        return False

    # Check if we've already processed this delivery
    if delivery_id in _github_delivery_ids:
        return True  # Duplicate detected

    # Record this new delivery ID for future duplicate detection
    _github_delivery_ids.add(delivery_id)
    _github_delivery_order.append(delivery_id)

    # Enforce bounded cache: remove oldest entry if size exceeded
    # This prevents unbounded memory growth in long-running applications
    if len(_github_delivery_order) > _MAX_GITHUB_DELIVERY_CACHE:
        evicted = _github_delivery_order.popleft()
        _github_delivery_ids.discard(evicted)

    return False  # This is a new (non-duplicate) delivery


def _verify_github_signature(raw_body: bytes, signature_header: str | None) -> bool:
    """
    Verify GitHub webhook signature using HMAC-SHA256.
    
    GitHub includes an X-Hub-Signature-256 header with each webhook payload.
    This header contains a HMAC-SHA256 digest computed using the webhook secret.
    We recompute the digest and compare using timingconstant comparison.
    
    Args:
        raw_body: Raw request body bytes (must be raw, not parsed JSON)
        signature_header: X-Hub-Signature-256 header value (format: "sha256=<hex>")
    
    Returns:
        True if signature is valid or secret is not configured
        False if signature is invalid or malformed
    
    Security Note:
        Uses hmac.compare_digest() to prevent timing attacks
    """
    # If no webhook secret configured, skip signature validation
    if not settings.github_webhook_secret:
        return True

    # Validate header format: must be "sha256=<hex>"
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    # Extract the hexadecimal digest from header
    provided = signature_header.split("=", 1)[1]
    
    # Recompute the expected HMAC-SHA256 digest
    expected = hmac.new(
        key=settings.github_webhook_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    
    # Use timing-constant comparison to prevent timing attacks
    return hmac.compare_digest(provided, expected)


def _is_duplicate_jenkins_delivery(delivery_id: str | None) -> bool:
    if not delivery_id:
        return False

    if delivery_id in _jenkins_delivery_ids:
        return True

    _jenkins_delivery_ids.add(delivery_id)
    _jenkins_delivery_order.append(delivery_id)

    if len(_jenkins_delivery_order) > _MAX_JENKINS_DELIVERY_CACHE:
        evicted = _jenkins_delivery_order.popleft()
        _jenkins_delivery_ids.discard(evicted)

    return False


def _is_duplicate_gitlab_delivery(delivery_id: str | None) -> bool:
    if not delivery_id:
        return False

    if delivery_id in _gitlab_delivery_ids:
        return True

    _gitlab_delivery_ids.add(delivery_id)
    _gitlab_delivery_order.append(delivery_id)

    if len(_gitlab_delivery_order) > _MAX_GITLAB_DELIVERY_CACHE:
        evicted = _gitlab_delivery_order.popleft()
        _gitlab_delivery_ids.discard(evicted)

    return False


def _verify_gitlab_token(token_header: str | None) -> bool:
    if not settings.gitlab_webhook_secret:
        return True

    if token_header is None:
        return False

    return hmac.compare_digest(token_header, settings.gitlab_webhook_secret)


def _validate_gitlab_webhook_payload(payload: dict[str, Any]) -> None:
    pipeline_section = payload.get("pipeline")
    jobs = payload.get("jobs")
    run_id = payload.get("run_id")

    if pipeline_section is not None and not isinstance(pipeline_section, dict):
        raise HTTPException(status_code=422, detail="Invalid pipeline field: expected a JSON object when provided")

    if isinstance(pipeline_section, dict):
        run_id = pipeline_section.get("id") or run_id

    if run_id is None:
        raise HTTPException(status_code=422, detail="Missing run identifier: provide pipeline.id or run_id")

    repository_id = payload.get("project_id") or payload.get("repository_id")
    if repository_id is None:
        raise HTTPException(status_code=422, detail="Missing repository identifier: provide project_id or repository_id")

    if jobs is not None:
        if not isinstance(jobs, list):
            raise HTTPException(status_code=422, detail="Invalid jobs field: expected a list when provided")
        if any(not isinstance(job, dict) for job in jobs):
            raise HTTPException(status_code=422, detail="Invalid jobs field: each job must be a JSON object")


def _verify_jenkins_signature(raw_body: bytes, signature_header: str | None) -> bool:
    if not settings.jenkins_webhook_secret:
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        return False

    provided = signature_header.split("=", 1)[1]
    expected = hmac.new(
        key=settings.jenkins_webhook_secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(provided, expected)


def _validate_jenkins_webhook_payload(payload: dict[str, Any]) -> None:
    build_section = payload.get("build")
    stages = payload.get("stages")
    run_id = payload.get("run_id")

    if build_section is not None and not isinstance(build_section, dict):
        raise HTTPException(status_code=422, detail="Invalid build field: expected a JSON object when provided")

    if isinstance(build_section, dict):
        run_id = build_section.get("number") or run_id

    if run_id is None:
        raise HTTPException(status_code=422, detail="Missing run identifier: provide build.number or run_id")

    if stages is not None:
        if not isinstance(stages, list):
            raise HTTPException(status_code=422, detail="Invalid stages field: expected a list when provided")
        if any(not isinstance(stage, dict) for stage in stages):
            raise HTTPException(status_code=422, detail="Invalid stages field: each stage must be a JSON object")


def _validate_github_webhook_payload(payload: dict[str, Any]) -> None:
    run_section = payload.get("workflow_run")
    run_id = payload.get("run_id")
    if isinstance(run_section, dict):
        run_id = run_section.get("id") or run_id

    if run_id is None:
        raise HTTPException(status_code=422, detail="Missing run identifier: provide workflow_run.id or run_id")

    repository = payload.get("repository")
    has_repository = False
    if isinstance(repository, str) and repository.strip():
        has_repository = True
    if isinstance(repository, dict) and (repository.get("full_name") or repository.get("id")):
        has_repository = True
    if payload.get("repository_id"):
        has_repository = True

    if not has_repository:
        raise HTTPException(status_code=422, detail="Missing repository identifier")

    jobs = payload.get("jobs")
    if jobs is not None and not isinstance(jobs, list):
        raise HTTPException(status_code=422, detail="Invalid jobs field: expected a list when provided")


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
def health(
    ts: int | None = Query(default=None, description="Optional cache-buster timestamp for docs/testing."),
) -> dict:
    return {"status": "ok", "project": settings.app_name}


@app.get("/status/checks")
def status_checks(
    ts: int | None = Query(default=None, description="Optional cache-buster timestamp for docs/testing."),
    db: Session = Depends(get_db),
) -> dict:
    checks = _compute_live_checks(db)
    all_passed = all(item["passed"] for item in checks)
    return {
        "project": settings.app_name,
        "all_passed": all_passed,
        "checks": checks,
        "queue": queue_stats,
    }


@app.get("/status-ui", response_class=HTMLResponse)
def status_ui(
    ts: int | None = Query(default=None, description="Optional cache-buster timestamp for docs/testing."),
    db: Session = Depends(get_db),
) -> str:
    checks = _compute_live_checks(db)
    all_passed = all(item["passed"] for item in checks)
    overall_class = "overall-pass" if all_passed else "overall-fail"
    overall_label = "OVERALL PASS" if all_passed else "OVERALL FAIL"
    now_ist = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S IST")
    passed_count = sum(1 for item in checks if item["passed"])
    failed_count = len(checks) - passed_count
    rows = []
    for item in checks:
        dot_class = "dot-pass" if item["passed"] else "dot-fail"
        label = "PASS" if item["passed"] else "FAIL"
        rows.append(
            f"<tr><td><span class='dot {dot_class}'></span><span class='table-status {dot_class}'>{label}</span></td><td>{item['name']}</td><td>{item['detail']}</td></tr>"
        )

    table_rows = "".join(rows)
    html = f"""
<!doctype html>
<html>
<head>
    <meta charset='utf-8'>
    <meta name='viewport' content='width=device-width, initial-scale=1'>
    <meta http-equiv='refresh' content='5'>
    <title>{settings.app_name} | Operations Status</title>
    <style>
        :root {{
            --bg: #eef2f7;
            --panel: rgba(255, 255, 255, 0.9);
            --panel-border: rgba(148, 163, 184, 0.28);
            --text: #102033;
            --muted: #5c6b7f;
            --accent: #0f62fe;
            --success: #116149;
            --success-soft: #dcfce7;
            --danger: #b42318;
            --danger-soft: #fee4e2;
            --shadow: 0 22px 60px rgba(15, 23, 42, 0.12);
        }}

        * {{ box-sizing: border-box; }}

        body {{
            margin: 0;
            min-height: 100vh;
            font-family: Inter, "Segoe UI", Aptos, Arial, sans-serif;
            color: var(--text);
            background:
                radial-gradient(circle at top left, rgba(15, 98, 254, 0.14), transparent 28%),
                radial-gradient(circle at top right, rgba(17, 97, 73, 0.12), transparent 24%),
                linear-gradient(180deg, #f8fbff 0%, var(--bg) 100%);
            padding: 28px;
        }}

        .shell {{ max-width: 1180px; margin: 0 auto; }}

        .hero {{
            position: relative;
            overflow: hidden;
            border: 1px solid var(--panel-border);
            background: linear-gradient(135deg, rgba(255,255,255,0.92), rgba(244,248,255,0.88));
            backdrop-filter: blur(10px);
            border-radius: 24px;
            box-shadow: var(--shadow);
            padding: 28px;
        }}

        .hero::after {{
            content: "";
            position: absolute;
            inset: auto -80px -80px auto;
            width: 240px;
            height: 240px;
            border-radius: 999px;
            background: radial-gradient(circle, rgba(15, 98, 254, 0.18), transparent 68%);
            pointer-events: none;
        }}

        .eyebrow {{
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            border-radius: 999px;
            background: rgba(15, 98, 254, 0.08);
            color: var(--accent);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }}

        .hero-grid {{
            position: relative;
            display: grid;
            grid-template-columns: 1.7fr 1fr;
            gap: 20px;
            margin-top: 18px;
        }}

        .hero-main h1 {{ margin: 14px 0 10px; font-size: clamp(30px, 4vw, 44px); line-height: 1.08; }}
        .hero-main p {{ margin: 0; color: var(--muted); font-size: 15px; line-height: 1.6; max-width: 72ch; }}

        .status-banner {{
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 18px;
            border-radius: 20px;
            padding: 18px;
            border: 1px solid var(--panel-border);
            background: rgba(255, 255, 255, 0.7);
        }}

        .overall {{
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 92px;
            border-radius: 18px;
            font-weight: 800;
            font-size: 22px;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }}
        .overall-pass {{ background: var(--success-soft); color: var(--success); border: 1px solid rgba(17, 97, 73, 0.2); }}
        .overall-fail {{ background: var(--danger-soft); color: var(--danger); border: 1px solid rgba(180, 35, 24, 0.2); }}

        .mini-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }}
        .mini-card {{
            border-radius: 16px;
            background: #fff;
            border: 1px solid var(--panel-border);
            padding: 14px;
            box-shadow: 0 10px 28px rgba(15, 23, 42, 0.05);
        }}
        .mini-label {{ display: block; color: var(--muted); font-size: 12px; margin-bottom: 6px; }}
        .mini-value {{ font-size: 24px; font-weight: 800; line-height: 1; }}

        .content {{ margin-top: 20px; display: grid; grid-template-columns: 1fr; gap: 18px; }}
        .panel {{
            border-radius: 24px;
            border: 1px solid var(--panel-border);
            background: var(--panel);
            box-shadow: var(--shadow);
            overflow: hidden;
        }}
        .panel-header {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            padding: 18px 22px;
            border-bottom: 1px solid rgba(148, 163, 184, 0.18);
        }}
        .panel-title {{ font-size: 18px; font-weight: 800; margin: 0; }}
        .panel-subtitle {{ margin: 4px 0 0; color: var(--muted); font-size: 13px; }}

        table {{ width: 100%; border-collapse: collapse; }}
        thead th {{
            background: rgba(15, 23, 42, 0.03);
            color: var(--muted);
            font-size: 12px;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            text-align: left;
            padding: 14px 22px;
        }}
        tbody td {{
            border-top: 1px solid rgba(148, 163, 184, 0.16);
            padding: 16px 22px;
            font-size: 14px;
            vertical-align: top;
        }}
        tbody tr:hover {{ background: rgba(15, 98, 254, 0.03); }}

        .dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 999px; margin-right: 10px; }}
        .dot-pass {{ background: #1d4ed8; box-shadow: 0 0 0 6px rgba(37, 99, 235, 0.08); }}
        .dot-fail {{ background: #dc2626; box-shadow: 0 0 0 6px rgba(220, 38, 38, 0.08); }}
        .table-status {{
            display: inline-flex;
            align-items: center;
            padding: 4px 10px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            min-width: 64px;
            justify-content: center;
        }}
        .table-status.dot-pass {{ background: rgba(37, 99, 235, 0.09); color: #1d4ed8; }}
        .table-status.dot-fail {{ background: rgba(220, 38, 38, 0.09); color: #dc2626; }}

        .footer {{
            display: flex;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            padding: 16px 22px 20px;
            color: var(--muted);
            font-size: 13px;
        }}

        .signature {{ font-weight: 600; color: #334155; }}

        @media (max-width: 900px) {{
            body {{ padding: 16px; }}
            .hero-grid {{ grid-template-columns: 1fr; }}
            .mini-grid {{ grid-template-columns: 1fr; }}
            .panel-header {{ align-items: flex-start; flex-direction: column; }}
        }}

        @media (max-width: 640px) {{
            .hero, .panel {{ border-radius: 20px; }}
            thead {{ display: none; }}
            tbody tr {{ display: block; padding: 6px 0; }}
            tbody td {{ display: block; padding: 12px 18px; border-top: none; }}
            tbody td:first-child {{ padding-top: 18px; }}
            tbody td:last-child {{ padding-bottom: 18px; }}
            tbody td:nth-child(2) {{ font-weight: 700; }}
        }}
    </style>
</head>
<body>
    <div class='shell'>
        <section class='hero'>
            <span class='eyebrow'>Operations dashboard</span>
            <div class='hero-grid'>
                <div class='hero-main'>
                    <h1>{settings.app_name}</h1>
                    <p>
                        Live runtime verification for webhook intake, queue health, scoring, and database readiness.
                        This page is designed for fast release checks and operational visibility.
                    </p>
                </div>
                <div class='status-banner'>
                    <div class='overall {overall_class}'>{overall_label}</div>
                    <div class='mini-grid'>
                        <div class='mini-card'>
                            <span class='mini-label'>Passed checks</span>
                            <div class='mini-value'>{passed_count}</div>
                        </div>
                        <div class='mini-card'>
                            <span class='mini-label'>Failed checks</span>
                            <div class='mini-value'>{failed_count}</div>
                        </div>
                        <div class='mini-card'>
                            <span class='mini-label'>Updated</span>
                            <div class='mini-value' style='font-size: 18px; line-height: 1.15;'>{now_ist}</div>
                        </div>
                    </div>
                </div>
            </div>
        </section>

        <section class='content'>
            <div class='panel'>
                <div class='panel-header'>
                    <div>
                        <h2 class='panel-title'>Live health checks</h2>
                        <p class='panel-subtitle'>Blue indicates PASS, red indicates FAIL. The page refreshes every 5 seconds.</p>
                    </div>
                    <div class='signature'>Queue stats: queued={queue_stats.get('queued', 0)} · processed={queue_stats.get('processed', 0)} · failed={queue_stats.get('failed', 0)}</div>
                </div>
                <table>
                    <thead>
                        <tr><th>Status</th><th>Check</th><th>Detail</th></tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>
                <div class='footer'>
                    <span>Last updated: {now_ist}</span>
                    <span>Source: live application runtime</span>
                </div>
            </div>
        </section>
    </div>
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


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui() -> HTMLResponse:
    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{settings.app_name} | API Reference",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js",
        swagger_css_url="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css",
        swagger_favicon_url="https://fastapi.tiangolo.com/img/favicon.png",
    ).body.decode("utf-8")

    html = html.replace(
        "</head>",
        """
<style>
    :root { color-scheme: light; }
    body {
        margin: 0;
        font-family: Inter, "Segoe UI", Aptos, Arial, sans-serif;
        background:
            radial-gradient(circle at top left, rgba(15, 98, 254, 0.08), transparent 28%),
            linear-gradient(180deg, #f8fbff 0%, #eef2f7 100%);
        color: #0f172a;
    }
    .swagger-shell { max-width: 1400px; margin: 0 auto; padding: 24px; }
    .swagger-hero {
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 20px;
        background: linear-gradient(135deg, rgba(255,255,255,0.96), rgba(244,248,255,0.94));
        box-shadow: 0 16px 44px rgba(15, 23, 42, 0.08);
        padding: 20px;
        margin-bottom: 18px;
    }
    .eyebrow {
        display: inline-flex;
        padding: 7px 12px;
        border-radius: 999px;
        background: rgba(37, 99, 235, 0.1);
        color: #1d4ed8;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.1em;
        text-transform: uppercase;
    }
    .swagger-hero h1 {
        margin: 12px 0 8px;
        font-size: clamp(26px, 3.2vw, 40px);
        line-height: 1.1;
        color: #0f172a;
    }
    .swagger-hero p {
        margin: 0;
        color: #475569;
        font-size: 15px;
        line-height: 1.55;
        max-width: 75ch;
    }
    .docs-panel {
        border: 1px solid rgba(148, 163, 184, 0.24);
        border-radius: 20px;
        background: linear-gradient(180deg, rgba(255,255,255,0.98), rgba(252,253,255,0.97));
        box-shadow: 0 16px 44px rgba(15, 23, 42, 0.08);
        overflow: hidden;
    }
    .docs-panel-header {
        display: flex;
        justify-content: space-between;
        gap: 12px;
        align-items: center;
        padding: 14px 18px;
        border-bottom: 1px solid rgba(148, 163, 184, 0.2);
        background: rgba(241, 245, 249, 0.75);
    }
    .docs-panel-title {
        margin: 0;
        font-size: 16px;
        font-weight: 700;
        color: #0f172a;
    }
    .docs-panel-sub {
        margin: 2px 0 0;
        color: #64748b;
        font-size: 12px;
    }
    .docs-panel-pill {
        display: inline-flex;
        align-items: center;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(37, 99, 235, 0.1);
        color: #1d4ed8;
        font-size: 12px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
    }
    .swagger-ui {
        border-radius: 0;
        overflow: hidden;
        box-shadow: none;
    }
    .swagger-ui .topbar { display: none; }
    .swagger-ui .info { display: none; }
    .swagger-ui .info .title { color: #0f172a; }
    .swagger-ui .info .title small { color: #64748b; }
    .swagger-ui .btn.execute {
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%);
        border-color: #2563eb;
        color: #ffffff;
    }
    .swagger-ui .btn.execute:hover {
        background: linear-gradient(135deg, #1d4ed8 0%, #1e40af 100%);
    }
    .swagger-ui .btn.authorize {
        border-color: rgba(37, 99, 235, 0.35);
        color: #1d4ed8;
    }
    @media (max-width: 900px) {
        .swagger-shell { padding: 14px; }
    }
</style>
<script>
    document.addEventListener('DOMContentLoaded', function () {
        const mount = document.getElementById('swagger-ui');
        if (!mount || mount.dataset.wrapped === '1') return;

        const shell = document.createElement('div');
        shell.className = 'swagger-shell';

        const hero = document.createElement('section');
        hero.className = 'swagger-hero';
        hero.innerHTML = '<span class="eyebrow">API reference</span>' +
            '<h1>Autonomous CI/CD Pipeline Optimizer API</h1>' +
            '<p>Use this documentation to test endpoints, validate webhook payloads, and inspect runtime responses with high readability.</p>';

        const panel = document.createElement('section');
        panel.className = 'docs-panel';
        panel.innerHTML =
            '<div class="docs-panel-header">' +
                '<div>' +
                    '<h2 class="docs-panel-title">Endpoint Explorer</h2>' +
                    '<p class="docs-panel-sub">All GET, POST and secured operations are listed below.</p>' +
                '</div>' +
                '<span class="docs-panel-pill">Live OpenAPI</span>' +
            '</div>';

        const parent = mount.parentNode;
        parent.insertBefore(shell, mount);
        shell.appendChild(hero);
        shell.appendChild(panel);
        panel.appendChild(mount);
        mount.dataset.wrapped = '1';
    });
</script>
</head>
""",
    )

    return HTMLResponse(content=html)


@app.get("/docs/oauth2-redirect", include_in_schema=False)
def swagger_ui_redirect() -> HTMLResponse:
    return get_swagger_ui_oauth2_redirect_html()


@app.post("/ingest/events", response_model=IngestResponse)
def ingest_normalized_events(
    payload: list[NormalizedEvent] = Body(
        ...,
        examples=[
            [
                {
                    "source_system": "github_actions",
                    "tenant_id": "team-a",
                    "repository_id": "repo-123",
                    "pipeline_id": "pipeline-456",
                    "run_id": "run-789",
                    "job_id": "job-001",
                    "stage_name": "build",
                    "event_type": "job.completed",
                    "event_ts_utc": "2026-04-13T10:15:30Z",
                    "duration_ms": 1450,
                    "status": "success",
                    "branch": "main",
                    "commit_sha": "a1b2c3d4e5f6",
                    "actor": "github-actions[bot]",
                    "environment": "production",
                    "retry_count": 0,
                    "failure_signature": None,
                    "log_excerpt_hash": "sha256:abc123",
                    "metadata_version": "v1",
                    "metadata": {"service": "api", "region": "us-east-1"},
                }
            ]
        ],
    ),
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
    request: SourceEventRequest = Body(
        ...,
        examples={
            "github-actions": {
                "summary": "GitHub Actions workflow event",
                "value": {
                    "source_system": "github_actions",
                    "payload": {
                        "workflow_run": {
                            "id": 123456789,
                            "name": "CI",
                            "head_branch": "main",
                            "head_sha": "a1b2c3d4e5f6",
                            "conclusion": "success",
                        },
                        "repository": {
                            "full_name": "example-org/example-repo",
                            "id": 987654321,
                        },
                    },
                },
            }
        },
    ),
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
    request: Request,
    payload: dict[str, Any] = Body(
        ...,
        examples={
            "workflow-completed": {
                "summary": "GitHub Actions workflow completion payload",
                "value": {
                    "repository": "example-org/example-repo",
                    "ref": "main",
                    "sha": "a1b2c3d4e5f6",
                    "run_id": "123456789",
                    "run_attempt": "1",
                    "workflow": "CI",
                    "actor": "octocat",
                    "event_name": "push",
                    "status": "completed",
                    "conclusion": "success",
                    "jobs": [
                        {
                            "name": "send-to-optimizer",
                            "status": "completed",
                            "conclusion": "success",
                            "started_at": "2026-04-13T10:15:30Z",
                            "completed_at": "2026-04-13T10:15:35Z",
                        }
                    ],
                },
            }
        },
    ),
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    """
    GitHub Actions Webhook Receiver
    
    This endpoint accepts workflow events from GitHub Actions and processes them asynchronously.
    
    Security Checks:
    1. HMAC-SHA256 signature validation (prevents spoofing)
    2. Role-based access control (operator/admin only)
    3. Duplicate delivery detection (prevents double-processing)
    4. JSON payload validation
    
    Returns:
        - {queued: true} on successful intake
        - {duplicate: true} if this delivery was already processed
    
    HTTP Status:
        - 200: Webhook received and queued successfully
        - 401: Invalid GitHub signature or invalid API key
        - 403: Missing role header or insufficient permissions
        - 400: Malformed JSON payload
        - 422: Missing or invalid required fields
    """
    # Step 1: Retrieve raw request body for signature verification
    # IMPORTANT: Must use raw body before JSON parsing for HMAC validation
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")
    
    # Step 2: Verify GitHub HMAC-SHA256 signature
    # This ensures the webhook originated from GitHub using our secret
    from app.config import settings
    # If no secret is set, skip signature validation (for local/dev only)
    if settings.github_webhook_secret:
        if not _verify_github_signature(raw_body, signature_header):
            raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")
    else:
        print("[DEBUG] Skipping GitHub signature validation (no secret set)")

    # Step 3: Parse and validate JSON payload
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    # Step 4: Ensure payload is a JSON object (not array or primitive)
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid payload: expected a JSON object")

    # Step 5: Validate required fields (run_id, repository)
    # This ensures we can map the event to a pipeline run
    _validate_github_webhook_payload(payload)

    # Keep the latest GitHub payload in memory so Swagger/OpenAPI can render a live example.
    global _latest_github_actions_payload
    _latest_github_actions_payload = deepcopy(payload)
    app.openapi_schema = None

    # Step 6: Detect duplicate deliveries
    # GitHub can resend the same delivery if our server doesn't respond quickly
    delivery_id = request.headers.get("X-GitHub-Delivery")
    if _is_duplicate_github_delivery(delivery_id):
        # This is a duplicate: log and return success without re-processing
        queue_stats["duplicate_deliveries"] = queue_stats.get("duplicate_deliveries", 0) + 1
        return {
            "queued": False,
            "duplicate": True,
            "delivery_id": delivery_id,
            "source": "github_actions",
        }

    # Step 7: Queue the payload for async processing
    # The queue_worker task will normalize and score this event
    await enqueue_source_payload("github_actions", payload)
    return {
        "queued": True,
        "duplicate": False,
        "delivery_id": delivery_id,
        "source": "github_actions",
    }


@app.post("/webhooks/gitlab-ci")
async def gitlab_ci_webhook(
    request: Request,
    payload: dict[str, Any] = Body(
        ...,
        examples={
            "pipeline-completed": {
                "summary": "GitLab CI pipeline completion payload",
                "value": {
                    "project_id": "12345",
                    "ref": "main",
                    "sha": "abc123def456",
                    "pipeline": {
                        "id": 9876,
                        "status": "success",
                        "duration": 12.5,
                    },
                    "jobs": [
                        {
                            "id": "job-1",
                            "name": "unit-test",
                            "stage": "test",
                            "status": "success",
                            "duration_ms": 3500,
                        }
                    ],
                },
            }
        },
    ),
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    token_header = request.headers.get("X-Gitlab-Token")
    if not _verify_gitlab_token(token_header):
        raise HTTPException(status_code=401, detail="Invalid GitLab webhook token")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid payload: expected a JSON object")

    _validate_gitlab_webhook_payload(payload)

    delivery_id = request.headers.get("X-Gitlab-Event-UUID")
    if _is_duplicate_gitlab_delivery(delivery_id):
        queue_stats["duplicate_deliveries"] = queue_stats.get("duplicate_deliveries", 0) + 1
        return {
            "queued": False,
            "duplicate": True,
            "delivery_id": delivery_id,
            "source": "gitlab_ci",
        }

    await enqueue_source_payload("gitlab_ci", payload)
    return {
        "queued": True,
        "duplicate": False,
        "delivery_id": delivery_id,
        "source": "gitlab_ci",
    }


@app.post("/webhooks/jenkins")
async def jenkins_webhook(
    request: Request,
    payload: dict[str, Any] = Body(
        ...,
        examples={
            "build-completed": {
                "summary": "Jenkins build completion payload",
                "value": {
                    "run_id": "jk-run-1",
                    "job_name": "example-job",
                    "branch": "main",
                    "commit_sha": "abc123",
                    "build": {
                        "number": 42,
                        "result": "SUCCESS",
                        "duration": 1000,
                    },
                    "stages": [
                        {
                            "id": "stage-1",
                            "name": "unit-tests",
                            "status": "SUCCESS",
                            "duration_ms": 500,
                        }
                    ],
                },
            }
        },
    ),
    _: dict = Depends(require_role({"operator", "admin"})),
) -> dict:
    """
    Jenkins Webhook Receiver

    Accepts Jenkins build or stage payloads, verifies optional HMAC signature,
    validates required fields, deduplicates repeated deliveries, and queues the
    event for normalization and scoring.
    """
    raw_body = await request.body()
    signature_header = request.headers.get("X-Jenkins-Signature")

    if not _verify_jenkins_signature(raw_body, signature_header):
        raise HTTPException(status_code=401, detail="Invalid Jenkins webhook signature")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Invalid payload: expected a JSON object")

    _validate_jenkins_webhook_payload(payload)

    delivery_id = request.headers.get("X-Jenkins-Delivery")
    if _is_duplicate_jenkins_delivery(delivery_id):
        queue_stats["duplicate_deliveries"] = queue_stats.get("duplicate_deliveries", 0) + 1
        return {
            "queued": False,
            "duplicate": True,
            "delivery_id": delivery_id,
            "source": "jenkins",
        }

    await enqueue_source_payload("jenkins", payload)
    return {
        "queued": True,
        "duplicate": False,
        "delivery_id": delivery_id,
        "source": "jenkins",
    }


@app.get("/queue/status", response_model=QueueStatusResponse)
def get_queue_status(_: dict = Depends(require_role({"viewer", "operator", "admin"}))) -> QueueStatusResponse:
    return QueueStatusResponse(**queue_stats)


@app.post("/feedback/run/{run_id}", response_model=FeedbackResponse)
def submit_feedback(
    run_id: str,
    feedback: FeedbackRequest = Body(
        ...,
        examples={
            "positive": {
                "summary": "Positive feedback",
                "value": {
                    "vote": "up",
                    "comment": "Build passed after retry.",
                },
            }
        },
    ),
    x_user: str | None = Header(default="anonymous"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    
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


# ---------------------------------------------------------------------------
# Optimization endpoints — expose the full pipeline optimizer
# ---------------------------------------------------------------------------

@app.post("/optimize/run/{run_id}")
def optimize_run(
    run_id: str,
    dry_run: bool = Query(default=True, description="If true, return suggestions only. If false, apply changes via CI/CD API."),
    repo_owner: str | None = Query(default=None, description="GitHub repo owner (required for GitHub apply)"),
    repo_name: str | None = Query(default=None, description="GitHub repo name (required for GitHub apply)"),
    workflow_path: str | None = Query(default=None, description="GitHub workflow file path e.g. .github/workflows/ci.yml"),
    gitlab_project_id: str | None = Query(default=None, description="GitLab project ID (required for GitLab apply)"),
    jenkins_job_name: str | None = Query(default=None, description="Jenkins job name (required for Jenkins apply)"),
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Run the full optimization cycle for a pipeline run:
    - Detects redundant steps (GitHub Actions, GitLab CI, Jenkins)
    - Detects slow steps using statistical outlier detection
    - Learns from historical build data stored in DB
    - Applies changes via CI/CD API (if dry_run=False and credentials configured)
    """
    from app.services.pipeline_optimizer import PipelineOptimizerEngine
    from app.models import PipelineEvent as PE

    rows = db.execute(select(PE).where(PE.run_id == run_id)).scalars().all()
    if not rows:
        raise HTTPException(status_code=404, detail=f"No events found for run_id '{run_id}'")

    events = [
        {
            "stage_name": r.stage_name,
            "status": r.status,
            "duration_ms": r.duration_ms,
            "retry_count": r.retry_count,
        }
        for r in rows
    ]

    source_system = rows[0].source_system
    repository_id = rows[0].repository_id
    pipeline_id = rows[0].pipeline_id

    # Wire clients from settings
    from app.connectors.github_actions_client import GitHubActionsClient
    from app.connectors.gitlab_ci_client import GitLabCIClient
    from app.connectors.jenkins_client import JenkinsClient

    github_client = None
    gitlab_client = None
    jenkins_client = None

    if settings.github_token:
        github_client = GitHubActionsClient(settings.github_token)
        github_client.owner = settings.github_owner
        github_client.repo = settings.github_repo

    if settings.gitlab_token and settings.gitlab_project_id:
        gitlab_client = GitLabCIClient(settings.gitlab_token)
        gitlab_client.project_id = settings.gitlab_project_id

    if settings.jenkins_url and settings.jenkins_user and settings.jenkins_api_token:
        jenkins_client = JenkinsClient(settings.jenkins_url, settings.jenkins_user, settings.jenkins_api_token)

    engine = PipelineOptimizerEngine(
        db=db,
        github_client=github_client,
        gitlab_client=gitlab_client,
        jenkins_client=jenkins_client,
    )

    result = engine.run(
        events=events,
        source_system=source_system,
        repository_id=repository_id,
        pipeline_id=pipeline_id,
        repo_owner=repo_owner or settings.github_owner,
        repo_name=repo_name or settings.github_repo,
        workflow_path=workflow_path,
        gitlab_project_id=gitlab_project_id or settings.gitlab_project_id,
        jenkins_job_name=jenkins_job_name,
        dry_run=dry_run,
    )

    write_audit_log(db, "optimize_run", "api", {"run_id": run_id, "dry_run": dry_run, "result_summary": {
        "redundant_count": len(result.get("redundant_steps", [])),
        "slow_count": len(result.get("slow_steps", [])),
        "changes_applied": result.get("changes_applied", []),
    }})
    db.commit()

    return result


@app.get("/optimize/insights")
def get_pipeline_insights(
    repository_id: str = Query(..., description="Repository ID to analyze"),
    pipeline_id: str = Query(..., description="Pipeline ID to analyze"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Get ML-learned insights for a pipeline:
    - Per-step health labels (healthy, flaky, redundant, degrading)
    - Auto-remove candidates with confidence scores
    - Parallelization candidates based on duration trends
    - Combined ML + feedback recommendations
    """
    from app.services.ml_optimizer import (
        learn_step_patterns,
        get_auto_remove_candidates,
        get_parallelize_candidates,
        get_feedback_reinforced_candidates,
    )
    from app.services.pipeline_optimizer import learn_from_history

    patterns = learn_step_patterns(db, repository_id)
    history = learn_from_history(db, repository_id, pipeline_id)
    auto_remove = get_auto_remove_candidates(db, repository_id)
    parallelize = get_parallelize_candidates(db, repository_id)
    reinforced = get_feedback_reinforced_candidates(db)

    return {
        "repository_id": repository_id,
        "pipeline_id": pipeline_id,
        "step_patterns": patterns,
        "history_summary": history,
        "auto_remove_candidates": auto_remove,
        "parallelize_candidates": parallelize,
        "feedback_reinforced": reinforced,
    }


@app.get("/optimize/explain/{step_name}")
def explain_step(
    step_name: str,
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Explain why a specific step is flagged for optimization."""
    from app.services.ml_optimizer import explain_step as _explain
    return {"step": step_name, "explanation": _explain(step_name, db)}


# ---------------------------------------------------------------------------
# Build time reduction proof endpoints
# ---------------------------------------------------------------------------

@app.get("/report/build-time-reduction", response_class=FileResponse)
def build_time_reduction_chart(
    repo_id: str = Query(..., description="Repository ID"),
    pipeline_id: str = Query(..., description="Pipeline ID"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Returns a PNG chart showing before/after build time comparison.
    Proves the 42% build time reduction with baseline vs optimized phases.
    """
    import tempfile
    from app.services.reporting import plot_build_time_reduction

    tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tf.close()
    result, stats = plot_build_time_reduction(db, repo_id, pipeline_id, tf.name)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=stats.get("error", "Could not generate chart"),
        )
    return FileResponse(tf.name, media_type="image/png")


@app.get("/report/multi-pipeline-comparison", response_class=FileResponse)
def multi_pipeline_comparison_chart(
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Returns a PNG chart comparing build time reduction across all pipelines.
    Shows which pipelines meet the 42% target.
    """
    import tempfile
    from app.services.reporting import plot_multi_pipeline_comparison

    tf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tf.close()
    result, stats = plot_multi_pipeline_comparison(db, tf.name)
    if not result:
        raise HTTPException(status_code=404, detail="No pipelines with sufficient data found")
    return FileResponse(tf.name, media_type="image/png")


@app.get("/report/optimization-summary")
def optimization_summary(
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    JSON proof of build time reduction across all pipelines.
    Shows overall reduction %, whether 42% target is met, and per-pipeline breakdown.
    Methodology: baseline = first 40% of runs, optimized = last 40% of runs.
    """
    from app.services.reporting import compute_optimization_summary
    return compute_optimization_summary(db)


# ---------------------------------------------------------------------------
# Flaky Test Quarantine endpoints
# ---------------------------------------------------------------------------

@app.get("/quarantine/report")
def quarantine_report(
    repository_id: str | None = Query(default=None, description="Filter by repository ID"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Get full quarantine status — active and resolved flaky steps."""
    from app.services.quarantine import get_quarantine_report
    return get_quarantine_report(db, repository_id)


@app.get("/quarantine/detect")
def detect_flaky(
    repository_id: str | None = Query(default=None),
    pipeline_id: str | None = Query(default=None),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Scan pipeline history and return flaky step candidates (does not quarantine yet)."""
    from app.services.quarantine import detect_flaky_steps
    flaky = detect_flaky_steps(db, repository_id, pipeline_id)
    return {"flaky_detected": len(flaky), "steps": flaky}


@app.post("/quarantine/apply")
def apply_quarantine(
    repository_id: str = Query(...),
    pipeline_id: str = Query(...),
    step_name: str = Query(...),
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Manually quarantine a specific step."""
    from app.services.quarantine import quarantine_step
    result = quarantine_step(
        db=db,
        source_system="manual",
        repository_id=repository_id,
        pipeline_id=pipeline_id,
        step_name=step_name,
        reason="Manually quarantined via API",
        fail_rate=0.0,
        confidence=1.0,
        quarantined_by="manual",
    )
    write_audit_log(db, "quarantine_applied", "api", result)
    db.commit()
    return result


@app.post("/quarantine/resolve")
def resolve_quarantine(
    repository_id: str = Query(...),
    pipeline_id: str = Query(...),
    step_name: str = Query(...),
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Mark a quarantined step as resolved (fixed)."""
    from app.services.quarantine import unquarantine_step
    result = unquarantine_step(db, repository_id, pipeline_id, step_name)
    write_audit_log(db, "quarantine_resolved", "api", result)
    db.commit()
    return result


@app.post("/quarantine/auto-scan")
def auto_quarantine_scan(
    _: dict = Depends(require_role({"operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Scan all pipelines for flaky steps and auto-quarantine high-confidence ones.
    This is also triggered automatically during scoring for risky runs.
    """
    from app.services.quarantine import auto_quarantine_all
    result = auto_quarantine_all(db)
    write_audit_log(db, "auto_quarantine_scan", "api", {
        "flaky_detected": result["flaky_detected"],
        "quarantined": result["quarantined"],
    })
    db.commit()
    return result


# ---------------------------------------------------------------------------
# ML Model endpoints
# ---------------------------------------------------------------------------

@app.post("/ml/train")
def train_ml_model(
    _: dict = Depends(require_role({"admin"})),
    db: Session = Depends(get_db),
):
    """
    Train the RandomForest ML model on current PipelineEvent history.
    Model is saved to disk and used for future predictions.
    Requires at least 10 distinct steps with 3+ runs each.
    """
    from app.services.ml_model import train_model
    result = train_model(db)
    if result.get("trained"):
        write_audit_log(db, "ml_model_trained", "api", {
            "version": result.get("model_version"),
            "accuracy": result.get("accuracy"),
            "samples": result.get("training_samples"),
        })
        db.commit()
    return result


@app.get("/ml/predict")
def ml_predict(
    repository_id: str | None = Query(default=None, description="Filter by repository"),
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """
    Use trained ML model to classify all pipeline steps.
    Returns label (healthy/flaky/redundant/degrading/trivial/unstable),
    confidence score, and whether ML agrees with rule-based classifier.
    Falls back to rule-based if model not trained yet.
    """
    from app.services.ml_model import predict_step_labels
    predictions = predict_step_labels(db, repository_id)
    return {
        "total_steps": len(predictions),
        "predictions": predictions,
        "summary": {
            label: sum(1 for p in predictions if p["label"] == label)
            for label in {"healthy", "flaky", "redundant", "degrading", "trivial", "unstable"}
        },
    }


@app.get("/ml/status")
def ml_model_status(
    _: dict = Depends(require_role({"viewer", "operator", "admin"})),
    db: Session = Depends(get_db),
):
    """Get current ML model status, accuracy, and feature importances."""
    from app.services.ml_model import get_model_status
    return get_model_status(db)
