$ErrorActionPreference = 'Stop'

$listeners = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue |
    Where-Object { $_.State -eq 'Listen' } |
    Select-Object -ExpandProperty OwningProcess -Unique

if (-not $listeners) {
    Write-Host 'No running server found on port 8000.'
    exit 0
}

foreach ($pid in $listeners) {
    Stop-Process -Id $pid -Force
    Write-Host "Stopped process on port 8000: PID $pid"
}
