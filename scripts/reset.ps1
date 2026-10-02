param([string]$RunId = [guid]::NewGuid().ToString())
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$seedFile = Join-Path $env:TEMP ("hutch-seed-{0}.sql" -f [guid]::NewGuid())
$containerFile = '/tmp/hutch-seed-run.sql'
Push-Location $root
try {
  docker compose --env-file .env up -d --wait
  if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL failed to start.' }
  $pgUserLine = Select-String -Path '.env' -Pattern '^POSTGRES_USER=' | Select-Object -First 1
  $pgDbLine = Select-String -Path '.env' -Pattern '^POSTGRES_DB=' | Select-Object -First 1
  $pgUser = if ($pgUserLine) { $pgUserLine.Line.Substring('POSTGRES_USER='.Length) } else { 'hutch_admin' }
  $pgDb = if ($pgDbLine) { $pgDbLine.Line.Substring('POSTGRES_DB='.Length) } else { 'hutch_resolve' }
  $lifecycleSql = "SELECT (SELECT count(*)=2 FROM information_schema.columns WHERE table_schema='sandbox' AND table_name='sandbox_runs' AND column_name IN ('run_status','retired_at')) AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='resolve' AND table_name='sessions' AND column_name='revoked_at') AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema='resolve' AND table_name='voice_bindings' AND column_name='revoked_at')"
  $lifecycleReady = docker compose --env-file .env exec -T postgres psql -At -v ON_ERROR_STOP=1 -U $pgUser -d $pgDb -c $lifecycleSql
  if ($LASTEXITCODE -ne 0 -or $lifecycleReady.Trim() -ne 't') {
    throw 'Resolve run-lifecycle migrations are required. Run python -m alembic upgrade head first.'
  }
  python scripts/seed_run.py $RunId $seedFile --retire-active
  if ($LASTEXITCODE -ne 0) { throw 'Could not generate a unique fixture run.' }
  docker compose --env-file .env cp $seedFile ("postgres:{0}" -f $containerFile)
  if ($LASTEXITCODE -ne 0) { throw 'Could not copy seed data to PostgreSQL.' }
  docker compose --env-file .env exec -T postgres sh -lc 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -f /tmp/hutch-seed-run.sql'
  if ($LASTEXITCODE -ne 0) { throw 'Fixture reset failed; inspect the PostgreSQL log.' }
  Write-Host ("Created active synthetic sandbox run {0}; prior runs retired and sessions/Voice bindings revoked." -f $RunId)
} finally {
  Remove-Item -LiteralPath $seedFile -Force -ErrorAction SilentlyContinue
  docker compose --env-file .env exec -T postgres rm -f $containerFile 2>$null
  Pop-Location
}
