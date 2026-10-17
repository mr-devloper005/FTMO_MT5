# Safe code update with automatic rollback (run as administrator).
#
#   from a git bundle (GitHub blocked):  .\deploy.ps1 -Bundle D:\ftmo-update.bundle [-Wheels D:\wheels]
#   from GitHub (later, when reachable): .\deploy.ps1 -FromGit
#   preview only, changes nothing:       .\deploy.ps1 -Bundle D:\ftmo-update.bundle -DryRun
#
# 1 preflight   clean checkout, bundle verified, new commit is a fast-forward of the running one
# 2 backup      full backup to C:\ftmo\backups\pre-deploy (research.db, state, RAG dump)
# 3 stop        HOLD flag on (health check stands down), service stopped, worker gone
# 4 update      fast-forward the code; reinstall packages only if the lock file changed; rag db-init
# 5 test        Windows-compat + operations + research tests
# 6 start       service started, health must answer within 3 minutes
# any failure in 4-6 -> code + packages rolled back to the previous commit and the service restarted.
# Data is never rolled back automatically (schema changes are additive); the pre-deploy backup is
# there for `python -m research_agent restore --from <backup> --replace` if ever needed.

param([string]$Bundle = $null,
      [switch]$FromGit,
      [string]$Branch = 'master',
      [string]$Wheels = $null,
      [switch]$Online,
      [switch]$SkipTests,
      [switch]$DryRun)
. "$PSScriptRoot\common.ps1"
Assert-Admin
if (-not $Bundle -and -not $FromGit) { throw 'give -Bundle <file> or -FromGit' }
$git = Find-Exe 'git.exe' @('C:\Program Files\Git\cmd\git.exe')
$lockRel = 'deploy/windows/requirements-research.lock.txt'
$lockFile = Join-Path $FtmoRoot 'deploy.lock'

function Git {
    param([string[]]$A)
    $ErrorActionPreference = 'Continue'                       # git writes progress to stderr
    $o = & $git -C $AppDir @A 2>&1 | ForEach-Object { "$_" }
    if ($LASTEXITCODE -ne 0) { throw "git $($A -join ' '): $($o -join ' ')" }
    return ($o -join "`n").Trim()
}

function Install-Packages {
    $a = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', (Join-Path $AppDir $lockRel))
    if ($Wheels) { $a += @('--no-index', '--find-links', $Wheels) }
    elseif (-not $Online) { throw 'the lock file changed: pass -Wheels <offline wheel folder> or -Online' }
    Invoke-Native -Exe $Python -Arguments $a -WorkDir $AppDir | Out-Null
}

function Stop-Research {
    Set-Content -Path $HoldFlag -Value "deploy $(Get-Date -Format s)" -Encoding ASCII
    $svc = Get-Service $ServiceName -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne 'Stopped') { Stop-Service $ServiceName -Force; (Get-Service $ServiceName).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(90)) }
    # NSSM kills the process tree; make sure no research process from this venv survived
    $left = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.ExecutablePath -eq $Python -and $_.CommandLine -match 'research_agent' }
    foreach ($p in $left) { Write-FtmoLog "stopping leftover research process $($p.ProcessId)" 'WARN' 'deploy.log'; Stop-Process -Id $p.ProcessId -Force }
}

function Start-Research {
    Start-Service $ServiceName
    $h = $null
    for ($i = 0; $i -lt 36 -and -not $h; $i++) { Start-Sleep -Seconds 5; $h = Get-Health }
    if (-not $h) { throw "the dashboard did not answer $HealthUrl within 3 minutes" }
    if ($h.status -eq 'down') { throw ("health is down: {0}" -f (($h.problems) -join '; ')) }
    return $h
}

if (Test-Path $lockFile) { throw "another deploy is running ($lockFile). Delete it only if you are sure none is." }
Set-Content -Path $lockFile -Value $PID -Encoding ASCII
try {
    # ---- 1. preflight ------------------------------------------------------------------------
    $dirty = Git @('status', '--porcelain', '--untracked-files=no')
    if ($dirty) { throw "the checkout has local changes - refusing to deploy over them:`n$dirty" }
    $prev = Git @('rev-parse', 'HEAD')
    if ($Bundle) {
        Invoke-Native -Exe $git -Arguments @('-C', $AppDir, 'bundle', 'verify', $Bundle) | Out-Null
        Git @('fetch', '--quiet', $Bundle, "+refs/heads/${Branch}:refs/remotes/incoming/$Branch") | Out-Null
    } else {
        Git @('fetch', '--quiet', 'origin', "+refs/heads/${Branch}:refs/remotes/incoming/$Branch") | Out-Null
    }
    $new = Git @('rev-parse', "refs/remotes/incoming/$Branch")
    if ($new -eq $prev) { Write-FtmoLog "already at $($prev.Substring(0, 10)) - nothing to deploy" 'INFO' 'deploy.log'; return }
    $code = Invoke-Native -Exe $git -Arguments @('-C', $AppDir, 'merge-base', '--is-ancestor', $prev, $new) -Ok @(0, 1)
    if ($code -ne 0) { throw "$($new.Substring(0, 10)) is not a fast-forward of the running $($prev.Substring(0, 10)) - refusing" }
    $log = Git @('log', '--oneline', "$prev..$new")
    $lockChanged = [bool](Git @('diff', '--name-only', $prev, $new, '--', $lockRel))
    Write-FtmoLog "deploy $($prev.Substring(0, 10)) -> $($new.Substring(0, 10)) (packages changed: $lockChanged)`n$log" 'INFO' 'deploy.log'
    if ($lockChanged -and -not $Wheels -and -not $Online) { throw 'the lock file changed: pass -Wheels <offline wheel folder> or -Online' }
    if ($DryRun) { Write-FtmoLog 'dry run - nothing changed' 'INFO' 'deploy.log'; return }

    # ---- 2. backup ---------------------------------------------------------------------------
    Invoke-Agent @('backup', '--dest', (Join-Path $BackupDir 'pre-deploy'), '--keep', '5') | Out-Null
    Write-FtmoLog 'pre-deploy backup done' 'INFO' 'deploy.log'

    # ---- 3. stop -----------------------------------------------------------------------------
    Stop-Research
    try {
        # ---- 4. update -----------------------------------------------------------------------
        Git @('merge', '--ff-only', '--quiet', $new) | Out-Null
        if ($lockChanged) { Install-Packages }
        Invoke-Rag @('db-init') | Out-Null
        # ---- 5. test -------------------------------------------------------------------------
        if (-not $SkipTests) {
            $env:PYTHONUTF8 = '1'
            Invoke-Native -Exe $Python -Arguments @('-m', 'pytest', '-q', '-p', 'no:cacheprovider', 'tests\test_windows_compat.py',
                'tests\test_ops.py', 'tests\test_research_agent.py') -WorkDir $AppDir | Out-Null
        }
        # ---- 6. start ------------------------------------------------------------------------
        $h = Start-Research
        Write-FtmoLog "DEPLOYED $($new.Substring(0, 10)) - health $($h.status)" 'INFO' 'deploy.log'
    } catch {
        $err = $_
        Write-FtmoLog "deploy failed: $err - ROLLING BACK to $($prev.Substring(0, 10))" 'ERROR' 'deploy.log'
        Stop-Research
        Git @('reset', '--hard', '--quiet', $prev) | Out-Null
        if ($lockChanged) { try { Install-Packages } catch { Write-FtmoLog "package rollback failed: $_" 'ERROR' 'deploy.log' } }
        $h = Start-Research
        Write-FtmoLog "ROLLED BACK to $($prev.Substring(0, 10)) - health $($h.status)" 'WARN' 'deploy.log'
        throw $err
    }
} finally {
    Remove-Item $HoldFlag -ErrorAction SilentlyContinue
    Remove-Item $lockFile -ErrorAction SilentlyContinue
}
