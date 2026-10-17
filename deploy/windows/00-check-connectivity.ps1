# Step 0 - can this PC reach what the research agent needs?  Read-only; safe to run any time.
#   powershell -ExecutionPolicy Bypass -File .\00-check-connectivity.ps1
# Exit code 0 = every REQUIRED host reachable, 1 = at least one required host blocked.

. "$PSScriptRoot\common.ps1"
$ErrorActionPreference = 'Continue'

$targets = @(
    @{ Host = 'api.anthropic.com';             Need = 'REQUIRED'; Why = 'Claude research agent (Agent SDK / CLI)' },
    @{ Host = 'financialmodelingprep.com';     Need = 'REQUIRED'; Why = 'FMP fundamentals + transcripts (RAG)' },
    @{ Host = 'query1.finance.yahoo.com';      Need = 'REQUIRED'; Why = 'daily prices (yfinance)' },
    @{ Host = 'query2.finance.yahoo.com';      Need = 'REQUIRED'; Why = 'daily prices (yfinance)' },
    @{ Host = 'fc.yahoo.com';                  Need = 'REQUIRED'; Why = 'yfinance cookie/crumb' },
    @{ Host = 'www.nasdaqtrader.com';          Need = 'REQUIRED'; Why = 'NASDAQ symbol directory (universe)' },
    @{ Host = 'www.sec.gov';                   Need = 'optional'; Why = 'SEC filings ingest (rag ingest)' },
    @{ Host = 'data.sec.gov';                  Need = 'optional'; Why = 'SEC submissions API (rag ingest)' },
    @{ Host = 'sheets.googleapis.com';         Need = 'optional'; Why = 'Google Sheets report sync' },
    @{ Host = 'oauth2.googleapis.com';         Need = 'optional'; Why = 'Google Sheets service-account auth' },
    @{ Host = 'pypi.org';                      Need = 'setup';    Why = 'pip install (not needed with the offline wheelhouse)' },
    @{ Host = 'files.pythonhosted.org';        Need = 'setup';    Why = 'pip install (not needed with the offline wheelhouse)' },
    @{ Host = 'huggingface.co';                Need = 'setup';    Why = 'embedding model (not needed: model ships in the transfer)' },
    @{ Host = 'github.com';                    Need = 'later';    Why = 'git pull / CI runner (code ships as a git bundle meanwhile)' },
    @{ Host = 'region1.v2.argotunnel.com';     Need = 'later';    Why = 'Cloudflare Tunnel (dashboard access from outside)' }
)

$proxy = [System.Net.WebRequest]::GetSystemWebProxy().GetProxy([Uri]'https://api.anthropic.com')
if ($proxy.Host -ne 'api.anthropic.com') { Write-Host "System proxy in use: $proxy (set HTTPS_PROXY in the service if needed)" }

$failedRequired = 0
$rows = foreach ($t in $targets) {
    $dns = $false; $tcp = $false; $https = '-'
    try { [System.Net.Dns]::GetHostAddresses($t.Host) | Out-Null; $dns = $true } catch { }
    if ($dns) {
        try {
            $c = New-Object System.Net.Sockets.TcpClient
            $iar = $c.BeginConnect($t.Host, 443, $null, $null)
            $tcp = $iar.AsyncWaitHandle.WaitOne(5000) -and $c.Connected
            $c.Close()
        } catch { $tcp = $false }
    }
    if ($tcp) {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $r = Invoke-WebRequest -Uri ("https://{0}/" -f $t.Host) -Method Head -TimeoutSec 10 -UseBasicParsing -MaximumRedirection 0 -ErrorAction Stop
            $https = [string]$r.StatusCode
        } catch {
            if ($_.Exception.Response) { $https = [string][int]$_.Exception.Response.StatusCode } else { $https = 'TLS/HTTP error' }
        }
    }
    $ok = $dns -and $tcp -and ($https -notmatch 'error')
    if (-not $ok -and $t.Need -eq 'REQUIRED') { $failedRequired++ }
    [pscustomobject]@{ Host = $t.Host; Need = $t.Need; DNS = $dns; TCP443 = $tcp; HTTPS = $https;
                       Result = $(if ($ok) { 'OK' } else { 'BLOCKED' }); Why = $t.Why }
}
$rows | Format-Table -AutoSize | Out-String -Width 220 | Write-Host
# any HTTP status (200/301/403/404) proves the network path works; only DNS/TCP/TLS failures are blockers
if ($failedRequired -gt 0) {
    Write-FtmoLog "$failedRequired REQUIRED host(s) blocked - ask IT to allow them (HTTPS 443) before going live" 'ERROR'
    exit 1
}
Write-FtmoLog 'connectivity: every required host reachable'
exit 0
