# GitHub Real Integration Checklist (Secrets Safe)

Objective:
Connect a real GitHub repository to this service without exposing personal account details or secrets in source code.

## What Is Already Ready
1. Webhook endpoint exists: POST /webhooks/github-actions
2. Role-based header checks exist using X-Role
3. Optional API key protection exists using X-API-Key when APP_API_KEY is set
4. Payload guardrails reject malformed webhook payloads
5. Optional signature verification is active when GITHUB_WEBHOOK_SECRET is set
6. Duplicate GitHub deliveries are skipped by X-GitHub-Delivery id
7. Status verification endpoints exist: GET /status/checks and GET /status-ui

## Required Inputs From Operator
1. Public API base URL where this app is reachable
2. GitHub repository where Actions will run
3. Secret values created in GitHub repository settings

Do not put any secret in tracked files.

## Secret Names (Use Exactly These)
Configure in GitHub repository Settings > Secrets and variables > Actions:

1. OPTIMIZER_BASE_URL
- Example value: https://your-company-domain

2. OPTIMIZER_API_KEY
- Must match APP_API_KEY in server environment

3. OPTIMIZER_ROLE
- Recommended value: operator

4. OPTIMIZER_WEBHOOK_PATH
- Value: /webhooks/github-actions

5. OPTIMIZER_WEBHOOK_SECRET
- Needed only if server uses webhook signature verification

## Server Environment Variables
Set these on deployment target (not in repo):

1. APP_API_KEY
2. APP_NAME
3. DATABASE_URL
4. GITHUB_WEBHOOK_SECRET
5. RISK_BLOCK_THRESHOLD
6. RISK_DELAY_THRESHOLD
7. RISK_CANARY_THRESHOLD

## Setup Steps
1. Deploy API so endpoint is internet reachable over HTTPS.
2. Set APP_API_KEY on server with a strong random value.
3. Add the GitHub Action secrets listed above.
4. Add workflow file from .github/workflows/optimizer_webhook.yml.
5. Trigger a workflow run (push or manual dispatch).
6. Validate in service:
- GET /queue/status should show processed increasing
- GET /status/checks should return all_passed true in healthy flow
- GET /status-ui should show blue status dots for passing checks

If signature verification is enabled:
7. Ensure OPTIMIZER_WEBHOOK_SECRET matches server GITHUB_WEBHOOK_SECRET exactly.

## Validation Commands (No Secrets Printed)
Use browser or API client:

1. Open status UI:
- https://your-company-domain/status-ui

2. Queue status:
- https://your-company-domain/queue/status

## Security Rules
1. Never commit real token, key, or webhook secret.
2. Never commit internal hostnames, private IPs, or VPN URLs.
3. Use GitHub Secrets and deployment environment variables only.
4. Rotate APP_API_KEY and GITHUB_WEBHOOK_SECRET on schedule.

## Rollback Plan
1. Disable workflow in GitHub Actions.
2. Remove or rotate APP_API_KEY on server.
3. Re-run status checks to confirm queue stability.

## Minimum Acceptance Criteria
1. One real GitHub workflow run reaches webhook endpoint.
2. Queue processed count increases by at least 1.
3. No queue failure increment during valid payload run.
4. Status UI reflects healthy checks after successful ingestion.
5. Replayed delivery id does not enqueue duplicate payload.
