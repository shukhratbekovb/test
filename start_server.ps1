$ErrorActionPreference = "Stop"
$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Install dependencies in .venv first." }
& $python (Join-Path $PSScriptRoot "scripts\server.py")
if ($LASTEXITCODE -ne 0) { throw "Application server failed with exit code $LASTEXITCODE. See logs\server.log." }
