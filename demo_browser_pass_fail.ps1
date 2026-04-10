$ErrorActionPreference = 'Stop'

$operatorHeaders = @{ 'X-Role' = 'operator' }
$viewerHeaders = @{ 'X-Role' = 'viewer' }

try {
    Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -Method Get | Out-Null
} catch {
    Write-Host 'Server not running. Start it first with .\start_server.ps1' -ForegroundColor Red
    exit 1
}

Write-Host 'Running PASS demo...' -ForegroundColor Cyan
$valid = Get-Content 'f:\CI-Cd\autonomous-cicd-optimizer\samples\source_event_gitlab.json' -Raw | ConvertFrom-Json
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/webhooks/gitlab-ci' -Headers $operatorHeaders -ContentType 'application/json' -Body ($valid.payload | ConvertTo-Json -Depth 20) | Out-Null
$statusPass = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/status/checks' -Headers $viewerHeaders
Write-Host "PASS state all_passed: $($statusPass.all_passed)"
Start-Process "http://127.0.0.1:8000/status-ui?mode=pass&ts=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

Write-Host 'Press Enter to run FAIL demo...' -ForegroundColor Yellow
[void][System.Console]::ReadLine()

Write-Host 'Running FAIL demo...' -ForegroundColor Cyan
$badPayload = @{
    tenant_id = 'demo-enterprise'
    project_id = 'proj-bad'
    ref = 'main'
    sha = 'bad123'
    pipeline = @{ id = 9999; status = 'failed'; duration = 10 }
    jobs = @(
        @{
            id = 'bad-job'
            name = 'test'
            stage = 'test'
            status = 'failed'
            duration = 'oops'
        }
    )
}
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/webhooks/gitlab-ci' -Headers $operatorHeaders -ContentType 'application/json' -Body ($badPayload | ConvertTo-Json -Depth 20) | Out-Null
$statusFail = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/status/checks' -Headers $viewerHeaders
Write-Host "FAIL state all_passed: $($statusFail.all_passed)"
Start-Process "http://127.0.0.1:8000/status-ui?mode=fail&ts=$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"

Write-Host 'Done. Browser opened for both PASS and FAIL states.' -ForegroundColor Green
