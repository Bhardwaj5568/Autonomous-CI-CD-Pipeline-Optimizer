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

## Workflow-Driven Input

Production input should come from GitHub Actions only.

How the tool receives input:
1. A GitHub Actions workflow runs on `test-integration` or `main`.
2. The workflow builds a JSON payload from GitHub runtime context.
3. The workflow POSTs that payload to `POST /webhooks/github-actions`.
4. The API normalizes, stores, scores, and exposes the result through `/status/checks`, `/status-ui`, `/runs`, and `/assessments`.

Where to adjust input for testing:
- Edit `.github/workflows/optimizer_webhook.yml` to change the payload values sent by the workflow.
- Change fields like `repository`, `ref`, `sha`, `run_id`, or `jobs` in the workflow payload to observe different outputs.

If you need to change inputs for testing:
- Edit the payload fields directly in `.github/workflows/optimizer_webhook.yml`

## Full QA Check

Run the full project smoke test from PowerShell:

```powershell
.\qa_smoke.ps1 -StartServer
```

## Workflow Verification

Use GitHub Actions as the only production input path.

1. Trigger `.github/workflows/optimizer_webhook.yml` from the `test-integration` branch.
2. Confirm the workflow log shows `Health status: 200`.
3. Confirm the workflow log shows `Webhook HTTP status: 200`.
4. Open `/status/checks` and `/status-ui` on the public tunnel URL to confirm live PASS/FAIL status.

If you need to change the observed output, edit the payload fields inside `.github/workflows/optimizer_webhook.yml` and rerun the workflow.

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

GitHub Actions workflow payloads are the production input path.
