# Exercises deploy/windows/common.ps1 helpers with PowerShell 7 on any OS (run by
# tests/test_windows_scripts.py when pwsh is available). Prints PASS lines; throws on failure.
param([string]$Root, [string]$Py, [string]$HealthUrlOverride = '')
New-Item -ItemType Directory -Path $Root -Force | Out-Null
$env:FTMO_ROOT = $Root
. (Join-Path $PSScriptRoot '..\deploy\windows\common.ps1')

function Check { param([bool]$Cond, [string]$Name) if (-not $Cond) { throw "FAIL $Name" } ; Write-Output "PASS $Name" }

# Read-DotEnv: quotes stripped, comments and blank lines ignored, values never echoed
$envFile = Join-Path $Root 'test.env'
[IO.File]::WriteAllText($envFile, "# comment`nFMP_API_KEY=`"abc`"`n`nPOSTGRES_PASSWORD='p=w'`nSEC_USER_AGENT=Co x@y.io`n", [Text.Encoding]::UTF8)
$e = Read-DotEnv $envFile
Check ($e['FMP_API_KEY'] -eq 'abc' -and $e['POSTGRES_PASSWORD'] -eq 'p=w' -and $e['SEC_USER_AGENT'] -eq 'Co x@y.io' -and $e.Count -eq 3) 'Read-DotEnv'

# Invoke-Native returns only the exit code (program output goes to the host), throws on a bad code
$code = Invoke-Native -Exe $Py -Arguments @('-c', 'print("noise"); import sys; sys.stderr.write("warn\n")')
Check ($code -is [int] -and $code -eq 0) 'Invoke-Native returns only the exit code, stderr is not fatal'
$code = Invoke-Native -Exe $Py -Arguments @('-c', 'raise SystemExit(3)') -Ok @(0, 3)
Check ($code -eq 3) 'Invoke-Native accepts listed exit codes'
$threw = $false
try { Invoke-Native -Exe $Py -Arguments @('-c', 'raise SystemExit(2)') | Out-Null } catch { $threw = $true }
Check $threw 'Invoke-Native throws on an unexpected exit code'
$threw = $false
try { Invoke-Native -Exe (Join-Path $Root 'missing.exe') -Arguments @() | Out-Null } catch { $threw = $true }
Check $threw 'Invoke-Native throws when the program is missing'

# Write-FtmoLog: UTF-8 file in the log folder, rotated past 10 MB
Write-FtmoLog 'arrow -> ok' 'INFO' 'smoke.log' | Out-Null
$log = Join-Path $LogDir 'smoke.log'
Check ((Get-Content $log -Raw) -match 'arrow -> ok') 'Write-FtmoLog writes'
$fs = [IO.File]::Open($log, 'Open', 'ReadWrite'); $fs.SetLength(11MB); $fs.Close()
Write-FtmoLog 'after rotation' 'INFO' 'smoke.log' | Out-Null
Check ((Test-Path "$log.1") -and (Get-Item $log).Length -lt 1KB) 'Write-FtmoLog rotates'

# Get-Health: $null when nothing listens, parsed JSON from the real dashboard otherwise
$HealthUrl = 'http://127.0.0.1:9/api/research/health'
Check ($null -eq (Get-Health)) 'Get-Health is null when the dashboard is down'
if ($HealthUrlOverride) {
    $HealthUrl = $HealthUrlOverride
    $h = $null
    for ($i = 0; $i -lt 30 -and -not $h; $i++) { Start-Sleep -Milliseconds 500; $h = Get-Health }
    Check ($h -and $h.status -in @('ok', 'degraded', 'down') -and $h.checks.research_db) 'Get-Health reads the live health endpoint'
}
Write-Output 'ALL PASS'
