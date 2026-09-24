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
$expectedRevision = (git rev-parse --short HEAD).Trim()
Write-Host ("Starting Scout server revision {0} on http://127.0.0.1:8000" -f $expectedRevision) -ForegroundColor Green
Start-Process -FilePath $python -ArgumentList @(
  "-m", "autonomous_agent.server",
  "--host", "127.0.0.1",
  "--port", "8000",
  "--root", $repoRoot,
  "--open"
) -WorkingDirectory $repoRoot

$ready = $false
for ($attempt = 1; $attempt -le 15; $attempt++) {
  Start-Sleep -Milliseconds 500
  try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/health" -TimeoutSec 2
    if ($health.status -eq "ok" -and $health.build_revision -eq $expectedRevision) {
      $ready = $true
      Write-Host ("Scout online and serving revision {0}" -f $health.build_revision) -ForegroundColor Green
      break
    }
  } catch {
    # Server is still starting.
  }
}
if (-not $ready) {
  throw "Scout server did not become healthy on revision $expectedRevision"
}

$smokePath = Join-Path $env:TEMP "autonomous-ai-scout-readme-smoke.py"
@'
from autonomous_agent.task_intent import classify_intent
from autonomous_agent.task_plan_models import TaskIntent
from autonomous_agent.tool_router import DynamicToolRouter

task = "Read README.md and give me a human-readable summary. Do not modify any files."
intent = classify_intent(task)
selection = DynamicToolRouter().select_names(task)
assert intent is TaskIntent.WORKSPACE, intent
assert selection.tool_names == ("filesystem.read",), selection.tool_names
print("README smoke test: WORKSPACE -> filesystem.read")
'@ | Set-Content -Path $smokePath -Encoding UTF8

try {
  $smokeOutput = & $python $smokePath 2>&1
  if ($LASTEXITCODE -ne 0) {
    $smokeOutput | ForEach-Object { Write-Host $_ -ForegroundColor Red }
    throw "Local Scout smoke test failed for direct README read"
  }
  $smokeOutput | ForEach-Object { Write-Host $_ -ForegroundColor Green }
}
finally {
  Remove-Item -Force -ErrorAction SilentlyContinue $smokePath
}

Write-Host "Scout restarted, revision verified, and README smoke test passed. Refresh the browser." -ForegroundColor Green
