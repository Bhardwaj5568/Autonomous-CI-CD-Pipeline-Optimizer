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

## Fast Script Workflow (Recommended)
Use these commands for a no-confusion run:

```powershell
.\start_server.ps1
```

In a second terminal:

```powershell
.\run_pass_demo.ps1
```

To simulate fail:

```powershell
.\run_fail_demo.ps1
```

Combined browser demo (shows PASS then FAIL):

```powershell
.\demo_browser_pass_fail.ps1
```

To stop server:

```powershell
.\stop_server.ps1
```

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

## Step 6: Manual Demo Ingest And Score
Run:

```powershell
$headers = @{ "X-Role" = "operator" }
$payload = Get-Content "f:\CI-Cd\autonomous-cicd-optimizer\samples\source_event_github.json" -Raw | ConvertFrom-Json
$ingest = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/ingest/source-event" -Headers $headers -ContentType "application/json" -Body ($payload | ConvertTo-Json -Depth 20)
$ingest
$runId = $ingest.run_ids[0]
$score = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/score/run/$runId" -Headers $headers
$score
```

Expected:
- `ingested_count` is greater than 0
- `risk_score` is returned
- `recommendation` is one of `deploy`, `canary`, `delay`, `block`

## Step 7: Submit Feedback
Run:

```powershell
$viewer = @{ "X-Role" = "viewer"; "X-User" = "qa-user" }
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/feedback/run/$runId" -Headers $viewer -ContentType "application/json" -Body '{"vote":"up","comment":"demo passed"}'
```

Expected:
- Feedback is accepted

## Step 8: Check KPI Output
Run:

```powershell
Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8000/kpis" -Headers @{ "X-Role" = "viewer" }
```

Expected:
- KPI values are returned

## Step 9: Test Async Webhook Path
Run:

```powershell
f:/CI-Cd/.venv/Scripts/python.exe f:/CI-Cd/autonomous-cicd-optimizer/samples/smoke_webhook.py
```

Expected:
- `webhook 200`
- `processed_ok True`
- Queue status shows processed events

## Step 10: Test RBAC Rejection
Run:

```powershell
Invoke-WebRequest -Uri "http://127.0.0.1:8000/runs" -UseBasicParsing
```

Expected:
- Request fails with `403` because `X-Role` header is missing

## Step 11: Open Live Red/Blue Status UI (Real Checks)
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

## Step 12: Final Success Criteria
The system is working properly if all of these are true:
1. `qa_smoke.ps1` returns `PASS`
2. `/health` returns 200
3. Demo payload ingest works
4. Risk score is generated
5. Feedback is accepted
6. KPI data is returned
7. Webhook queue is processed
8. Unauthorized calls are rejected

## Step 13: Demo Inputs
Use these sample files:
- `samples/source_event_github.json`
- `samples/source_event_gitlab.json`
- `samples/source_event_jenkins.json`

## Step 14: Notes
1. Keep the API terminal running while testing.
2. If the browser says `ERR_CONNECTION_REFUSED`, the server is not running.
3. Always include `X-Role` in requests when testing secured endpoints.
