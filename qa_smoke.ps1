param(
    [switch]$StartServer
)

$ErrorActionPreference = 'Stop'
$projectRoot = 'f:\CI-Cd\autonomous-cicd-optimizer'
$python = 'f:/CI-Cd/.venv/Scripts/python.exe'
$serverProcess = $null
$results = New-Object System.Collections.Generic.List[object]

function Add-Result {
    param(
        [string]$Name,
        [bool]$Passed,
        [string]$Details
    )

    $results.Add([pscustomobject]@{
        Check = $Name
        Passed = $Passed
        Details = $Details
    }) | Out-Null
}

function Show-VisualSummary {
    param(
        [System.Collections.Generic.List[object]]$Items
    )

    Write-Host "\nVisual status (blue=pass, red=fail):"
    foreach ($item in $Items) {
        if ($item.Passed) {
            Write-Host "o PASS  $($item.Check)" -ForegroundColor Blue
        } else {
            Write-Host "o FAIL  $($item.Check)" -ForegroundColor Red
        }
    }
}

try {
    Set-Location $projectRoot

    if ($StartServer) {
        $serverProcess = Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', '--app-dir', $projectRoot, 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -PassThru
        Start-Sleep -Seconds 2
    }

    $testOutput = & $python -m pytest -q "$projectRoot/tests" 2>&1
    $testPassed = $LASTEXITCODE -eq 0
    Add-Result 'Unit tests' $testPassed ($testOutput -join "`n")

    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -Method Get
    Add-Result 'Health endpoint' ($health.status -eq 'ok') ($health | ConvertTo-Json -Depth 10)

    $operatorHeaders = @{ 'X-Role' = 'operator' }
    $viewerHeaders = @{ 'X-Role' = 'viewer'; 'X-User' = 'qa-user' }
    $adminHeaders = @{ 'X-Role' = 'admin' }

    $githubPayload = Get-Content "$projectRoot/samples/source_event_github.json" -Raw | ConvertFrom-Json
    $ingest = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/ingest/source-event' -Headers $operatorHeaders -ContentType 'application/json' -Body ($githubPayload | ConvertTo-Json -Depth 20)
    $runId = $ingest.run_ids[0]
    Add-Result 'GitHub ingest' ($ingest.ingested_count -gt 0) ($ingest | ConvertTo-Json -Depth 10)

    $score = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/score/run/$runId" -Headers $operatorHeaders
    Add-Result 'Score run' ($null -ne $score.risk_score) ($score | ConvertTo-Json -Depth 10)

    $feedbackBody = '{"vote":"up","comment":"qa smoke pass"}'
    $feedback = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/feedback/run/$runId" -Headers $viewerHeaders -ContentType 'application/json' -Body $feedbackBody
    Add-Result 'Feedback' ($feedback.vote -eq 'up') ($feedback | ConvertTo-Json -Depth 10)

    $kpis = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/kpis' -Headers $viewerHeaders
    Add-Result 'KPIs' ($kpis.total_runs -ge 1) ($kpis | ConvertTo-Json -Depth 10)

    $unauthorizedPassed = $false
    $unauthorizedDetails = ''
    try {
        Invoke-WebRequest -Uri 'http://127.0.0.1:8000/runs' -UseBasicParsing | Out-Null
        $unauthorizedDetails = 'Unexpected success'
    } catch {
        $unauthorizedPassed = $_.Exception.Response.StatusCode.value__ -eq 403
        $unauthorizedDetails = "Expected 403, got $($_.Exception.Response.StatusCode.value__)"
    }
    Add-Result 'RBAC rejection' $unauthorizedPassed $unauthorizedDetails

    $webhookScript = @"
import json
import sys
import time
sys.path.insert(0, r'f:/CI-Cd/autonomous-cicd-optimizer')
from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
with open(r'f:/CI-Cd/autonomous-cicd-optimizer/samples/source_event_gitlab.json', 'r', encoding='utf-8') as f:
    payload = json.load(f)
with TestClient(app) as client:
    response = client.post('/webhooks/gitlab-ci', json=payload['payload'], headers={'X-Role':'operator'})
    status = {}
    ok = False
    for _ in range(40):
        status = client.get('/queue/status', headers={'X-Role':'viewer'}).json()
        if status['processed'] >= 1:
            ok = True
            break
        time.sleep(0.05)
print(json.dumps({'status_code': response.status_code, 'queued': response.json(), 'queue': status, 'processed_ok': ok}))
"@
    $webhookResult = & $python -c $webhookScript 2>&1 | ConvertFrom-Json
    Add-Result 'Webhook queue' ($webhookResult.processed_ok -eq $true) ($webhookResult | ConvertTo-Json -Depth 10)

}
finally {
    if ($serverProcess -and -not $serverProcess.HasExited) {
        Stop-Process -Id $serverProcess.Id -Force
    }
}

$results | Format-Table -AutoSize
Show-VisualSummary -Items $results
$failed = $results | Where-Object { -not $_.Passed }
if ($failed) {
    Write-Host "\nOverall result: FAIL" -ForegroundColor Red
    exit 1
}

Write-Host "\nOverall result: PASS" -ForegroundColor Green
