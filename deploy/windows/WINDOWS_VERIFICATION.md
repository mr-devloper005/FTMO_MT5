# What still has to be proven on the Windows PC

Everything below was designed for Windows and tested as far as macOS allows, but needs a real
Windows run before the PC is trusted 24/7. Do them in order during the first install; each line
says how to check it and what to do if it fails.

## Blockers (must be resolved before go-live)

| # | Item | Why it cannot be proven on the Mac | Check on the PC | If it fails |
|---|---|---|---|---|
| B1 | **Claude credential for the service** | The Mac uses a `claude auth login` session in the macOS keychain; that does not travel and a service account has no interactive login | `.env` has `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; Control Center → **Run now** completes an agent run | Generate a token (`claude setup-token`) or an API key; never copy the Mac keychain |
| B2 | **Outbound HTTPS** | Different network | `00-check-connectivity.ps1` exits 0 | Ask IT to allow the REQUIRED hosts on 443 (list in the script) |
| B3 | **Claude CLI on Windows** | The Agent SDK runs the bundled `claude.exe`; on Windows it may require Git Bash | `CLAUDE_CODE_GIT_BASH_PATH` is set by 05 when Git is installed; a manual `venv\Scripts\python -m research_agent agent` run works | Install Git for Windows; set the path in NSSM `AppEnvironmentExtra` |
| B4 | **pgvector build** | Needs MSVC `nmake` against the Windows PostgreSQL | `02-build-pgvector.ps1` ends with "installed"; `rag db-check` shows `pgvector 0.8.7` | Run from an elevated shell; check `PG_BIN`; Build Tools "Desktop development with C++" |
| B5 | **Service account rights** | Windows-only | `Get-Service FTMO-Research` is Running after 05; `service.log` has no "access denied" | NSSM grants "Log on as a service" when setting `ObjectName`; if a domain policy removes it, grant it via `secpol.msc` or run as a dedicated domain service account |

## Behaviour to observe in the first week

| # | Item | Expected | How to check |
|---|---|---|---|
| W1 | Reboot survival | PostgreSQL then FTMO-Research start by themselves (delayed auto-start); the worker comes back if the agent was on | Reboot; `healthcheck.ps1` → ok within ~3 min; `Control Center` shows the worker alive |
| W2 | Service stop | NSSM sends Ctrl+C, then kills the process tree; the worker (own process group, no console) is killed with it; on next start its run is marked interrupted and the schedule continues | `Stop-Service`; no `python.exe` with `research_agent` left (`Get-CimInstance Win32_Process`); next start resumes |
| W3 | Ctrl+C / SIGBREAK in a console | `python -m research_agent worker` in a console stops cleanly on Ctrl+C / Ctrl+Break | Manual |
| W4 | Proactor event loop + SDK subprocess | Agent SDK starts `claude.exe` under the default Windows event loop | A full `agent` run (B1) |
| W5 | Long paths | No "path too long" errors in pip or the price cache | 01 enables `LongPathsEnabled`; `git config core.longpaths true` set by 04 |
| W6 | Hugging Face cache without symlinks | Model loads from `rag\state\models` offline (the transfer ships resolved files, no symlinks) | `venv\Scripts\python -m rag search "HBM demand" ` works with networking to huggingface.co blocked |
| W7 | Health check task | Every 5 min, logs in `health.log`; restarts a hung dashboard after 2 failures; respects `C:\ftmo\HOLD` | Stop the service without HOLD → task restarts it within 5 min; with HOLD → it doesn't |
| W8 | Scheduled task repetition | `-RepetitionInterval` without a duration repeats indefinitely on Windows 10/11 / Server 2016+ | Task Scheduler → \FTMO\HealthCheck → "Repeat every 5 minutes indefinitely" |
| W9 | Nightly backup | `C:\ftmo\backups\ftmo-backup-*` appears each night, 14 kept; pg_dump found at `PG_BIN` | `backup.log`; restore one into scratch folders monthly: `python -m research_agent --state C:\tmp\rs restore --from <backup> --no-rag-db --rag-state C:\tmp\rg` (live data untouched) |
| W10 | Log rotation | `service.log` rotates at 10 MB (NSSM online rotation); `worker.log` at 20 MB when the worker restarts | Sizes in `C:\ftmo\logs` stay bounded |
| W11 | Antivirus | Defender may slow or lock `research.db`/price cache | If backups or runs stall, exclude `C:\ftmo\app\research_agent\state` and `C:\ftmo\backups` from real-time scanning (IT decision) |
| W12 | Sleep / updates | A 24/7 PC must not sleep; Windows Update reboots are survived via W1 | Power plan "never sleep"; set active hours / maintenance window |
| W13 | Time zone | Reports show US market dates correctly (tzdata ships with the venv) | Compare a report date with the Mac |
| W14 | Console encoding | `research_agent leaderboard` shows `→ ≥ ✓` correctly in PowerShell 5.1 | Manual; logs are always UTF-8 regardless |

## Later (not part of this preparation)

* Remote access to the dashboard: Cloudflare Tunnel + Cloudflare Access in front of 127.0.0.1:8602.
  The dashboard has no login of its own, so it must never be exposed without Access.
* GitHub-driven deploys: needs github.com reachable from the PC (self-hosted runner, because the repo
  is private) — `deploy.ps1 -FromGit` is ready for it.
* The Vercel frontend hookup (open question from the deployment plan).
