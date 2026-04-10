$ErrorActionPreference = 'Stop'

$operatorHeaders = @{ 'X-Role' = 'operator' }
$viewerHeaders = @{ 'X-Role' = 'viewer' }

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

$webhook = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/webhooks/gitlab-ci' -Headers $operatorHeaders -ContentType 'application/json' -Body ($badPayload | ConvertTo-Json -Depth 20)
$status = Invoke-RestMethod -Method Get -Uri 'http://127.0.0.1:8000/status/checks' -Headers $viewerHeaders

Write-Host "Webhook response: $($webhook | ConvertTo-Json -Compress)"
Write-Host "all_passed: $($status.all_passed)"
$status.checks | ForEach-Object {
    Write-Host ("check=" + $_.name + " | passed=" + $_.passed + " | detail=" + $_.detail)
}
Write-Host "Open browser: http://127.0.0.1:8000/status-ui"
