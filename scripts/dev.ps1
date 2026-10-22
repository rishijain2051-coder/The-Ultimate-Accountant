<#
Development runner for Ultimate Accountant.

    .\scripts\dev.ps1              the app on :8770 (auto-reload) + the Invoice Bridge UI on :3000/bridge (hot reload)
    .\scripts\dev.ps1 -Test        every self-check: Reconciliation and Exports, the Invoice Bridge, the installer
    .\scripts\dev.ps1 -FakeTally   also start the simulated Tally on :9000
    .\scripts\dev.ps1 -ApiOnly     just the app (it also serves the last UI build at /bridge)

Data goes to data\ (the console) and data\bridge (the Invoice Bridge), never the installed app's.
#>
[CmdletBinding()]
param([switch]$Test, [switch]$FakeTally, [switch]$ApiOnly)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
  Write-Host "First run: creating .venv..." -ForegroundColor Cyan
  & python -m venv (Join-Path $Root ".venv")
  & $Py -m pip install --quiet --upgrade pip
  & $Py -m pip install --quiet -r (Join-Path $Root "requirements.txt")
}

if ($Test) {
  $ErrorActionPreference = "Continue"     # a warning on stderr is not a failure: the exit codes say
  $env:PYTHONIOENCODING = "utf-8"
  $env:ASSURANCE_DATA_DIR = Join-Path ([IO.Path]::GetTempPath()) "ultimate-accountant-selftest"
  Push-Location $Root
  try {
    $failed = 0
    & $Py -m app.backend.selftest; if ($LASTEXITCODE) { $failed++ }
    foreach ($t in "test_pipeline", "test_bank", "test_masters", "test_phone") {
      & $Py "app\bridge\tests\$t.py"; if ($LASTEXITCODE) { $failed++ }
    }
    & $Py "app\keeper\tests\test_kept_apart.py"; if ($LASTEXITCODE) { $failed++ }
    & $Py -m app.keeper.tests.test_books; if ($LASTEXITCODE) { $failed++ }
    & $Py "app\office\tests\test_office.py"; if ($LASTEXITCODE) { $failed++ }
    Push-Location packaging; & $Py test_update.py; if ($LASTEXITCODE) { $failed++ }; Pop-Location
    if ($failed) { Write-Host "$failed self-check(s) failed" -ForegroundColor Red } else { Write-Host "Every self-check passed" -ForegroundColor Green }
    exit $failed
  } finally { Pop-Location }
}

$procs = @()
try {
  if ($FakeTally) {
    Write-Host "Simulated Tally   -> http://127.0.0.1:9000" -ForegroundColor Yellow
    $procs += Start-Process $Py -ArgumentList "app\bridge\tests\fake_tally.py" -WorkingDirectory $Root -PassThru -NoNewWindow
  }
  $env:DATA_DIR = Join-Path $Root "data\bridge"
  Write-Host "The app           -> http://127.0.0.1:8770  (Invoice Bridge at /bridge/)" -ForegroundColor Green
  $procs += Start-Process $Py -ArgumentList "-m", "uvicorn", "app.main:app", "--reload", "--port", "8770" `
    -WorkingDirectory $Root -PassThru -NoNewWindow

  if ($ApiOnly) {
    Wait-Process -Id $procs[-1].Id
  } else {
    Write-Host "Invoice Bridge UI   -> http://localhost:3000/bridge/  (hot reload)" -ForegroundColor Green
    Push-Location (Join-Path $Root "bridge-ui")
    try {
      if (-not (Test-Path "node_modules")) { & npm.cmd install }
      & npm.cmd run dev
    } finally { Pop-Location }
  }
} finally {
  $procs | Where-Object { $_ -and -not $_.HasExited } | ForEach-Object { Stop-Process -Id $_.Id -Force }
}
