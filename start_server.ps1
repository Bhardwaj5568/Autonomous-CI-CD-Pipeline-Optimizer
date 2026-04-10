$ErrorActionPreference = 'Stop'

$projectRoot = 'f:\CI-Cd\autonomous-cicd-optimizer'
$python = 'f:/CI-Cd/.venv/Scripts/python.exe'

Set-Location $projectRoot
& $python -m uvicorn --app-dir $projectRoot app.main:app --host 127.0.0.1 --port 8000 --reload
