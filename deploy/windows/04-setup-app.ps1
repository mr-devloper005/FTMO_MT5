# Step 4 - install the code, the Python environment and restore the data from the Mac transfer.
#   powershell -ExecutionPolicy Bypass -File .\04-setup-app.ps1 -Transfer D:\ftmo-transfer-20261011-120000 -SecretsFile E:\.env.windows
#
# -Transfer     the folder written on the Mac by `python -m research_agent transfer-export`
#               (ftmo.bundle + research.db + state zips + ftmo_rag.dump + manifest.json [+ wheels\]).
# -SecretsFile  the filtered .env.windows (research keys only) - carried separately, e.g. a USB stick.
# -Online       install Python packages from PyPI instead of the offline wheels\ folder.
#
# Safe to re-run: an existing checkout is not overwritten and the restore refuses to replace data.

param([Parameter(Mandatory = $true)][string]$Transfer,
      [Parameter(Mandatory = $true)][string]$SecretsFile,
      [switch]$Online,
      [switch]$SkipTests)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$git = Find-Exe 'git.exe' @('C:\Program Files\Git\cmd\git.exe')
if (-not $git) { throw 'git not found (01-install-prereqs.ps1)' }
$bundle = Join-Path $Transfer 'ftmo.bundle'
foreach ($f in @($bundle, (Join-Path $Transfer 'manifest.json'), $SecretsFile)) {
    if (-not (Test-Path $f)) { throw "missing $f" }
}
foreach ($d in @($FtmoRoot, $LogDir, $BackupDir)) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null } }

# ---- 1. code -------------------------------------------------------------------------------
Invoke-Native -Exe $git -Arguments @('bundle', 'verify', $bundle) | Out-Null
if (-not (Test-Path (Join-Path $AppDir '.git'))) {
    Write-FtmoLog "cloning the code from $bundle into $AppDir"
    Invoke-Native -Exe $git -Arguments @('clone', '--branch', 'master', $bundle, $AppDir) | Out-Null
    Invoke-Native -Exe $git -Arguments @('-C', $AppDir, 'remote', 'rename', 'origin', 'bundle') | Out-Null
} else {
    Write-FtmoLog "$AppDir already has a checkout - leaving it (use deploy.ps1 to update code)"
}
Invoke-Native -Exe $git -Arguments @('-C', $AppDir, 'config', 'core.longpaths', 'true') | Out-Null

# ---- 2. secrets (research keys only) ---------------------------------------------------------
$secrets = Read-DotEnv $SecretsFile
foreach ($k in $secrets.Keys) {
    if ($k -match '^(MT5_|TT_|WEBULL_|IB_|TASTY|BROKER$|HUB_PORT$)') { throw "$SecretsFile contains broker key $k - refusing (research PC only)" }
}
if (-not (Test-Path $EnvFile)) {
    Copy-Item $SecretsFile $EnvFile
    Write-FtmoLog "secrets installed to $EnvFile"
} else {
    Write-FtmoLog "$EnvFile exists - not overwritten"
}
Protect-SecretFile $EnvFile
if (-not ($secrets['ANTHROPIC_API_KEY'] -or $secrets['CLAUDE_CODE_OAUTH_TOKEN'])) {
    Write-FtmoLog 'no ANTHROPIC_API_KEY / CLAUDE_CODE_OAUTH_TOKEN in the secrets: the Claude research agent will not run until one is added to .env' 'WARN'
}

# ---- 3. Python environment -----------------------------------------------------------------
if (-not (Test-Path $Python)) {
    $py = Find-Exe 'py.exe' @('C:\Windows\py.exe')
    if ($py) { Invoke-Native -Exe $py -Arguments @('-3.12', '-m', 'venv', $VenvDir) | Out-Null }
    else {
        $py312 = Find-Exe 'python.exe' @('C:\Program Files\Python312\python.exe')
        if (-not $py312) { throw 'Python 3.12 not found (01-install-prereqs.ps1)' }
        Invoke-Native -Exe $py312 -Arguments @('-m', 'venv', $VenvDir) | Out-Null
    }
    Write-FtmoLog "created $VenvDir"
}
$lock = Join-Path $AppDir 'deploy\windows\requirements-research.lock.txt'
$wheels = Join-Path $Transfer 'wheels'
$pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $lock)
if (-not $Online) {
    if (-not (Test-Path $wheels)) { throw "no offline wheels in $wheels - re-export with --wheels on the Mac, or pass -Online" }
    $pipArgs += @('--no-index', '--find-links', $wheels)
}
Write-FtmoLog 'installing Python packages (pinned lock file)'
Invoke-Native -Exe $Python -Arguments $pipArgs -WorkDir $AppDir | Out-Null
Invoke-Native -Exe $Python -Arguments @('-m', 'pip', 'check') -WorkDir $AppDir | Out-Null

# ---- 4. database schema + data -------------------------------------------------------------
Invoke-Rag @('db-init') | Out-Null
$code = Invoke-Agent @('restore', '--from', $Transfer) -Ok @(0, 1, 3)
if ($code -eq 3) { Write-FtmoLog 'restore refused: data already present on this PC (kept as is). Use restore --replace only on purpose.' 'WARN' }
elseif ($code -ne 0) { throw 'restore failed - see the output above' }
Invoke-Rag @('db-check') | Out-Null

# ---- 5. self-test ----------------------------------------------------------------------------
if (-not $SkipTests) {
    Write-FtmoLog 'running the Windows compatibility + operations tests'
    $env:PYTHONUTF8 = '1'
    Invoke-Native -Exe $Python -Arguments @('-m', 'pytest', '-q', '-p', 'no:cacheprovider',
        'tests\test_windows_compat.py', 'tests\test_ops.py', 'tests\test_research_agent.py') -WorkDir $AppDir | Out-Null
}
Invoke-Agent @('health') -Ok @(0, 1) | Out-Null
Write-FtmoLog 'app ready. Next: 05-install-services.ps1'
