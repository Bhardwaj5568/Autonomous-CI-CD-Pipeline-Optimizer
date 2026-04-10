# Autonomous CI/CD Pipeline Optimizer

A runnable MVP backend scaffold with:
- Canonical CI/CD event ingestion
- Source mappers for GitHub Actions, GitLab CI, and Jenkins
- Rule-based release risk scoring (Deploy/Canary/Delay/Block)
- SQLite persistence for local development
- Async webhook queue processing
- RBAC-style role checks and optional API key authentication
- Audit logs, feedback loop, and KPI endpoints

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
.
qa_smoke.ps1 -StartServer
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
