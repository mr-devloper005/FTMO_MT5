# Setting up the research PC with Claude Code

How to install FTMO_MT5's research side on the Windows PC by cloning from GitHub and letting a
Claude Code session on that PC run the install. The full reference is
[RUNBOOK_WINDOWS.md](RUNBOOK_WINDOWS.md); this page is the short path.

**What the PC runs:** the AI research dashboard and Agent Control Center (127.0.0.1:8602), the 24/7
research worker, the company-knowledge RAG (PostgreSQL + pgvector) and the management-credibility
score. **What it never runs:** the broker hub (:8600), the MT5 bridge, the live trading loop, or any
broker credential. Nothing on this PC can place an order.

## What you do (a person, at the PC or over AnyDesk)

1. **Install Claude Code** on the PC (desktop app or CLI) and sign in.
2. **Give the PC access to the private GitHub repo**, for example `winget install GitHub.cli` then
   `gh auth login`, or a fine-grained read-only token.
3. **Clone to the fixed location** (the scripts expect it there):
   ```powershell
   git clone https://github.com/mr-devloper005/FTMO_MT5.git C:\ftmo\app
   ```
4. **Create the secrets file** `C:\ftmo\secrets\.env.windows`. Never commit it, never paste its values
   into a chat. Research keys only:
   ```
   POSTGRES_HOST=localhost
   POSTGRES_PORT=5432
   POSTGRES_DB=ftmo_rag
   POSTGRES_USER=ftmo_rag
   POSTGRES_PASSWORD=<choose one; required on Windows>
   FMP_API_KEY=<from the Mac .env>
   ANTHROPIC_API_KEY=<or CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`>
   SEC_USER_AGENT="Company Name contact@company.com"
   ```
   Do **not** add `MT5_*`, `TT_*`, `WEBULL_*`, `BROKER` or `HUB_PORT`. Step 4 refuses a file that has them.
5. **Open Claude Code in `C:\ftmo\app`** as administrator (the install needs elevation) and paste the
   message below.
6. **Be at the keyboard for the prompts only you can answer:** Windows administrator (UAC) prompts,
   the PostgreSQL superuser password you choose in step 01, and approving each command Claude runs.

## Message to paste into Claude Code on the PC

```text
Set up this repo as the FTMO research PC by following deploy/windows/SETUP_WITH_CLAUDE_CODE.md and
deploy/windows/RUNBOOK_WINDOWS.md (route B: install from this GitHub clone, no transfer folder).
Run the steps in order in an elevated PowerShell, stop at the first failure and show me the error:
  .\deploy\windows\00-check-connectivity.ps1
  .\deploy\windows\01-install-prereqs.ps1        (then reopen an elevated shell)
  .\deploy\windows\02-build-pgvector.ps1
  .\deploy\windows\03-setup-database.ps1 -SecretsFile C:\ftmo\secrets\.env.windows
  .\deploy\windows\04-setup-app.ps1 -SecretsFile C:\ftmo\secrets\.env.windows
  .\deploy\windows\05-install-services.ps1
Then run deploy\windows\healthcheck.ps1 and work through deploy/windows/WINDOWS_VERIFICATION.md,
reporting each item as passed or failed. Rules: never print or echo secret values; never add broker
credentials; never start the broker hub, the MT5 bridge or ftmo_mt5.main (CLAUDE.md, TRADING MODE).
I will type every password myself when a script asks.
```

## Research data

A GitHub clone starts with **empty research data** (the research database, reports and RAG documents
are not in git). To bring the Mac's data across, make a transfer folder on the Mac
(`deploy/windows/export-from-mac.sh`), copy it to the PC (USB or AnyDesk file transfer), and run
step 4 with `-Transfer <folder>` instead (route A in the runbook). You can also start empty and let
the 24/7 agent build its own history.

## After it is running

- Dashboard: `http://127.0.0.1:8602/research` on the PC; press **Start** in the Agent Control Center.
- Health: `C:\ftmo\app\deploy\windows\healthcheck.ps1`, or `venv\Scripts\python -m research_agent health`.
- Code updates from GitHub: `C:\ftmo\app\deploy\windows\deploy.ps1 -FromGit -Online` (backs up first,
  stops the service, fast-forwards, runs the tests, restarts; rolls the code back on any failure).
