param([string]$RunId = [guid]::NewGuid().ToString())
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$seedFile = Join-Path $env:TEMP ("hutch-seed-{0}.sql" -f [guid]::NewGuid())
$containerFile = '/tmp/hutch-seed-run.sql'
Push-Location $root
try {
  docker compose --env-file .env up -d --wait
  if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
  python scripts/seed_run.py $RunId $seedFile
  if ($LASTEXITCODE -ne 0) { throw 'Could not generate a unique fixture run.' }
  docker compose --env-file .env cp $seedFile ("postgres:{0}" -f $containerFile)
  if ($LASTEXITCODE -ne 0) { throw 'Could not copy seed data to PostgreSQL.' }
  docker compose --env-file .env exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/hutch-seed-run.sql'
  if ($LASTEXITCODE -ne 0) { throw 'Fixture reset failed; inspect the PostgreSQL log.' }
  Write-Host ("Created synthetic sandbox run {0}; prior runs are retained." -f $RunId)
} finally {
  Remove-Item -LiteralPath $seedFile -Force -ErrorAction SilentlyContinue
  docker compose --env-file .env exec -T postgres rm -f $containerFile 2>$null
  Pop-Location
}
