# Step 5 - run the research dashboard + 24/7 worker as a Windows service, plus the scheduled
# health check, nightly backup and maintenance (run as administrator; safe to re-run).
#   powershell -ExecutionPolicy Bypass -File .\05-install-services.ps1
#
# Service FTMO-Research = `python -m research_agent ui --port 8602` (127.0.0.1 only) under NSSM.
# The dashboard brings the 24/7 worker back on start if the agent was left running.
# It runs as a local non-admin account (ftmo-svc) with a random password nobody needs to know.
# The broker hub (:8600) and the MT5 bridge are NOT installed.

param([switch]$NoStart)
. "$PSScriptRoot\common.ps1"
Assert-Admin
$nssm = Get-Nssm
if (-not (Test-Path $Python)) { throw "no venv at $VenvDir - run 04-setup-app.ps1 first" }

# ---- 1. service account ----------------------------------------------------------------------
function New-RandomPassword {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return ([Convert]::ToBase64String($bytes) -replace '[+/=]', 'x') + 'aA1!'
}
$plain = New-RandomPassword
$secure = ConvertTo-SecureString $plain -AsPlainText -Force
if (Get-LocalUser -Name $ServiceUser -ErrorAction SilentlyContinue) {
    Set-LocalUser -Name $ServiceUser -Password $secure -PasswordNeverExpires $true
    Write-FtmoLog "service account $ServiceUser exists - password rotated"
} else {
    New-LocalUser -Name $ServiceUser -Password $secure -PasswordNeverExpires -UserMayNotChangePassword `
        -Description 'Runs the FTMO research agent service' | Out-Null
    Write-FtmoLog "created local service account $ServiceUser (not an administrator)"
}
$hide = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
if (-not (Test-Path $hide)) { New-Item -Path $hide -Force | Out-Null }
New-ItemProperty -Path $hide -Name $ServiceUser -Value 0 -PropertyType DWord -Force | Out-Null   # not on the logon screen

foreach ($d in @($AppDir, $LogDir, $BackupDir)) {
    Invoke-Native -Exe 'icacls.exe' -Arguments @($d, '/grant', ('{0}:(OI)(CI)M' -f $ServiceUser), '/T', '/C', '/Q') | Out-Null
}
Protect-SecretFile $EnvFile -ExtraUser $ServiceUser

# ---- 2. the service ------------------------------------------------------------------------
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Write-FtmoLog "service $ServiceName exists - stopping to update its settings"
    Set-Content -Path $HoldFlag -Value 'reinstall' -Encoding ASCII
    Invoke-Native -Exe $nssm -Arguments @('stop', $ServiceName) -Ok @(0, 1, 2, 3) | Out-Null
} else {
    Invoke-Native -Exe $nssm -Arguments @('install', $ServiceName, $Python) | Out-Null
}
$envExtra = @('PYTHONUTF8=1', 'PYTHONIOENCODING=utf-8', 'PYTHONUNBUFFERED=1', "PG_BIN=$PgBin")
$bash = 'C:\Program Files\Git\bin\bash.exe'
if (Test-Path $bash) { $envExtra += "CLAUDE_CODE_GIT_BASH_PATH=$bash" }          # Claude CLI on Windows uses Git Bash
$settings = @(
    @('AppParameters', "-m research_agent ui --port $UiPort"),
    @('AppDirectory', $AppDir),
    @('DisplayName', 'FTMO Research Agent'),
    @('Description', 'FTMO AI research dashboard (127.0.0.1:8602) and 24/7 research worker. Research only - no broker access.'),
    @('Start', 'SERVICE_DELAYED_AUTO_START'),
    @('DependOnService', $PgService),
    @('AppStdout', (Join-Path $LogDir 'service.log')),
    @('AppStderr', (Join-Path $LogDir 'service.log')),
    @('AppStdoutCreationDisposition', '4'),
    @('AppStderrCreationDisposition', '4'),
    @('AppRotateFiles', '1'),
    @('AppRotateOnline', '1'),
    @('AppRotateBytes', '10485760'),
    @('AppExit', 'Default', 'Restart'),
    @('AppRestartDelay', '15000'),
    @('AppThrottle', '60000'),
    @('AppStopMethodConsole', '15000'),
    @('AppStopMethodWindow', '5000'),
    @('AppStopMethodThreads', '5000'),
    @('AppKillProcessTree', '1')
)
foreach ($s in $settings) { Invoke-Native -Exe $nssm -Arguments (@('set', $ServiceName) + $s) | Out-Null }
Invoke-Native -Exe $nssm -Arguments (@('set', $ServiceName, 'AppEnvironmentExtra') + $envExtra) | Out-Null
Invoke-Native -Exe $nssm -Arguments @('set', $ServiceName, 'ObjectName', ".\$ServiceUser", $plain) | Out-Null
$plain = $null
# if nssm.exe itself dies, the Service Control Manager restarts it
Invoke-Native -Exe 'sc.exe' -Arguments @('failure', $ServiceName, 'reset=', '86400', 'actions=', 'restart/60000/restart/60000/restart/300000') | Out-Null
Write-FtmoLog "service $ServiceName configured (runs as .\$ServiceUser, delayed auto-start after $PgService)"

# ---- 3. scheduled tasks (run as SYSTEM so the health check may restart the service) ----------
$scripts = Join-Path $AppDir 'deploy\windows'
function Register-FtmoTask {
    param([string]$Name, [string]$Script, $Trigger, [int]$LimitMinutes)
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory $scripts `
        -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f (Join-Path $scripts $Script))
    $set = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes $LimitMinutes)
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    Register-ScheduledTask -TaskName $Name -TaskPath $TaskFolder -Action $action -Trigger $Trigger -Settings $set `
        -Principal $principal -Force | Out-Null
    Write-FtmoLog "scheduled task $TaskFolder$Name registered"
}
$every5 = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
Register-FtmoTask 'HealthCheck' 'healthcheck.ps1' $every5 4
Register-FtmoTask 'NightlyBackup' 'backup.ps1' (New-ScheduledTaskTrigger -Daily -At '02:30') 120
Register-FtmoTask 'Maintenance' 'maintenance.ps1' (New-ScheduledTaskTrigger -Daily -At '03:30') 60

# ---- 4. start + verify -----------------------------------------------------------------------
Remove-Item $HoldFlag -ErrorAction SilentlyContinue
if ($NoStart) { Write-FtmoLog 'not started (-NoStart)'; exit 0 }
Start-Service $ServiceName
$h = $null
for ($i = 0; $i -lt 24 -and -not $h; $i++) { Start-Sleep -Seconds 5; $h = Get-Health }
if (-not $h) { throw "service started but $HealthUrl does not answer - see $LogDir\service.log" }
Write-FtmoLog ("health: {0} {1}" -f $h.status, (($h.problems) -join '; '))
Write-FtmoLog "dashboard: http://127.0.0.1:$UiPort/research (this PC only)"
