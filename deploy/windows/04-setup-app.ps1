# Step 4 - install the code, the Python environment and (optionally) restore the data from the Mac transfer.
#
# A. From the Mac transfer folder (USB; works without GitHub access):
#   powershell -ExecutionPolicy Bypass -File .\04-setup-app.ps1 -Transfer D:\ftmo-transfer-20261011-120000 -SecretsFile E:\.env.windows
# B. From a GitHub clone (fresh, empty research data; packages from PyPI):
#   git clone https://github.com/<owner>/FTMO_MT5.git C:\ftmo\app
#   powershell -ExecutionPolicy Bypass -File C:\ftmo\app\deploy\windows\04-setup-app.ps1 -SecretsFile C:\ftmo\secrets\.env.windows
#
# -Transfer     the folder written on the Mac by `python -m research_agent transfer-export`
#               (ftmo.bundle + research.db + state zips + ftmo_rag.dump + manifest.json [+ wheels\]).
#               Leave it out to install from the checkout already at C:\ftmo\app (B).
# -SecretsFile  the filtered .env.windows (research keys only) - carried separately, never committed.
# -Online       install Python packages from PyPI instead of the offline wheels\ folder (always so in B).
#
# Safe to re-run: an existing checkout is not overwritten and the restore refuses to replace data.

param([string]$Transfer = $null,
      [Parameter(Mandatory = $true)][string]$SecretsFile,
      [switch]$Online,
      [switch]$SkipTests)
. "$PSScriptRoot\common.ps1"
Assert-Admin

$git = Find-Exe 'git.exe' @('C:\Program Files\Git\cmd\git.exe')
if (-not $git) { throw 'git not found (01-install-prereqs.ps1)' }
$required = @($SecretsFile)
if ($Transfer) {
    $bundle = Join-Path $Transfer 'ftmo.bundle'
    $required += @($bundle, (Join-Path $Transfer 'manifest.json'))
} else {
    $Online = $true                                  # no transfer folder = no offline wheels
    if (-not (Test-Path (Join-Path $AppDir '.git'))) {
        throw "no -Transfer folder given and $AppDir is not a git checkout. Clone the repo there first: git clone <repo url> $AppDir"
    }
}
foreach ($f in $required) {
    if (-not (Test-Path $f)) { throw "missing $f" }
}
foreach ($d in @($FtmoRoot, $LogDir, $BackupDir)) { if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null } }

# ---- 1. code -------------------------------------------------------------------------------
if ($Transfer) {
    Invoke-Native -Exe $git -Arguments @('bundle', 'verify', $bundle) | Out-Null
    if (-not (Test-Path (Join-Path $AppDir '.git'))) {
        Write-FtmoLog "cloning the code from $bundle into $AppDir"
        Invoke-Native -Exe $git -Arguments @('clone', '--branch', 'master', $bundle, $AppDir) | Out-Null
        Invoke-Native -Exe $git -Arguments @('-C', $AppDir, 'remote', 'rename', 'origin', 'bundle') | Out-Null
    } else {
        Write-FtmoLog "$AppDir already has a checkout - leaving it (use deploy.ps1 to update code)"
    }
} else {
    Write-FtmoLog "installing from the existing checkout in $AppDir (no transfer folder: research data starts empty)"
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
    $py312 = Find-Python312
    if (-not $py312) { throw 'Python 3.12 not found (01-install-prereqs.ps1, or set FTMO_PYTHON)' }
    Invoke-Native -Exe $py312 -Arguments @('-m', 'venv', $VenvDir) | Out-Null
    Write-FtmoLog "created $VenvDir with $py312"
}
$lock = Join-Path $AppDir 'deploy\windows\requirements-research.lock.txt'
$pipArgs = @('-m', 'pip', 'install', '--disable-pip-version-check', '-r', $lock)
if (-not $Online) {                                  # only reachable with -Transfer (B forces -Online)
    $wheels = Join-Path $Transfer 'wheels'
    if (-not (Test-Path $wheels)) { throw "no offline wheels in $wheels - re-export with --wheels on the Mac, or pass -Online" }
    $pipArgs += @('--no-index', '--find-links', $wheels)
}
Write-FtmoLog 'installing Python packages (pinned lock file)'
Invoke-Native -Exe $Python -Arguments $pipArgs -WorkDir $AppDir | Out-Null
Invoke-Native -Exe $Python -Arguments @('-m', 'pip', 'check') -WorkDir $AppDir | Out-Null

# ---- 4. database schema + data -------------------------------------------------------------
Invoke-Rag @('db-init') | Out-Null
if ($Transfer) {
    $code = Invoke-Agent @('restore', '--from', $Transfer) -Ok @(0, 1, 3)
    if ($code -eq 3) { Write-FtmoLog 'restore refused: data already present on this PC (kept as is). Use restore --replace only on purpose.' 'WARN' }
    elseif ($code -ne 0) { throw 'restore failed - see the output above' }
} else {
    Invoke-Agent @('status') | Out-Null              # creates an empty research.db
}
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
