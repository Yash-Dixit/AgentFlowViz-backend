$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $projectRoot

.\.venv\Scripts\python.exe -m alembic upgrade head
