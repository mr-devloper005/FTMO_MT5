# FTMO Research Agent on a 24/7 Windows PC — runbook

What runs on the PC: the **AI Research dashboard + Agent Control Center** (`python -m research_agent ui`,
127.0.0.1:8602), the **24/7 research worker** it supervises, **RAG** (PostgreSQL 16 + pgvector, local
embedding model), plus scheduled health checks, nightly backups and daily maintenance.

What does **not** run on the PC: the broker hub (:8600), the MT5 bridge, any broker credentials.
Nothing here can place an order.

> Status: scripts are prepared and tested on macOS (see the end of this file). **Nothing has been
> deployed to the Windows PC yet.** Items that can only be proven on Windows are listed in
> [WINDOWS_VERIFICATION.md](WINDOWS_VERIFICATION.md).

## Layout on the PC

| Path | What |
|---|---|
| `C:\ftmo\app` | the code (git checkout from a bundle), `venv\`, `.env` (research keys only, ACL-locked) |
| `C:\ftmo\app\research_agent\state` | research.db, runs, reports, exports, price cache (as on the Mac) |
| `C:\ftmo\app\rag\state` | RAG raw downloads, FMP probe, embedding model (`models\`) |
| `C:\ftmo\logs` | `service.log` (dashboard), `health.log`, `backup.log`, `deploy.log`, `maintenance.log` |
| `C:\ftmo\backups` | nightly backups (14 kept) and `pre-deploy\` (5 kept) |
| `C:\ftmo\HOLD` | if present, the health check leaves the service alone |
| PostgreSQL 16 | databases `ftmo_rag` (+ `ftmo_rag_test` for tests), schema `rag` |

Services and tasks: Windows service **FTMO-Research** (NSSM, account `.\ftmo-svc`, delayed auto-start
after `postgresql-x64-16`); scheduled tasks `\FTMO\HealthCheck` (every 5 min), `\FTMO\NightlyBackup`
(02:30), `\FTMO\Maintenance` (03:30).

## 0. On the Mac — build the transfer

Commit first (only committed code travels), then:

```bash
deploy/windows/export-from-mac.sh /Volumes/FTMO-USB /Volumes/SECOND-USB
```

* `/Volumes/FTMO-USB/ftmo-transfer-YYYYMMDD-HHMMSS/`: `ftmo.bundle` (code + history), `research.db`,
  `research_state.zip`, `rag_state.zip` (incl. the 64 MB embedding model), `ftmo_rag.dump`,
  `manifest.json` (sha256 of every file + row counts), `wheels\` (offline packages),
  `pgvector-0.8.7.zip`, `windows-scripts\`. About 350 MB.
* `/Volumes/SECOND-USB/.env.windows`: research keys only. Broker keys (`MT5_*`, `TT_*`, `WEBULL_*`,
  `BROKER`, `HUB_PORT`) and Mac paths are filtered out. Keep it apart from the transfer folder.
  **Before step 4, add on the PC:** `ANTHROPIC_API_KEY=` (or `CLAUDE_CODE_OAUTH_TOKEN=` from
  `claude setup-token`) and `SEC_USER_AGENT="Company Name contact@company.com"` if SEC ingest is used.
  `POSTGRES_PASSWORD` must be non-empty on Windows.

## 1. On the PC — one-time setup (elevated PowerShell)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
cd D:\ftmo-transfer-YYYYMMDD-HHMMSS\windows-scripts
.\00-check-connectivity.ps1                 # REQUIRED hosts must be reachable (read-only check)
.\01-install-prereqs.ps1                    # Git, Python 3.12, PostgreSQL 16 (you pick the postgres password), Build Tools, NSSM
# open a NEW elevated PowerShell (PATH changed), cd back here
.\02-build-pgvector.ps1 -Source ..\pgvector-0.8.7.zip
.\03-setup-database.ps1 -SecretsFile E:\.env.windows
.\04-setup-app.ps1 -Transfer D:\ftmo-transfer-YYYYMMDD-HHMMSS -SecretsFile E:\.env.windows
.\05-install-services.ps1
```

Then open `http://127.0.0.1:8602/research` on the PC and press **Start** in the Agent Control Center
(the agent stays stopped until someone starts it; after that it survives reboots).

Offline installers (no winget / no internet): Git for Windows, python.org 3.12 x64 installer
(tick "Add to PATH", "for all users"), EDB PostgreSQL 16 installer, Visual Studio 2022 Build Tools
("Desktop development with C++"), nssm 2.24 (`win64\nssm.exe` into `C:\Program Files\nssm\win64`).

## Day-to-day operations

| Task | Command (elevated PowerShell unless noted) |
|---|---|
| Health (human) | `C:\ftmo\app\deploy\windows\healthcheck.ps1` or browse `http://127.0.0.1:8602/api/research/health` |
| Health (CLI) | `cd C:\ftmo\app; venv\Scripts\python -m research_agent health` (exit 0 ok / 1 degraded / 2 down) |
| Service | `Get-Service FTMO-Research`, `Restart-Service FTMO-Research` |
| Stop on purpose | `New-Item C:\ftmo\HOLD; Stop-Service FTMO-Research` … then `Start-Service FTMO-Research; Remove-Item C:\ftmo\HOLD` |
| Agent start/pause/stop | Control Center, or `venv\Scripts\python -m research_agent control start|pause|stop|status` |
| Logs | `Get-Content C:\ftmo\logs\service.log -Tail 50 -Wait`; worker: `research_agent\state\worker.log` |
| Backup now | `C:\ftmo\app\deploy\windows\backup.ps1` (`-Mirror \\nas\ftmo` or set `FTMO_BACKUP_MIRROR`) |
| Restore | `venv\Scripts\python -m research_agent restore --from C:\ftmo\backups\ftmo-backup-…` (refuses to overwrite; `--replace` backs up the current data first) |
| Disk | `venv\Scripts\python -m research_agent maintenance` (keeps 3 price-cache days ≈ 2.6 GB) |

Health statuses: **ok**; **degraded** = something needs attention but research data is safe (worker
not alive while the agent is on, consecutive failed runs, RAG database unreachable or pgvector
missing, < 5 GB free disk);
**down** = research.db missing/unreadable. The 5-minute task restarts a dead dashboard (after 2 failed
checks) and asks it to bring the worker back; it never deletes anything.

## Code updates (while GitHub is blocked on the PC)

Mac:

```bash
deploy/windows/make-update-bundle.sh /Volumes/FTMO-USB <commit running on the PC>
```

PC (elevated):

```powershell
C:\ftmo\app\deploy\windows\deploy.ps1 -Bundle E:\ftmo-update-<sha>.bundle -DryRun   # shows the commits, changes nothing
C:\ftmo\app\deploy\windows\deploy.ps1 -Bundle E:\ftmo-update-<sha>.bundle [-Wheels E:\wheels]
```

`deploy.ps1`: refuses a dirty checkout or a non-fast-forward → pre-deploy backup → HOLD + stop →
fast-forward code → packages only if the lock changed → `rag db-init` → tests → start → health within
3 minutes. Any failure after the stop rolls the code (and packages) back to the previous commit and
restarts the service. Data is never rolled back automatically: schema changes are additive, and the
pre-deploy backup is there if a manual restore is ever needed. Later, with GitHub reachable:
`git -C C:\ftmo\app remote add origin <repo url>` and `deploy.ps1 -FromGit`.

## Removing it

`C:\ftmo\app\deploy\windows\uninstall-services.ps1` removes the service and tasks and keeps all data.

## What was changed in the code for Windows

| Area | Problem on Windows | Fix |
|---|---|---|
| Worker liveness | `os.kill(pid, 0)` **terminates** the process on Windows | `oscompat.pid_alive` (psutil, never signals, checks the command line against PID reuse) |
| Stopping the worker | `os.killpg`/`os.getpgid` do not exist | `oscompat.terminate_tree` (psutil: children first, terminate then kill) |
| Detached worker | `start_new_session` is POSIX-only | `CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW` |
| Signals | `loop.add_signal_handler` raises `NotImplementedError` | falls back to `signal.signal` for SIGINT/SIGBREAK/SIGTERM |
| Child environment | scrubbed env dropped `SYSTEMROOT`, `TEMP`, `APPDATA`… (sockets/SSL break) | Windows essentials kept (case-insensitive); proxies + CA bundle kept; broker keys never passed |
| Text encoding | cp1252 default: 26 file reads/writes would garble or crash on `→ ≥ ✓` | every text I/O names UTF-8 (AST test guards it); `PYTHONUTF8=1`; UTF-8 stdout |
| Time zones | `zoneinfo` has no system database on Windows | `tzdata` pinned (tested with the OS database disabled) |
| Embedding model | fastembed caches in TEMP (cleaned by Windows) | `rag/state/models` (or `RAG_MODEL_CACHE`), shipped offline in the transfer |
| Worker log | parent leaked the log handle (locks the file); never rotated | handle closed after spawn; rotation at 20 MB × 3 |
| Disk | price cache grows ~860 MB/day forever | `maintenance` keeps 3 days (never today's) |
| Secrets for a service | services have no login shell | the agent loads research keys from `.env` itself (never broker keys, never overriding) |
| Packages | — | `requirements-research.lock.txt`: 88 pins, every one has a win_amd64 cp312 wheel (verified) |
| Ops | — | `health`, `maintenance`, `backup`, `restore`, `transfer-export` commands + `/api/research/health` |

## Tested on the Mac

* `tests/test_windows_compat.py` (16), `tests/test_ops.py` (9), `tests/test_windows_scripts.py` (36,
  incl. PowerShell 7 parsing of every script and the helpers against a live dashboard), plus the
  existing research and RAG suites.
* Real-data rehearsal: export of the live data (research.db 40,914 tests / 41 runs; RAG 45 documents,
  2,965 chunks + embeddings) → restore into an empty state folder and `ftmo_rag_test` → every row
  count identical, checksums verified, a second restore refused, the embedding model loads **offline**
  from the restored copy and reproduces the stored embeddings (cosine 1.0, 25 random chunks).
* The step-3 SQL ran twice against PostgreSQL 16 + pgvector 0.8.7 (idempotent; a password with quotes
  logs in), then the throwaway role/databases were dropped.
* PSScriptAnalyzer 1.23 with the 5.1 syntax-compatibility rule: no parse errors; remaining warnings are
  intentional (`Write-Host` in interactive scripts, the random service password handed to NSSM).
