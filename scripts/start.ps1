$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
  if (-not (Test-Path .env)) { Copy-Item .env.example .env }
  docker compose --env-file .env up -d --wait
  if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
  Write-Host 'Sandbox ready. Fresh databases apply both migrations and the initial fixture run automatically.'
} finally { Pop-Location }
