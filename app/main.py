import asyncio
from collections import deque
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Header, Request
from fastapi.openapi.docs import get_swagger_ui_html, get_swagger_ui_oauth2_redirect_html
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

_MAX_GITHUB_DELIVERY_CACHE = 2000
_github_delivery_ids: set[str] = set()
_github_delivery_order: deque[str] = deque()


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
    if not _verify_github_signature(raw_body, signature_header):
        raise HTTPException(status_code=401, detail="Invalid GitHub webhook signature")

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
