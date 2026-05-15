$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $projectRoot
$logDir = Join-Path $projectRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$stdoutLog = Join-Path $logDir "backend.out.log"
$stderrLog = Join-Path $logDir "backend.err.log"

if (Get-Command docker -ErrorAction SilentlyContinue) {
    docker compose up -d postgres
    & "$PSScriptRoot\wait-for-postgres.ps1"
}

.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 `
    1>> $stdoutLog `
    2>> $stderrLog
