# 🚀 Junior Developer Setup Guide
## Autonomous CI/CD Pipeline Optimizer — GitHub Actions Integration

> **Goal:** Apne GitHub repository ki pipeline ko is optimizer se connect karo.
> Optimizer automatically detect karega ki kaunse steps slow/redundant/flaky hain
> aur suggestions dega (ya directly fix karega).

---

## What You Need (Prerequisites)

- Python 3.10+ installed
- Git installed
- A GitHub repository with GitHub Actions workflows
- 5 minutes

---

## Step 1: Clone & Setup the Optimizer

```bash
# Clone the project
git clone <this-repo-url>
cd autonomous-cicd-optimizer

# Create virtual environment
python -m venv .venv

# Activate it
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Step 2: Configure Environment

Copy the example env file:

```bash
cp .env.example .env
```

Open `.env` and fill in:

```env
APP_NAME=Autonomous CI/CD Pipeline Optimizer
DATABASE_URL=sqlite:///./optimizer.db
APP_API_KEY=my-secret-key-123          # <-- choose any secret key
GITHUB_WEBHOOK_SECRET=my-webhook-secret # <-- choose any webhook secret
RISK_BLOCK_THRESHOLD=85
RISK_DELAY_THRESHOLD=70
RISK_CANARY_THRESHOLD=50
```

> **Important:** `APP_API_KEY` aur `GITHUB_WEBHOOK_SECRET` — yeh dono values
> aapko GitHub Secrets mein bhi add karni hain (Step 4 mein).

---

## Step 3: Start the Optimizer Server

```bash
# Windows
.\start_server.ps1

# OR directly
python -m uvicorn app.main:app --reload --port 8000
```

Server start hone ke baad check karo:
- **API Docs:** http://127.0.0.1:8000/docs
- **Status Dashboard:** http://127.0.0.1:8000/status-ui
- **Health:** http://127.0.0.1:8000/health

---

## Step 4: Make Optimizer Publicly Accessible

GitHub Actions ko aapke local server tak pahunchne ke liye ek public URL chahiye.

### Option A: Cloudflare Tunnel (Free, Recommended)

```bash
# Install cloudflared (one time)
# Download from: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/

# Start tunnel (server chal raha ho tab)
cloudflared tunnel --url http://127.0.0.1:8000
```

Output mein ek URL milega jaise:
```
https://abc-xyz-123.trycloudflare.com
```

Yeh aapka `OPTIMIZER_BASE_URL` hai.

### Option B: ngrok

```bash
ngrok http 8000
```

`Forwarding` line se HTTPS URL copy karo.

> ⚠️ **Note:** Tunnel URL har baar change hota hai. Jab bhi naya tunnel start karo,
> GitHub Secret update karo.

---

## Step 5: Add GitHub Secrets to Your Repository

Apne GitHub repository mein jao:
**Settings → Secrets and variables → Actions → New repository secret**

Add these 5 secrets:

| Secret Name | Value | Example |
|---|---|---|
| `OPTIMIZER_BASE_URL` | Tunnel URL (Step 4 se) | `https://abc-xyz.trycloudflare.com` |
| `OPTIMIZER_API_KEY` | Same as `.env` APP_API_KEY | `my-secret-key-123` |
| `OPTIMIZER_ROLE` | `operator` | `operator` |
| `OPTIMIZER_WEBHOOK_PATH` | `/webhooks/github-actions` | `/webhooks/github-actions` |
| `OPTIMIZER_WEBHOOK_SECRET` | Same as `.env` GITHUB_WEBHOOK_SECRET | `my-webhook-secret` |

---

## Step 6: Add Optimizer Workflow to Your Repository

Apne repository mein yeh file create karo:
`.github/workflows/optimizer_webhook.yml`

```yaml
name: Optimizer Webhook Bridge

on:
  workflow_dispatch:        # Manual trigger
  push:
    branches: [main]        # Auto-trigger on push to main
  pull_request:
    branches: [main]        # Auto-trigger on PR

permissions:
  contents: read

jobs:
  send-to-optimizer:
    runs-on: ubuntu-latest
    steps:
      - name: Send workflow event to optimizer
        env:
          OPTIMIZER_BASE_URL: ${{ secrets.OPTIMIZER_BASE_URL }}
          OPTIMIZER_API_KEY: ${{ secrets.OPTIMIZER_API_KEY }}
          OPTIMIZER_ROLE: ${{ secrets.OPTIMIZER_ROLE }}
          OPTIMIZER_WEBHOOK_PATH: ${{ secrets.OPTIMIZER_WEBHOOK_PATH }}
          OPTIMIZER_WEBHOOK_SECRET: ${{ secrets.OPTIMIZER_WEBHOOK_SECRET }}
        run: |
          set -euo pipefail

          # Validate secrets
          if [ -z "${OPTIMIZER_BASE_URL}" ] || [ -z "${OPTIMIZER_API_KEY}" ]; then
            echo "ERROR: Required secrets missing!"
            exit 1
          fi

          TARGET_URL="${OPTIMIZER_BASE_URL%/}${OPTIMIZER_WEBHOOK_PATH}"
          DELIVERY_ID="gh-bridge-${{ github.run_id }}-${{ github.run_attempt }}"

          # Build payload
          PAYLOAD=$(jq -nc \
            --arg repository "${{ github.repository }}" \
            --arg repository_id "${{ github.repository_id }}" \
            --arg ref_name "${{ github.ref_name }}" \
            --arg sha "${{ github.sha }}" \
            --arg run_id "${{ github.run_id }}" \
            --arg run_attempt "${{ github.run_attempt }}" \
            --arg actor "${{ github.actor }}" \
            --arg event_name "${{ github.event_name }}" \
            --arg delivery_id "${DELIVERY_ID}" \
            '{
              repository: $repository,
              repository_id: $repository_id,
              branch: $ref_name,
              ref_name: $ref_name,
              sha: $sha,
              commit_sha: $sha,
              run_id: $run_id,
              run_attempt: $run_attempt,
              actor: $actor,
              event_name: $event_name,
              delivery_id: $delivery_id,
              status: "completed",
              conclusion: "success",
              workflow_run: {
                id: ($run_id | tonumber),
                head_branch: $ref_name,
                head_sha: $sha,
                repository_id: $repository_id
              },
              jobs: [{
                id: "send-to-optimizer",
                name: "send-to-optimizer",
                status: "completed",
                duration_ms: 0
              }]
            }')

          # Send to optimizer
          HTTP_STATUS=$(curl --silent --show-error \
            --output /tmp/response.txt \
            --write-out "%{http_code}" \
            -X POST "$TARGET_URL" \
            -H "Content-Type: application/json" \
            -H "X-API-Key: ${OPTIMIZER_API_KEY}" \
            -H "X-Role: ${OPTIMIZER_ROLE}" \
            -H "X-GitHub-Delivery: ${DELIVERY_ID}" \
            --data "$PAYLOAD")

          echo "Optimizer response: HTTP $HTTP_STATUS"
          cat /tmp/response.txt

          if [ "$HTTP_STATUS" -lt 200 ] || [ "$HTTP_STATUS" -ge 300 ]; then
            exit 1
          fi
```

---

## Step 7: Trigger & Verify

1. **Push any commit** to your `main` branch
2. Go to **Actions tab** in your GitHub repo
3. Watch `Optimizer Webhook Bridge` workflow run
4. Check optimizer dashboard: http://127.0.0.1:8000/status-ui

---

## Step 8: See Optimization Results

After a few pipeline runs, check these endpoints:

### 🔍 What did optimizer learn?
```
GET http://127.0.0.1:8000/optimize/insights?repository_id=YOUR_REPO&pipeline_id=YOUR_PIPELINE
Header: X-Role: viewer
```

### 📊 Build time reduction proof
```
GET http://127.0.0.1:8000/report/optimization-summary
Header: X-Role: viewer
```

### 🤖 ML predictions for your steps
```
GET http://127.0.0.1:8000/ml/predict?repository_id=YOUR_REPO
Header: X-Role: viewer
```

### 🚨 Flaky test detection
```
GET http://127.0.0.1:8000/quarantine/detect?repository_id=YOUR_REPO
Header: X-Role: viewer
```

### 🔧 Run full optimization on a specific run
```
POST http://127.0.0.1:8000/optimize/run/{run_id}?dry_run=true
Header: X-Role: operator
```

---

## Step 9: Train ML Model (After 10+ runs)

Once you have enough pipeline data:

```bash
curl -X POST http://127.0.0.1:8000/ml/train \
  -H "X-Role: admin" \
  -H "X-API-Key: my-secret-key-123"
```

Response will show:
- Accuracy score
- Which features matter most
- Label distribution (healthy/flaky/redundant/degrading)

---

## Step 10: Enable Auto-Apply (Optional — Advanced)

To let optimizer **automatically fix** your pipelines (not just suggest):

Add to your `.env`:
```env
GITHUB_TOKEN=ghp_your_personal_access_token
GITHUB_OWNER=your-github-username
GITHUB_REPO=your-repo-name
```

Then call optimize with `dry_run=false`:
```bash
curl -X POST "http://127.0.0.1:8000/optimize/run/{run_id}?dry_run=false&workflow_path=.github/workflows/ci.yml" \
  -H "X-Role: admin" \
  -H "X-API-Key: my-secret-key-123"
```

> ⚠️ This will directly commit changes to your workflow YAML.
> Always review changes before enabling on production repos.

---

## Quick Reference — All Useful Endpoints

| What | Method | URL |
|---|---|---|
| Health check | GET | `/health` |
| Status dashboard | GET | `/status-ui` |
| API docs | GET | `/docs` |
| All pipeline runs | GET | `/runs` |
| Risk assessments | GET | `/assessments` |
| KPIs | GET | `/kpis` |
| Optimize a run | POST | `/optimize/run/{run_id}` |
| ML insights | GET | `/optimize/insights` |
| Explain a step | GET | `/optimize/explain/{step}` |
| Build time proof | GET | `/report/optimization-summary` |
| Before/after chart | GET | `/report/build-time-reduction` |
| Flaky detection | GET | `/quarantine/detect` |
| Quarantine report | GET | `/quarantine/report` |
| Train ML model | POST | `/ml/train` |
| ML predictions | GET | `/ml/predict` |
| Audit logs | GET | `/audit-logs` |

---

## Troubleshooting

### "DNS lookup failed" in GitHub Actions
→ Your tunnel is not running. Start cloudflared/ngrok again and update `OPTIMIZER_BASE_URL` secret.

### "401 Unauthorized"
→ `OPTIMIZER_API_KEY` secret doesn't match `APP_API_KEY` in your `.env`.

### "No events found for run"
→ Webhook was received but not processed yet. Check `/queue/status`.

### "Need at least 5 runs" in optimization summary
→ Trigger more pipeline runs. Optimizer needs history to learn patterns.

### Server shows "No module named app"
→ Run from the project root directory, not a subdirectory.

---

## How It Works (Simple Explanation)

```
Your GitHub Repo
      |
      | (push/PR)
      ↓
GitHub Actions Workflow
      |
      | (sends pipeline data via webhook)
      ↓
Optimizer API (your local server)
      |
      ├── Stores run data in SQLite DB
      ├── Scores risk (deploy/canary/delay/block)
      ├── Detects redundant/slow/flaky steps
      ├── ML model learns patterns over time
      └── Returns optimization suggestions
            |
            ↓
      You check /docs or /status-ui
      to see what to fix in your pipeline
```

---

## Need Help?

1. Check `/status-ui` — shows all system health checks
2. Check `/docs` — interactive API documentation
3. Check `/audit-logs` — see what optimizer did automatically
4. Check `/queue/status` — see if webhooks are being processed
