# Project Name: Autonomous CI/CD Pipeline Optimizer

## Demo Run And Verification Steps

Use this file to verify the project end-to-end with demo inputs. Every step below includes the exact command to run.

## Step 1: Open PowerShell And Go To The Project Folder
Run:

```powershell
Set-Location "f:\CI-Cd\autonomous-cicd-optimizer"
```

Expected:
- Current folder becomes `f:\CI-Cd\autonomous-cicd-optimizer`

## Fast Workflow-Only Verification
Use GitHub Actions as the only production input source.

1. Trigger `.github/workflows/optimizer_webhook.yml` from the `main` branch.
2. Check workflow logs for `Health status: 200`.
3. Check workflow logs for `Webhook HTTP status: 200`.
4. Open the public tunnel URL `/status-ui` to confirm live PASS/FAIL state.
5. Stop the local server if you started it for the tunnel.

## Step 2: Install Dependencies
Run:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -m pip install -r requirements.txt
```

Expected:
- Dependencies install without errors

## Step 3: Start The API Server
Run this in a separate terminal and keep it open:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe -m uvicorn --app-dir f:/CI-Cd/autonomous-cicd-optimizer app.main:app --host 127.0.0.1 --port 8000
```

Expected:
- Server starts on `http://127.0.0.1:8000`
- Terminal stays running

## Step 4: Check Health Endpoint
Run:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing | Select-Object StatusCode,Content
```

Expected:
- `StatusCode = 200`
- Content contains `"status":"ok"`

## Step 5: Run Full QA Smoke Test
This is the fastest full-system check.

Run:

```powershell
.\qa_smoke.ps1
```

If the server is not already running, run:

```powershell
.\qa_smoke.ps1 -StartServer
```

Expected:
- Unit tests pass
- Health check passes
- Ingest passes
- Risk score is generated
- Feedback is accepted
- KPI endpoint returns data
- RBAC rejection works
- Webhook queue is processed
- Overall result is `PASS`

## Step 6: Open Live Red/Blue Status UI (Real Checks)
Open in browser:

```text
http://127.0.0.1:8000/status-ui
```

Expected:
- Blue dot means PASS for that check
- Red dot means FAIL for that check
- Status is based on live backend checks, not static text
- Page auto-refreshes every 5 seconds

Optional JSON output for same checks:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/status/checks"
```

## Step 7: Final Success Criteria
The system is working properly if all of these are true:
1. `qa_smoke.ps1` returns `PASS`
2. `/health` returns 200
3. GitHub workflow run reaches the webhook endpoint
4. Risk score is generated from workflow-driven input
5. Status UI shows live PASS/FAIL state
6. Unauthorized calls are rejected

## Step 8: Notes
1. Keep the API terminal running while testing.
2. If the browser says `ERR_CONNECTION_REFUSED`, the server is not running.
3. Always include `X-Role` in requests when testing secured endpoints.
