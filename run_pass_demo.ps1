$ErrorActionPreference = 'Stop'

$operatorHeaders = @{ 'X-Role' = 'operator' }
$viewerHeaders = @{ 'X-Role' = 'viewer' }

$payload = Get-Content 'f:\CI-Cd\autonomous-cicd-optimizer\samples\source_event_gitlab.json' -Raw | ConvertFrom-Json

$webhook = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/webhooks/gitlab-ci' -Headers $operatorHeaders -ContentType 'application/json' -Body ($payload.payload | ConvertTo-Json -Depth 20)
$status = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/status/checks' -Headers $viewerHeaders

Write-Host "Webhook response: $($webhook | ConvertTo-Json -Compress)"
Write-Host "all_passed: $($status.all_passed)"
$status.checks | ForEach-Object {
    Write-Host ("check=" + $_.name + " | passed=" + $_.passed + " | detail=" + $_.detail)
}
Write-Host "Open browser: http://127.0.0.1:8000/status-ui"
