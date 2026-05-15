$ErrorActionPreference = "Stop"

$containerName = "agentflowviz-postgres"
$maxAttempts = 30

for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
    $status = docker inspect --format='{{.State.Health.Status}}' $containerName 2>$null
    if ($status -eq "healthy") {
        Write-Host "Postgres is healthy."
        exit 0
    }
    Start-Sleep -Seconds 2
}

throw "Postgres container did not become healthy after $($maxAttempts * 2) seconds."
