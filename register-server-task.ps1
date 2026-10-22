#Requires -RunAsAdministrator
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Install dependencies in .venv first." }
if (-not (Test-Path (Join-Path $root ".env"))) { throw "Configure .env first." }
$taskName = "BTL Workload Server"
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "`"$(Join-Path $root 'scripts\server.py')`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "S-1-5-19" -LogonType ServiceAccount
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 10 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description "BTL Waitress application server" -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Started '$taskName'. Check /healthz and Task Scheduler history."
