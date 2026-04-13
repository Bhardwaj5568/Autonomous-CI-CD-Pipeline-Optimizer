param(
    [Parameter(Mandatory = $true)]
    [string]$BaseUrl,

    [string]$Role = 'viewer',

    [string]$ApiKey = ''
)

$ErrorActionPreference = 'Stop'

function Get-Headers([string]$role, [string]$apiKey) {
    $headers = @{
        'X-Role' = $role
    }
    if ($apiKey) {
        $headers['X-API-Key'] = $apiKey
    }
    return $headers
}

$base = $BaseUrl.TrimEnd('/')
$headers = Get-Headers -role $Role -apiKey $ApiKey

Write-Host 'Reading queue status before check...' -ForegroundColor Cyan
$before = Invoke-RestMethod -Method Get -Uri "$base/queue/status" -Headers $headers

Write-Host 'Reading status checks...' -ForegroundColor Cyan
$checks = Invoke-RestMethod -Method Get -Uri "$base/status/checks" -Headers $headers

Write-Host ''
Write-Host '==== Integration Verification Summary ====' -ForegroundColor Green
Write-Host ("All Passed: " + $checks.all_passed)
Write-Host ("Queue Processed: " + $before.processed)
Write-Host ("Queue Failed: " + $before.failed)
Write-Host ("Duplicate Deliveries: " + $before.duplicate_deliveries)
Write-Host ''

Write-Host 'Check details:' -ForegroundColor Yellow
foreach ($item in $checks.checks) {
    $state = if ($item.passed) { 'PASS' } else { 'FAIL' }
    Write-Host ("- " + $state + " | " + $item.name + " | " + $item.detail)
}

Write-Host ''
Write-Host 'Tip: Run this after triggering GitHub workflow to confirm live processing.' -ForegroundColor DarkCyan
