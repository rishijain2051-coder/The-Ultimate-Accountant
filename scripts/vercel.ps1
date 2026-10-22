# This repository's own Vercel CLI (control-plane/node_modules, pinned in control-plane/package.json).
#
#   .\scripts\vercel.ps1 login        sign in; the login is kept in control-plane\.vercel-cli
#   .\scripts\vercel.ps1 link         link control-plane\ to the Vercel project
#   .\scripts\vercel.ps1 whoami       any other vercel command, run in control-plane\
#
# --global-config keeps the sign-in inside this repository (git-ignored), apart from
# any vercel installed for the whole computer and its login.
$cp = Join-Path (Split-Path $PSScriptRoot) "control-plane"
$cli = Join-Path $cp "node_modules\.bin\vercel.cmd"
if (-not (Test-Path $cli)) {
  Write-Host "Installing the Vercel CLI into control-plane\ ..."
  Push-Location $cp; & npm.cmd install --no-fund --no-audit; Pop-Location
}
& $cli --global-config (Join-Path $cp ".vercel-cli") --cwd $cp @args
exit $LASTEXITCODE
