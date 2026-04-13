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

    $viewerHeaders = @{ 'X-Role' = 'viewer'; 'X-User' = 'qa-user' }
    $adminHeaders = @{ 'X-Role' = 'admin' }

    $statusChecks = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/status/checks' -Headers $viewerHeaders
    Add-Result 'Status checks' ($statusChecks.all_passed -in @($true, $false)) ($statusChecks | ConvertTo-Json -Depth 10)

    $statusUi = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/status-ui?source=smoke' -UseBasicParsing
    Add-Result 'Status UI' ($statusUi.StatusCode -eq 200 -and (($statusUi.Content -match 'OVERALL PASS') -or ($statusUi.Content -match 'OVERALL FAIL'))) ($statusUi.Content.Substring(0, [Math]::Min(500, $statusUi.Content.Length)))

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

    $health2 = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -Method Get
    Add-Result 'Health recheck' ($health2.status -eq 'ok') ($health2 | ConvertTo-Json -Depth 10)

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
