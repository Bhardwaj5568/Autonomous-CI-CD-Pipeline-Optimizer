# Autonomous CI/CD Pipeline Optimizer

A runnable MVP backend scaffold with:
- Canonical CI/CD event ingestion
- Source mappers for GitHub Actions, GitLab CI, and Jenkins
- Rule-based release risk scoring (Deploy/Canary/Delay/Block)
- SQLite persistence for local development
- Async webhook queue processing
- RBAC-style role checks and optional API key authentication
- Audit logs, feedback loop, and KPI endpoints

## Problem It Solves

This project solves common DevOps delivery problems:
- Slow and flaky CI/CD pipelines
- Risky releases that cause rollbacks and downtime
- Lack of clear go/no-go deployment signals
- Fragmented telemetry across GitHub Actions, GitLab CI, and Jenkins
- Low visibility into release quality, reliability, and operational risk

## Why It Matters

Teams can use this service to:
- Detect and score risky runs before deployment
- Standardize pipeline insights across multiple CI/CD tools
- Improve release confidence with auditable recommendations
- Track reliability trends with KPI and feedback loops

## Real-World Use Cases

- Release managers checking whether a run should Deploy, Canary, Delay, or Block
- Platform teams monitoring queue health and CI/CD risk trends
- DevOps teams validating webhook/event integrations from Jenkins/GitHub/GitLab
- Engineering leadership reviewing operational KPIs and audit history

## Architecture Overview (High-Level)

Data flow:
1. CI/CD source events are received from GitHub Actions, GitLab CI, and Jenkins.
2. Events are normalized into a canonical schema.
3. Webhook events are queued for async processing.
4. Scoring engine computes run risk and recommendation.
5. Results are exposed via APIs, status UI, KPI endpoints, and audit logs.

## Configuration (Non-Secret)

Use these environment variable names in your local `.env` (never commit real values):

| Variable | Purpose | Example (safe) |
|---|---|---|
| `APP_NAME` | Display name of service | `Autonomous CI/CD Pipeline Optimizer` |
| `DATABASE_URL` | Database connection string | `sqlite:///./optimizer.db` |
| `APP_API_KEY` | Optional API key for protected routes | `change-me` |
| `GITHUB_WEBHOOK_SECRET` | Optional HMAC secret for webhook signature verification | `change-me-github-secret` |
| `RISK_BLOCK_THRESHOLD` | Score threshold for `block` | `85` |
| `RISK_DELAY_THRESHOLD` | Score threshold for `delay` | `70` |
| `RISK_CANARY_THRESHOLD` | Score threshold for `canary` | `50` |

## Quick Start

1. Install dependencies:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -m pip install -r requirements.txt
```

2. Run API:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
```

Preferred (always correct app-dir):

```powershell
.\start_server.ps1
```

3. Open docs:
- http://127.0.0.1:8000/docs

Important startup note:
- If you start from wrong working directory, you may see `No module named app`.
- Prefer using `./start_server.ps1` or `--app-dir` command shown above.

## Main Endpoints
- `POST /ingest/events` ingest normalized events
- `POST /ingest/source-event` ingest source-specific payloads
- `POST /score/run/{run_id}` score a pipeline run
- `POST /webhooks/github-actions` queue GitHub payload
- `POST /webhooks/gitlab-ci` queue GitLab payload
- `POST /webhooks/jenkins` queue Jenkins payload
- `GET /queue/status` view async processor status
- `GET /runs` list runs
- `GET /assessments` list risk assessments
- `POST /feedback/run/{run_id}` submit thumbs up/down
- `GET /kpis` aggregate delivery KPIs
- `GET /audit-logs` admin-only audit trail

## Auth Headers

If `APP_API_KEY` is configured, pass:
- `X-API-Key: <your-key>`

Always pass role header:
- `X-Role: viewer | operator | admin`

Optional for feedback attribution:
- `X-User: your-name`

## GitHub Real Integration (Secrets Only)

Use repository secrets and deployment environment variables only. Never hardcode personal GitHub details, API keys, or tokens in code.

Required GitHub Actions secrets:
- `OPTIMIZER_BASE_URL`
- `OPTIMIZER_API_KEY`
- `OPTIMIZER_ROLE`
- `OPTIMIZER_WEBHOOK_PATH`
- `OPTIMIZER_WEBHOOK_SECRET` (required only when server has `GITHUB_WEBHOOK_SECRET` configured)

Hardened webhook behavior:
- Validates payload shape before queueing
- Verifies `X-Hub-Signature-256` when `GITHUB_WEBHOOK_SECRET` is configured
- Skips duplicate deliveries using `X-GitHub-Delivery` idempotency key

Ready workflow template:
- `.github/workflows/optimizer_webhook.yml`

Full setup guide:
- `docs/08_GitHub_Integration_Checklist.md`

Post-run verification script:
- `verify_real_github_integration.ps1`

## PASS / FAIL Interpretation

Use `http://127.0.0.1:8000/status-ui` for visual verification:
- Blue dot = PASS for that check
- Red dot = FAIL for that check
- `OVERALL PASS` means all runtime checks are healthy
- `OVERALL FAIL` means at least one runtime check is failing

Typical fail example:
- `Queue error free` turns FAIL when malformed webhook payloads are processed

## Sample Inputs
See `samples/` for source payload examples.

## Quick API Walkthrough

1. Ingest a GitHub Actions source event:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -c "import sys,json; sys.path.insert(0, r'f:/CI-Cd/autonomous-cicd-optimizer'); from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); payload=json.load(open(r'f:/CI-Cd/autonomous-cicd-optimizer/samples/source_event_github.json','r',encoding='utf-8')); h={'X-Role':'operator'}; print(c.post('/ingest/source-event', json=payload, headers=h).json())"
```

2. Score run `1024`:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, r'f:/CI-Cd/autonomous-cicd-optimizer'); from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); h={'X-Role':'operator'}; print(c.post('/score/run/1024', headers=h).json())"
```

3. Read KPIs:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -c "import sys; sys.path.insert(0, r'f:/CI-Cd/autonomous-cicd-optimizer'); from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app); h={'X-Role':'viewer'}; print(c.get('/kpis', headers=h).json())"
```

## Full QA Check

Run the full project smoke test from PowerShell:

```powershell
.\qa_smoke.ps1 -StartServer
```

## Quick Pass/Fail Demo Scripts

1. Start server:

```powershell
.\start_server.ps1
```

2. Run PASS demo:

```powershell
.\run_pass_demo.ps1
```

3. Run FAIL demo:

```powershell
.\run_fail_demo.ps1
```

4. Stop server:

```powershell
.\stop_server.ps1
```

5. Show PASS then FAIL in browser (with cache-busting URL):

```powershell
.\demo_browser_pass_fail.ps1
```

This checks:
- unit tests
- health endpoint
- ingest and scoring
- feedback and KPIs
- RBAC rejection
- async webhook queue processing

## Known Limitations (Current Build)

- Queue failure counter is in-memory and resets on server restart.
- Status UI is intended for operational checks, not long-term historical reporting.
- Local default database is SQLite; production should use managed relational storage.

## Safe Sharing Rules

Safe to keep in repo:
- architecture overview (high-level)
- env variable names only (no secret values)
- run commands and sample payload usage
- pass/fail interpretation and non-sensitive limitations

Do not commit:
- real API keys, tokens, passwords
- internal hostnames/IPs/VPN endpoints
- customer identifiers and private production logs
