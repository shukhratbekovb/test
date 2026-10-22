$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$backupScript = Join-Path $root "backup.ps1"
$taskName = "BTL Workload Database Backup"

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$backupScript`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At "2:00AM"
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Creates and verifies a daily SQLite backup for BTL Workload." `
    -Force | Out-Null

Write-Output "Scheduled daily backup task '$taskName' at 02:00. Run backup.ps1 once and inspect the backups folder to verify access."
