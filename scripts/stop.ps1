$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try { docker compose --env-file .env stop } finally { Pop-Location }
