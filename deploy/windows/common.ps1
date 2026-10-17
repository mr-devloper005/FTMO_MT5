# Shared settings and helpers for the FTMO research PC scripts. Dot-source it:  . "$PSScriptRoot\common.ps1"
# Windows PowerShell 5.1 compatible. Keep this file ASCII (5.1 reads BOM-less scripts as ANSI).
#
# Research agent only: the broker hub (:8600), the MT5 bridge and broker credentials are never
# installed, started or copied by these scripts.

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }   # python prints UTF-8 (PYTHONUTF8=1)

# ---- layout (override with environment variables before running, if needed) ------------------
$FtmoRoot    = if ($env:FTMO_ROOT) { $env:FTMO_ROOT } else { 'C:\ftmo' }
$AppDir      = Join-Path $FtmoRoot 'app'                 # git checkout (code)
$VenvDir     = Join-Path $AppDir 'venv'
$Python      = Join-Path $VenvDir 'Scripts\python.exe'
$LogDir      = Join-Path $FtmoRoot 'logs'
$BackupDir   = Join-Path $FtmoRoot 'backups'
$EnvFile     = Join-Path $AppDir '.env'                  # research keys only (from .env.windows)
$ServiceName = 'FTMO-Research'
$UiPort      = 8602                                      # bound to 127.0.0.1 only
$HealthUrl   = "http://127.0.0.1:$UiPort/api/research/health"
$PgService   = if ($env:PG_SERVICE) { $env:PG_SERVICE } else { 'postgresql-x64-16' }   # e.g. postgresql-x64-17
$PgBin       = if ($env:PG_BIN) { $env:PG_BIN } else { 'C:\Program Files\PostgreSQL\16\bin' }
$ServiceUser = 'ftmo-svc'                                # local, non-admin account the service runs as
$TaskFolder  = '\FTMO\'
$HoldFlag    = Join-Path $FtmoRoot 'HOLD'               # present = health check leaves the service alone (deploys, manual work)

function Write-FtmoLog {
    param([string]$Message, [string]$Level = 'INFO', [string]$File = 'ops.log')
    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $path = Join-Path $LogDir $File
    if ((Test-Path $path) -and ((Get-Item $path).Length -gt 10MB)) {
        Move-Item -Path $path -Destination "$path.1" -Force          # one generation is enough for ops logs
    }
    Add-Content -Path $path -Value $line -Encoding UTF8
}

function Assert-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    $p = New-Object Security.Principal.WindowsPrincipal($id)
    if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run this script from an elevated PowerShell (Run as administrator).'
    }
}

function Find-Exe {
    param([string]$Name, [string[]]$Guesses = @())
    $cmd = Get-Command $Name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($g in $Guesses) { if (Test-Path $g) { return $g } }
    return $null
}

# A Python 3.12 interpreter: $env:FTMO_PYTHON, then the py launcher, then python.exe on PATH, then the
# python.org default. Each candidate is asked for its version, so a launcher without 3.12 is skipped.
function Find-Python312 {
    $cands = @()
    if ($env:FTMO_PYTHON) { $cands += $env:FTMO_PYTHON }
    $py = Find-Exe 'py.exe' @('C:\Windows\py.exe')
    if ($py) {
        $ErrorActionPreference = 'Continue'
        $exe = & $py -3.12 -c 'import sys; print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $cands += "$exe".Trim() }
    }
    $onPath = Find-Exe 'python.exe'
    if ($onPath) { $cands += $onPath }
    $cands += 'C:\Program Files\Python312\python.exe'
    foreach ($c in $cands) {
        if (-not (Test-Path $c)) { continue }
        $ErrorActionPreference = 'Continue'
        $v = & $c -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null
        if ($LASTEXITCODE -eq 0 -and "$v".Trim() -eq '3.12') { return $c }
    }
    return $null
}

function Get-Nssm {
    $n = Find-Exe 'nssm.exe' @('C:\Program Files\nssm\win64\nssm.exe', 'C:\ProgramData\chocolatey\bin\nssm.exe',
                              "$env:LOCALAPPDATA\Microsoft\WinGet\Links\nssm.exe")
    if (-not $n) { throw 'nssm.exe not found. Run 01-install-prereqs.ps1 (winget install NSSM.NSSM) or put nssm.exe on PATH.' }
    return $n
}

# Run a native program; throw on a non-zero exit code unless it is in -Ok. Returns the exit code.
function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [int[]]$Ok = @(0), [string]$WorkDir = $null)
    # PowerShell 5.1 turns a native program's stderr into errors under 'Stop'; judge by exit code only.
    if (-not (Get-Command $Exe -ErrorAction SilentlyContinue)) { throw "program not found: $Exe" }
    $ErrorActionPreference = 'Continue'
    $code = $null
    $old = Get-Location
    try {
        if ($WorkDir) { Set-Location $WorkDir }
        & $Exe @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host     # shown, never returned
        $code = $LASTEXITCODE
    } finally { Set-Location $old }
    if ($null -eq $code -or $Ok -notcontains $code) { throw ('{0} {1} failed with exit code {2}' -f $Exe, ($Arguments -join ' '), $code) }
    return $code
}

# The research agent CLI, run from the app folder with UTF-8 I/O (same as the service).
function Invoke-Agent {
    param([string[]]$Arguments, [int[]]$Ok = @(0))
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PG_BIN = $PgBin
    return Invoke-Native -Exe $Python -Arguments (@('-m', 'research_agent') + $Arguments) -Ok $Ok -WorkDir $AppDir
}

function Invoke-Rag {
    param([string[]]$Arguments, [int[]]$Ok = @(0))
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    return Invoke-Native -Exe $Python -Arguments (@('-m', 'rag') + $Arguments) -Ok $Ok -WorkDir $AppDir
}

# Read KEY=VALUE pairs from a .env file without printing them.
function Read-DotEnv {
    param([string]$Path)
    $out = @{}
    if (-not (Test-Path $Path)) { return $out }
    foreach ($line in [IO.File]::ReadAllLines($Path, [Text.Encoding]::UTF8)) {
        $t = $line.Trim()
        if ($t -eq '' -or $t.StartsWith('#') -or -not $t.Contains('=')) { continue }
        $i = $t.IndexOf('=')
        $out[$t.Substring(0, $i).Trim()] = $t.Substring($i + 1).Trim().Trim('"').Trim("'")
    }
    return $out
}

# Only the owner account, the service account, SYSTEM and Administrators may read a secrets file.
function Protect-SecretFile {
    param([string]$Path, [string]$ExtraUser = $null)
    $acl = @($Path, '/inheritance:r', '/grant:r', '*S-1-5-18:F', '/grant:r', '*S-1-5-32-544:F',
              '/grant:r', ('{0}:F' -f $env:USERNAME))
    if ($ExtraUser) { $acl += @('/grant:r', ('{0}:R' -f $ExtraUser)) }
    Invoke-Native -Exe 'icacls.exe' -Arguments $acl | Out-Null
}

function Get-Health {
    try {
        $r = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 20 -UseBasicParsing
        return $r
    } catch {
        return $null
    }
}
