$ErrorActionPreference = "Stop"

$projectRoot = Resolve-Path "$PSScriptRoot\.."
Set-Location $projectRoot

if (-not (Test-Path ".venv")) {
    py -3 -m venv .venv
}

.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example. Add Telegram secrets before the live Telegram demo."
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        docker compose up -d postgres
        & "$PSScriptRoot\wait-for-postgres.ps1"
        .\.venv\Scripts\python.exe -m alembic upgrade head
    } catch {
        Write-Host "Postgres was not started. Start Docker Desktop or your local Postgres service, then run scripts\migrate.ps1."
    }
} else {
    Write-Host "Docker was not found. Install/start Postgres manually, then run scripts\migrate.ps1."
}

if (Get-Command ollama -ErrorAction SilentlyContinue) {
    ollama pull gpt-oss:20b
    ollama pull qwen3.5:9b
    ollama pull qwen3-embedding:4b
    ollama pull llava:7b
} else {
    Write-Host "Ollama was not found on PATH. Install/start Ollama before running the demo."
}
