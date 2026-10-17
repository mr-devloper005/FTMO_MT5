# Remove the FTMO-Research service and its scheduled tasks (run as administrator).
# Never deletes data: C:\ftmo\app (code + research state), C:\ftmo\backups, logs and the
# PostgreSQL databases are left exactly as they are.

. "$PSScriptRoot\common.ps1"
Assert-Admin
Set-Content -Path $HoldFlag -Value 'uninstall' -Encoding ASCII
foreach ($t in @('HealthCheck', 'NightlyBackup', 'Maintenance')) {
    if (Get-ScheduledTask -TaskName $t -TaskPath $TaskFolder -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -TaskPath $TaskFolder -Confirm:$false
        Write-FtmoLog "removed scheduled task $TaskFolder$t"
    }
}
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    $nssm = Get-Nssm
    Invoke-Native -Exe $nssm -Arguments @('stop', $ServiceName) -Ok @(0, 1, 2, 3) | Out-Null
    Invoke-Native -Exe $nssm -Arguments @('remove', $ServiceName, 'confirm') | Out-Null
    Write-FtmoLog "removed service $ServiceName"
}
Remove-Item $HoldFlag -ErrorAction SilentlyContinue
Write-FtmoLog 'uninstalled. Data kept in C:\ftmo (app\research_agent\state, app\rag\state, backups) and PostgreSQL.'
