# Health check - run every 5 minutes by the scheduled task \FTMO\HealthCheck (as SYSTEM).
# Also fine to run by hand:  powershell -ExecutionPolicy Bypass -File .\healthcheck.ps1
#
#   service stopped                  -> start it
#   dashboard not answering 2x       -> restart the service
#   agent on but worker dead         -> ask the dashboard to bring the worker back
#   anything else degraded           -> logged (logs\health.log), nothing restarted
#   C:\ftmo\HOLD exists              -> does nothing (create it before stopping the service on purpose)
# Exit 0 ok, 1 degraded, 2 down. Last result: logs\health-last.json

. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'
Set-StrictMode -Off            # health JSON fields are optional (e.g. worker = {error: ...})
$stateFile = Join-Path $LogDir 'health.state.json'
$fails = 0
if (Test-Path $stateFile) { try { $fails = [int](Get-Content $stateFile -Raw | ConvertFrom-Json).fails } catch { $fails = 0 } }

if (Test-Path $HoldFlag) { Write-FtmoLog "skipped: $HoldFlag present (deploy or manual maintenance)" 'INFO' 'health.log'; exit 0 }

$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if (-not $svc) { Write-FtmoLog "service $ServiceName is not installed" 'ERROR' 'health.log'; exit 2 }
if ($svc.Status -ne 'Running') {
    Write-FtmoLog "service $ServiceName is $($svc.Status) - starting it" 'WARN' 'health.log'
    try { Start-Service $ServiceName -ErrorAction Stop } catch { Write-FtmoLog "start failed: $_" 'ERROR' 'health.log' }
    @{ fails = 0 } | ConvertTo-Json | Set-Content $stateFile -Encoding UTF8
    exit 2
}

$h = Get-Health
if (-not $h) {
    $fails++
    if ($fails -ge 2) {
        Write-FtmoLog "dashboard did not answer $fails checks in a row - restarting $ServiceName" 'ERROR' 'health.log'
        try { Restart-Service $ServiceName -Force -ErrorAction Stop } catch { Write-FtmoLog "restart failed: $_" 'ERROR' 'health.log' }
        $fails = 0
    } else {
        Write-FtmoLog 'dashboard did not answer (will restart if it happens again)' 'WARN' 'health.log'
    }
    @{ fails = $fails } | ConvertTo-Json | Set-Content $stateFile -Encoding UTF8
    exit 2
}
@{ fails = 0 } | ConvertTo-Json | Set-Content $stateFile -Encoding UTF8
$h | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $LogDir 'health-last.json') -Encoding UTF8

$w = $h.checks.worker
if ($w -and $w.desired -in @('running', 'paused') -and -not $w.alive) {
    Write-FtmoLog 'agent is on but the worker is not alive - asking the dashboard to restart it' 'WARN' 'health.log'
    try { Invoke-RestMethod -Uri "http://127.0.0.1:$UiPort/api/research/agent/status" -TimeoutSec 30 -UseBasicParsing | Out-Null } catch { }
}
if ($h.status -eq 'ok') { exit 0 }
Write-FtmoLog ("{0}: {1}" -f $h.status, (($h.problems) -join '; ')) 'WARN' 'health.log'
if ($h.status -eq 'degraded') { exit 1 } else { exit 2 }
