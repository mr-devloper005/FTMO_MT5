# Daily maintenance - run by the scheduled task \FTMO\Maintenance (03:30, as SYSTEM).
# Prunes daily price caches older than -KeepCacheDays (each day is ~860 MB; today's is never
# touched) and rotates the worker log. Research data, reports and RAG documents are not touched.

param([int]$KeepCacheDays = 3)
. "$PSScriptRoot\common.ps1"
try {
    Invoke-Agent @('maintenance', '--keep-cache-days', [string]$KeepCacheDays) | Out-Null
    $free = [math]::Round((Get-PSDrive -Name ($FtmoRoot.Substring(0, 1))).Free / 1GB, 1)
    Write-FtmoLog "maintenance ok - $free GB free on $($FtmoRoot.Substring(0, 2))" 'INFO' 'maintenance.log'
} catch {
    Write-FtmoLog "MAINTENANCE FAILED: $_" 'ERROR' 'maintenance.log'
    exit 1
}
