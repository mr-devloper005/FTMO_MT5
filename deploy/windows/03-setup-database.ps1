# Step 3 - create the RAG role and databases (ftmo_rag + ftmo_rag_test) with pgvector.
#   powershell -ExecutionPolicy Bypass -File .\03-setup-database.ps1 -SecretsFile E:\.env.windows
#
# Reads POSTGRES_DB / POSTGRES_USER / POSTGRES_PASSWORD from the secrets file (never printed).
# Asks for the 'postgres' superuser password chosen in the PostgreSQL installer.
# Idempotent: existing role/databases are kept (the password is reset to the one in the file).

param([Parameter(Mandatory = $true)][string]$SecretsFile)
. "$PSScriptRoot\common.ps1"

$cfg = Read-DotEnv $SecretsFile
foreach ($k in @('POSTGRES_DB', 'POSTGRES_USER', 'POSTGRES_PASSWORD')) {
    if (-not $cfg.ContainsKey($k) -or -not $cfg[$k]) { throw "$k is missing in $SecretsFile (a password is required on Windows)" }
}
$db = $cfg['POSTGRES_DB']; $user = $cfg['POSTGRES_USER']
$testDb = if ($cfg['POSTGRES_TEST_DB']) { $cfg['POSTGRES_TEST_DB'] } else { "${db}_test" }
foreach ($n in @($db, $user, $testDb)) { if ($n -notmatch '^[a-z_][a-z0-9_]{0,62}$') { throw "unsafe PostgreSQL name: $n" } }
if (-not $testDb.EndsWith('_test')) { throw 'the test database name must end in _test' }

$psql = Join-Path $PgBin 'psql.exe'
if (-not (Test-Path $psql)) { throw "psql.exe not found in $PgBin" }
$svc = Get-Service -Name $PgService -ErrorAction SilentlyContinue
if (-not $svc) { throw "PostgreSQL service $PgService not found" }
if ($svc.StartType -ne 'Automatic') { Set-Service -Name $PgService -StartupType Automatic }
if ($svc.Status -ne 'Running') { Start-Service $PgService }

$sec = Read-Host -AsSecureString "Password of the PostgreSQL superuser 'postgres'"
$env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
try {
    $ErrorActionPreference = 'Continue'      # psql NOTICEs on stderr are not failures; exit codes are checked
    # The role password travels in an environment variable read by psql's \getenv, and psql
    # variables (:'pw') quote it safely - it never appears on a command line or in the output.
    $env:FTMO_DB_PW = $cfg['POSTGRES_PASSWORD']
    $sql = @"
\getenv pw FTMO_DB_PW
SELECT format('CREATE ROLE %I LOGIN', :'usr') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'usr') \gexec
SELECT format('ALTER ROLE %I WITH LOGIN PASSWORD %L', :'usr', :'pw') \gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'db', :'usr') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db') \gexec
SELECT format('CREATE DATABASE %I OWNER %I ENCODING ''UTF8''', :'tdb', :'usr') WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'tdb') \gexec
"@
    $sql | & $psql -X -q -v ON_ERROR_STOP=1 -h localhost -U postgres -d postgres -v "usr=$user" -v "db=$db" -v "tdb=$testDb"
    if ($LASTEXITCODE -ne 0) { throw 'creating the role/databases failed' }
    foreach ($d in @($db, $testDb)) {
        & $psql -X -q -v ON_ERROR_STOP=1 -h localhost -U postgres -d $d -c 'CREATE EXTENSION IF NOT EXISTS vector;'
        if ($LASTEXITCODE -ne 0) { throw "CREATE EXTENSION vector failed in $d (is pgvector installed? 02-build-pgvector.ps1)" }
    }
} finally {
    Remove-Item Env:\PGPASSWORD, Env:\FTMO_DB_PW -ErrorAction SilentlyContinue
}
Write-FtmoLog "databases ready: $db, $testDb (owner $user, pgvector enabled). Next: 04-setup-app.ps1"
