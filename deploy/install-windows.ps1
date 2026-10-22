#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidatePattern('^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$')]
    [string]$HostName = 'monitoring.btl.local',
    [string]$InstallPath = 'C:\Apps\BTL',
    [ValidateRange(1024, 65535)][int]$Port = 8080,
    [string]$CertificateThumbprint
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$source = Split-Path $PSScriptRoot -Parent
$InstallPath = [IO.Path]::GetFullPath($InstallPath).TrimEnd('\')
if ($InstallPath -eq [IO.Path]::GetFullPath($source).TrimEnd('\')) {
    throw 'Extract the source outside InstallPath, for example into C:\Temp\BTL-source.'
}
if (-not [Environment]::Is64BitProcess -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'Run 64-bit Windows PowerShell on an x64 Windows Server.'
}
if (-not (Get-Command Install-WindowsFeature -ErrorAction SilentlyContinue)) {
    throw 'Windows Server with ServerManager is required (Windows Server 2016 or newer).'
}
$siteName = 'BTL-Monitoring'
$marker = Join-Path $InstallPath '.btl-install.json'
$existing = Test-Path $marker
if ((Test-Path $InstallPath) -and -not $existing -and @(Get-ChildItem -Force $InstallPath).Count) {
    throw 'InstallPath is not empty and is not managed by this installer. Choose an empty directory.'
}
if ($existing) {
    $previous = Get-Content -Raw $marker | ConvertFrom-Json
    if ($previous.HostName -ne $HostName -or $previous.Port -ne $Port) {
        throw 'Existing installation uses another hostname or port. Use the original parameters.'
    }
}

function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    & $File @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed ($LASTEXITCODE): $File" }
}
function Install-SignedPackage {
    param([string]$Url, [string]$Name, [string]$Publisher, [string]$Arguments)
    $file = Join-Path $cache $Name
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $file
    $signature = Get-AuthenticodeSignature $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch $Publisher) {
        throw "Invalid installer signature: $Name. Check the server clock and certificate trust."
    }
    if ($Name.EndsWith('.msi')) {
        $process = Start-Process msiexec.exe -ArgumentList "/i `"$file`" /qn /norestart" -Wait -PassThru
    } else {
        $process = Start-Process $file -ArgumentList $Arguments -Wait -PassThru
    }
    if ($process.ExitCode -eq 3010) { throw 'A reboot is required. Restart Windows, then run this script again.' }
    if ($process.ExitCode -ne 0) { throw "Installer failed: $Name ($($process.ExitCode))" }
}
function Set-PrivateDirectory {
    param([string]$Path, [switch]$ServiceRead)
    New-Item -ItemType Directory -Force $Path | Out-Null
    $acl = New-Object Security.AccessControl.DirectorySecurity
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new($sid), 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    if ($ServiceRead) {
        $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new(
            [Security.Principal.SecurityIdentifier]::new('S-1-5-19'), 'ReadAndExecute', 'ContainerInherit,ObjectInherit', 'None', 'Allow'))
    }
    Set-Acl -Path $Path -AclObject $acl
}
function New-RandomSecret {
    $bytes = New-Object byte[] 48
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [Convert]::ToBase64String($bytes)
}

Set-PrivateDirectory -Path $InstallPath -ServiceRead
if (-not $existing) {
    @{ HostName = $HostName; Port = $Port; State = 'installing' } |
        ConvertTo-Json | Set-Content -Encoding UTF8 $marker
}
$cache = Join-Path $InstallPath 'installer-cache'
Set-PrivateDirectory $cache
Write-Host '[1/7] Installing IIS, URL Rewrite, ARR and Python...'
$result = Install-WindowsFeature Web-Server, Web-Mgmt-Tools -IncludeManagementTools
if (-not $result.Success) { throw 'IIS installation failed.' }
if ($result.RestartNeeded -eq 'Yes') { throw 'Restart Windows, then run this script again.' }
if (-not (Test-Path "$env:windir\System32\inetsrv\rewrite.dll")) {
    Install-SignedPackage -Url 'https://download.microsoft.com/download/1/2/8/128E2E22-C1B9-44A4-BE2A-5859ED1D4592/rewrite_amd64_en-US.msi' `
        -Name 'rewrite.msi' -Publisher 'Microsoft Corporation'
}
if (-not (Test-Path "$env:windir\System32\inetsrv\requestRouter.dll")) {
    Install-SignedPackage -Url 'https://download.microsoft.com/download/E/9/8/E9849D6A-020E-47E4-9FD0-A023E99B54EB/requestRouter_amd64.msi' `
        -Name 'arr.msi' -Publisher 'Microsoft Corporation'
}
# A machine-wide runtime is accessible to LOCAL SERVICE, unlike a user's AppData install.
$runtime = Join-Path $env:ProgramFiles 'BTL-Python313'
$basePython = Join-Path $runtime 'python.exe'
if (-not (Test-Path $basePython)) {
    Install-SignedPackage -Url 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe' `
        -Name 'python.exe' -Publisher 'Python Software Foundation' `
        -Arguments "/quiet InstallAllUsers=1 TargetDir=`"$runtime`" Include_test=0 Include_launcher=0 PrependPath=0"
}
if (-not (Test-Path $basePython)) { throw 'Machine-wide Python installation was not found.' }
Import-Module WebAdministration
foreach ($site in @(Get-Website)) {
    if ($site.Name -eq $siteName -and -not $existing) { throw "IIS site $siteName already exists and is not managed by this installer." }
    if ($site.Name -eq $siteName) { continue }
    foreach ($binding in @($site.Bindings.Collection)) {
        if ($binding.bindingInformation -match ":(80|443):$([regex]::Escape($HostName))$") {
            throw "Hostname is already bound to IIS site $($site.Name). No binding was changed."
        }
    }
}

Write-Host '[2/7] Backing up existing data and installing application files...'
$task = Get-ScheduledTask -TaskName 'BTL Workload Server' -ErrorAction SilentlyContinue
if ($task -and -not $existing) { throw 'An unmanaged BTL server task exists. Migrate it before running this installer.' }
if ($existing) {
    if ((Test-Path (Join-Path $InstallPath 'backup.ps1')) -and (Test-Path (Join-Path $InstallPath 'data\workload.sqlite3'))) {
        & (Join-Path $InstallPath 'backup.ps1')
    }
    if ($task) {
        Stop-ScheduledTask -TaskName 'BTL Workload Server'
        Start-Sleep -Seconds 2
    }
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Port $Port is still occupied. Stop the previous process or choose another port on a fresh install."
}
# Never replace .env, database, backups, secrets or an existing virtual environment.
$files = @('app.py', 'index.html', 'requirements.txt', 'requirements.lock.txt', '.env.example',
    'start_server.ps1', 'backup.ps1', 'register-server-task.ps1', 'register-backup-task.ps1', 'README.md')
foreach ($file in $files) { Copy-Item -LiteralPath (Join-Path $source $file) -Destination $InstallPath -Force }
foreach ($directory in @('static', 'scripts', 'tests', 'deploy')) {
    New-Item -ItemType Directory -Force (Join-Path $InstallPath $directory) | Out-Null
    Get-ChildItem -Path (Join-Path $source $directory) -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $InstallPath $directory) -Force
    }
}
foreach ($directory in @('data', 'backups', 'logs')) {
    $path = Join-Path $InstallPath $directory
    New-Item -ItemType Directory -Force $path | Out-Null
    Invoke-Native -File icacls.exe -Arguments @($path, '/grant', '*S-1-5-19:(OI)(CI)M')
}
$secrets = Join-Path $InstallPath 'secrets'
Set-PrivateDirectory $secrets
$envFile = Join-Path $InstallPath '.env'
if (-not (Test-Path $envFile)) {
    $password = 'AaBb1!' + (New-RandomSecret)
    @"
HOST=127.0.0.1
PORT=$Port
DATABASE_PATH=data/workload.sqlite3
BACKUP_DIR=backups
SECRET_KEY=$(New-RandomSecret)
COOKIE_SECURE=1
ADMIN_USERNAME=admin
ADMIN_PASSWORD=$password
SESSION_HOURS=8
BACKUP_RETENTION_DAYS=30
"@ | Set-Content -Encoding UTF8 $envFile
    "URL=https://$HostName`r`nUsername=admin`r`nInitial password=$password`r`nChange the password after first login." |
        Set-Content -Encoding UTF8 (Join-Path $secrets 'initial-admin.txt')
}
$python = Join-Path $InstallPath '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Invoke-Native $basePython @('-m', 'venv', (Join-Path $InstallPath '.venv')) }
Invoke-Native $python @('-m', 'pip', 'install', '-r', (Join-Path $InstallPath 'requirements.lock.txt'))
Invoke-Native $python @('-m', 'pip', 'check')

Write-Host '[3/7] Testing application and validating configuration...'
# Tests use temporary databases and an isolated process. They do not touch production data.
Push-Location $InstallPath
try { Invoke-Native $python @('-m', 'unittest', 'discover', '-s', 'tests', '-v') }
finally { Pop-Location }
Invoke-Native $python @((Join-Path $InstallPath 'scripts\server.py'), '--check')

Write-Host '[4/7] Selecting HTTPS certificate...'
if ($CertificateThumbprint) {
    $certificate = Get-Item "Cert:\LocalMachine\My\$($CertificateThumbprint.Replace(' ', ''))"
} else {
    $certificate = Get-ChildItem Cert:\LocalMachine\My | Where-Object {
        $_.HasPrivateKey -and $_.NotAfter -gt (Get-Date).AddDays(7) -and
        @($_.DnsNameList | Where-Object { $null -ne $_ } | ForEach-Object {
            if ($_ -is [string]) { $_ } else { $_.Unicode }
        }) -contains $HostName
    } | Sort-Object NotAfter -Descending | Select-Object -First 1
    if (-not $certificate) {
        $certificate = New-SelfSignedCertificate -DnsName $HostName -CertStoreLocation Cert:\LocalMachine\My `
            -FriendlyName "BTL $HostName" -NotAfter (Get-Date).AddYears(1) -KeyLength 2048 -HashAlgorithm SHA256
    }
}
if (-not $certificate.HasPrivateKey -or $certificate.NotAfter -le (Get-Date) -or
    @($certificate.DnsNameList | Where-Object { $null -ne $_ } | ForEach-Object {
            if ($_ -is [string]) { $_ } else { $_.Unicode }
        }) -notcontains $HostName) {
    throw 'Certificate must have a private key, be valid, and include the exact hostname in its DNS names.'
}
Export-Certificate -Cert $certificate -FilePath (Join-Path $secrets 'btl-server.cer') -Force | Out-Null

Write-Host '[5/7] Configuring IIS reverse proxy and HTTPS...'
$appcmd = "$env:windir\System32\inetsrv\appcmd.exe"
Invoke-Native $appcmd @('add', 'backup', "BTL-before-$(Get-Date -Format 'yyyyMMdd-HHmmss')")
Set-WebConfigurationProperty -PSPath 'MACHINE/WEBROOT/APPHOST' -Filter 'system.webServer/proxy' -Name enabled -Value $true
$public = Join-Path $InstallPath 'public'
New-Item -ItemType Directory -Force $public | Out-Null
Invoke-Native icacls.exe @($public, '/grant', '*S-1-5-32-568:(OI)(CI)RX')
@"
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <system.webServer>
    <directoryBrowse enabled="false" />
    <rewrite>
      <rules>
        <clear />
        <rule name="HTTPS" stopProcessing="true">
          <match url="(.*)" />
          <conditions><add input="{HTTPS}" pattern="off" /></conditions>
          <action type="Redirect" url="https://$HostName/{R:1}" redirectType="Found" appendQueryString="true" />
        </rule>
        <rule name="BTL proxy" stopProcessing="true">
          <match url="(.*)" />
          <action type="Rewrite" url="http://127.0.0.1:$Port/{R:1}" appendQueryString="true" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
"@ | Set-Content -Encoding UTF8 (Join-Path $public 'web.config')
if (-not (Test-Path "IIS:\AppPools\$siteName")) { New-WebAppPool $siteName | Out-Null }
Set-ItemProperty "IIS:\AppPools\$siteName" -Name managedRuntimeVersion -Value ''
if (-not (Test-Path "IIS:\Sites\$siteName")) {
    New-Website -Name $siteName -PhysicalPath $public -ApplicationPool $siteName -Port 80 -HostHeader $HostName | Out-Null
}
if (-not (Get-WebBinding -Name $siteName -Protocol https)) {
    New-WebBinding -Name $siteName -Protocol https -Port 443 -HostHeader $HostName -SslFlags 1
}
(Get-WebBinding -Name $siteName -Protocol https).AddSslCertificate($certificate.Thumbprint, 'My')
Start-Website $siteName
foreach ($webPort in @(80, 443)) {
    $ruleName = "BTL-Monitoring-$webPort"
    if (-not (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $ruleName -DisplayName "BTL Monitoring TCP $webPort" -Direction Inbound `
            -Action Allow -Protocol TCP -LocalPort $webPort -Profile Domain,Private | Out-Null
    }
}

Write-Host '[6/7] Registering startup and backup tasks...'
@{ HostName = $HostName; Port = $Port; CertificateThumbprint = $certificate.Thumbprint } |
    ConvertTo-Json | Set-Content -Encoding UTF8 $marker
& (Join-Path $InstallPath 'register-server-task.ps1')
& (Join-Path $InstallPath 'register-backup-task.ps1')

Write-Host '[7/7] Checking running server and creating first verified backup...'
$healthy = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/healthz" -TimeoutSec 2
        if ($health.status -eq 'ok') { $healthy = $true; break }
    } catch { Start-Sleep -Seconds 1 }
}
if (-not $healthy) { throw "Server did not become healthy. Inspect $InstallPath\logs\server.log and Task Scheduler." }
Invoke-Native $python @((Join-Path $InstallPath 'scripts\check_https.py'), $HostName, (Join-Path $secrets 'btl-server.cer'))
& (Join-Path $InstallPath 'backup.ps1')
Write-Host "Installed. Application URL: https://$HostName" -ForegroundColor Green
Write-Host "Initial admin credentials (administrators only): $secrets\initial-admin.txt"
Write-Host "DNS: create an A record for $HostName pointing to this server's LAN IP."
if ($certificate.Subject -eq $certificate.Issuer) {
    Write-Warning "Self-signed HTTPS: distribute $secrets\btl-server.cer as a Trusted Root certificate to employee computers using your domain GPO, or rerun with -CertificateThumbprint for an internal CA certificate."
}
Write-Host 'Do not distribute initial-admin.txt or .env. Change the initial password after login.'
