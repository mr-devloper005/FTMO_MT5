# Nightly backup - run by the scheduled task \FTMO\NightlyBackup (02:30, as SYSTEM).
#   powershell -ExecutionPolicy Bypass -File .\backup.ps1 [-Keep 14] [-Mirror E:\ftmo-backups]
#
# research.db (online SQLite backup), research reports/runs/exports, RAG state and a pg_dump of
# the rag schema, with a sha256 manifest. Old backups beyond -Keep are rotated. -Mirror copies
# the newest backup to a second disk / network share (recommended: not the same physical disk).

param([int]$Keep = 14, [string]$Mirror = $env:FTMO_BACKUP_MIRROR)
. "$PSScriptRoot\common.ps1"
try {
    Invoke-Agent @('backup', '--dest', $BackupDir, '--keep', [string]$Keep) | Out-Null
    $latest = Get-ChildItem $BackupDir -Directory -Filter 'ftmo-backup-*' | Sort-Object Name | Select-Object -Last 1
    Write-FtmoLog "backup ok: $($latest.FullName)" 'INFO' 'backup.log'
    if ($Mirror) {
        $dst = Join-Path $Mirror $latest.Name
        $code = Invoke-Native -Exe 'robocopy.exe' -Arguments @($latest.FullName, $dst, '/E', '/R:2', '/W:10', '/NP', '/NFL', '/NDL') -Ok @(0, 1, 2, 3)
        Write-FtmoLog "mirrored to $dst (robocopy $code)" 'INFO' 'backup.log'
        Get-ChildItem $Mirror -Directory -Filter 'ftmo-backup-*' | Sort-Object Name -Descending | Select-Object -Skip $Keep |
            ForEach-Object { Remove-Item $_.FullName -Recurse -Force }
    }
} catch {
    Write-FtmoLog "BACKUP FAILED: $_" 'ERROR' 'backup.log'
    exit 1
}
