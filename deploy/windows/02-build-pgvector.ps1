# Step 2 - compile and install pgvector 0.8.7 into PostgreSQL 16 (run as administrator, once).
#   powershell -ExecutionPolicy Bypass -File .\02-build-pgvector.ps1 -Source D:\ftmo-transfer\pgvector-0.8.7.zip
#
# -Source is the pgvector source (zip or folder). The Mac export ships it in the transfer folder
# because github.com may be blocked on this PC. Without -Source the script downloads the tag.

param([string]$Source = $null, [string]$Version = '0.8.7')
. "$PSScriptRoot\common.ps1"
Assert-Admin

$pgConfig = Join-Path $PgBin 'pg_config.exe'
if (-not (Test-Path $pgConfig)) { throw "PostgreSQL 16 not found at $PgBin (set PG_BIN). Run 01-install-prereqs.ps1 first." }
$pgRoot = Split-Path $PgBin -Parent
if (Test-Path (Join-Path $pgRoot "share\extension\vector--$Version.sql")) {
    Write-FtmoLog "pgvector $Version is already installed in $pgRoot"
    exit 0
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { throw 'Visual Studio Build Tools not found (vswhere.exe). Run 01-install-prereqs.ps1.' }
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'The C++ build tools workload (VCTools) is not installed.' }
$vcvars = Join-Path $vs 'VC\Auxiliary\Build\vcvars64.bat'

$work = Join-Path $env:TEMP "pgvector-build-$Version"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null
if (-not $Source) {
    $Source = Join-Path $work "pgvector-$Version.zip"
    Write-FtmoLog "downloading pgvector v$Version"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri "https://github.com/pgvector/pgvector/archive/refs/tags/v$Version.zip" -OutFile $Source -UseBasicParsing
}
if ((Get-Item $Source).PSIsContainer) {
    $src = $Source
} else {
    Expand-Archive -Path $Source -DestinationPath $work -Force
    $src = (Get-ChildItem $work -Directory | Where-Object { Test-Path (Join-Path $_.FullName 'Makefile.win') } | Select-Object -First 1).FullName
}
if (-not $src -or -not (Test-Path (Join-Path $src 'Makefile.win'))) { throw "no Makefile.win in $Source - is this the pgvector source?" }

Write-FtmoLog "building pgvector from $src against $pgRoot"
$cmd = 'call "{0}" && set "PGROOT={1}" && cd /d "{2}" && nmake /NOLOGO /F Makefile.win && nmake /NOLOGO /F Makefile.win install' -f $vcvars, $pgRoot, $src
Invoke-Native -Exe 'cmd.exe' -Arguments @('/c', $cmd) | Out-Null

if (-not (Test-Path (Join-Path $pgRoot "share\extension\vector--$Version.sql"))) { throw 'pgvector install did not produce the extension files.' }
Write-FtmoLog "pgvector $Version installed. Next: 03-setup-database.ps1"
