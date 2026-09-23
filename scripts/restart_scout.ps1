$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host "== Autonomous AI Scout: sync + restart ==" -ForegroundColor Cyan
git fetch origin
git checkout fix/N9-N30-final-hardening
git reset --hard origin/fix/N9-N30-final-hardening

$existing = Get-CimInstance Win32_Process |
  Where-Object {
    $_.Name -match '^python(\.exe)?$' -and
    $_.CommandLine -match 'autonomous_agent\.server'
  }

foreach ($process in $existing) {
  Write-Host ("Stopping old Scout server PID {0}" -f $process.ProcessId)
  Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500

$python = (Get-Command python -ErrorAction Stop).Source
Write-Host "Starting latest Scout server on http://127.0.0.1:8000" -ForegroundColor Green
Start-Process -FilePath $python -ArgumentList @(
  "-m", "autonomous_agent.server",
  "--host", "127.0.0.1",
  "--port", "8000",
  "--root", $repoRoot,
  "--open"
) -WorkingDirectory $repoRoot

Write-Host "Scout restarted. Refresh the browser after it opens." -ForegroundColor Green
