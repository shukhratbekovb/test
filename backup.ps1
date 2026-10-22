$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$environmentFile = Join-Path $root ".env"
if (-not (Test-Path $environmentFile)) {
    throw "Application .env file was not found."
}

Get-Content -Encoding UTF8 $environmentFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#")) {
        $separator = $line.IndexOf("=")
        if ($separator -gt 0) {
            $key = $line.Substring(0, $separator).Trim()
            $value = $line.Substring($separator + 1).Trim()
            [Environment]::SetEnvironmentVariable($key, $value, "Process")
        }
    }
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Python virtual environment was not found. Install the application dependencies first."
}

Set-Location $root
& $python (Join-Path $root "scripts\backup.py")
if ($LASTEXITCODE -ne 0) {
    throw "Database backup failed with exit code $LASTEXITCODE."
}
