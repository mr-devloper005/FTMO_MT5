# Step 1 - install the prerequisites with winget (run as administrator, once).
#   powershell -ExecutionPolicy Bypass -File .\01-install-prereqs.ps1
#
# Installs: Git, Python 3.12 (64-bit), PostgreSQL 16, Visual Studio 2022 Build Tools (C++), NSSM.
# PostgreSQL opens its installer window so YOU choose the 'postgres' superuser password
# (keep it in your password manager; it is not stored anywhere by these scripts).
# No winget / no internet?  Install the same five from offline installers - see RUNBOOK_WINDOWS.md.

param([switch]$SkipBuildTools)
. "$PSScriptRoot\common.ps1"
Assert-Admin

if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    throw 'winget is not available. Install "App Installer" from the Microsoft Store, or use the offline installers (RUNBOOK_WINDOWS.md).'
}

function Install-Pkg {
    param([string]$Id, [string[]]$Extra = @())
    $ErrorActionPreference = 'Continue'
    $listed = & winget.exe list --id $Id --exact --accept-source-agreements 2>&1 | Out-String
    $ErrorActionPreference = 'Stop'
    if ($listed -match [regex]::Escape($Id)) { Write-FtmoLog "$Id already installed"; return }
    Write-FtmoLog "installing $Id"
    $a = @('install', '--id', $Id, '--exact', '--accept-package-agreements', '--accept-source-agreements') + $Extra
    $code = Invoke-Native -Exe 'winget.exe' -Arguments $a -Ok @(0, -1978335189)   # 0x8A15002B = already installed / no upgrade
    Write-FtmoLog "$Id done (exit $code)"
}

Install-Pkg 'Git.Git' @('--silent')
Install-Pkg 'Python.Python.3.12' @('--silent', '--override', '/quiet InstallAllUsers=1 PrependPath=1 Include_launcher=1')
Install-Pkg 'PostgreSQL.PostgreSQL.16' @('--interactive')
Install-Pkg 'NSSM.NSSM' @('--silent')
if (-not $SkipBuildTools) {
    # only needed to compile pgvector (02-build-pgvector.ps1); about 3-6 GB
    Install-Pkg 'Microsoft.VisualStudio.2022.BuildTools' @('--silent', '--override',
        '--quiet --wait --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended')
}

# Long paths: the price cache and pip wheels can exceed 260 characters.
$lp = 'HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem'
if ((Get-ItemProperty -Path $lp -Name LongPathsEnabled -ErrorAction SilentlyContinue).LongPathsEnabled -ne 1) {
    Set-ItemProperty -Path $lp -Name LongPathsEnabled -Value 1 -Type DWord
    Write-FtmoLog 'enabled Win32 long paths (takes effect for new processes)'
}

foreach ($d in @($FtmoRoot, $LogDir, $BackupDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
Write-FtmoLog 'prerequisites installed. Open a NEW PowerShell window (PATH changed) and continue with 02-build-pgvector.ps1'
