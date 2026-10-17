# Prompt for Claude Code on the Windows PC — 24/7 + Vercel

Paste everything inside the fence into Claude Code, opened **as administrator** in `C:\ftmo\app`.
Before pasting, have ready: GitHub access on the PC (`gh auth login`), the secrets file
`C:\ftmo\secrets\.env.windows` (see SETUP_WITH_CLAUDE_CODE.md), a Cloudflare account with a domain on
it, and a Vercel account linked to GitHub. You type every password and do every browser sign-in
yourself; Claude never sees them.

```text
GOAL
Make the FTMO research system live 24/7 on this Windows PC and reachable from anywhere through a
frontend on Vercel, safely. Work in phases, stop at the end of each phase, show me what you verified,
and wait for my "continue". Stop at the first failure and show me the exact error.

WHAT THIS PC RUNS (and never runs)
Runs: the research dashboard + Agent Control Center (python -m research_agent ui, 127.0.0.1:8602),
the 24/7 research worker it supervises, RAG on PostgreSQL 16 + pgvector, nightly backups, health checks.
Never: the broker hub (:8600), the MT5 bridge, ftmo_mt5.main, any broker credential (MT5_*, TT_*,
WEBULL_*, BROKER, HUB_PORT). Nothing here may place an order. Follow CLAUDE.md (TRADING MODE) in this repo.

HARD RULES
- Never print, echo, log, commit or paste secret values (API keys, tokens, passwords). Refer to them
  by name only. I type every password and do every browser sign-in myself.
- Never expose 127.0.0.1:8602 to the internet without authentication. The dashboard has no login of
  its own and can start paid Claude runs.
- Code changes go on a git branch + pull request for me to review and merge; never push to master,
  never edit files directly in C:\ftmo\app outside that flow (deploy.ps1 refuses a dirty checkout).
- Don't delete data, databases or backups. Don't change Windows security settings beyond what the
  scripts in deploy/windows do. Ask before anything outside this plan.

PHASE 1 - Install and run 24/7 on this PC
Follow deploy/windows/SETUP_WITH_CLAUDE_CODE.md (route B, GitHub clone) in an elevated PowerShell:
  .\deploy\windows\00-check-connectivity.ps1
  .\deploy\windows\01-install-prereqs.ps1        (then reopen an elevated shell)
  .\deploy\windows\02-build-pgvector.ps1
  .\deploy\windows\03-setup-database.ps1 -SecretsFile C:\ftmo\secrets\.env.windows
  .\deploy\windows\04-setup-app.ps1 -SecretsFile C:\ftmo\secrets\.env.windows
       (if I give you a transfer folder from the Mac, add -Transfer <folder> to restore my data)
  .\deploy\windows\05-install-services.ps1
Then: healthcheck.ps1 must report ok; set the power plan so the PC never sleeps; confirm the Windows
service FTMO-Research and PostgreSQL are Automatic; reboot once (tell me first) and prove both come
back by themselves and healthcheck is ok again. Work through deploy/windows/WINDOWS_VERIFICATION.md
and report every item as passed / failed / not testable yet. Tell me when to press Start in the
Agent Control Center (http://127.0.0.1:8602/research) and confirm one agent run completes.

PHASE 2 - Secure public API through Cloudflare Tunnel (nothing public before this is done)
1. Backend auth (code change, branch + PR): add an API token check to the research web app.
   When RESEARCH_API_TOKEN is set, every /api/research/* request must carry
   "Authorization: Bearer <token>" (constant-time compare) or get 401; /api/research/health may stay
   open but must not reveal secrets. Requests from the local browser on the PC itself must keep
   working (e.g. the server injects the token into the page it serves on 127.0.0.1 only, or skip the
   check only when there is no Cf-Connecting-Ip header AND the client is 127.0.0.1 - pick one, explain
   why it is safe, since cloudflared also connects from 127.0.0.1). No CORS wildcard. Tests for:
   missing token 401, wrong token 401, right token 200, local page still works. Load
   RESEARCH_API_TOKEN from .env like the other research keys (research_agent/ops.py ENV_ALLOWED).
   After I merge, deploy it with: .\deploy\windows\deploy.ps1 -FromGit -Online
2. Generate RESEARCH_API_TOKEN (32+ random bytes) straight into C:\ftmo\app\.env without showing it.
3. Install cloudflared as a Windows service; I do `cloudflared tunnel login` in the browser. Create a
   named tunnel (e.g. ftmo-research) routing a hostname I give you (e.g. ftmo-api.<my-domain>) to
   http://127.0.0.1:8602 only. Add a Cloudflare Access application on that hostname with a
   Service Token policy (I create the token in the dashboard if you can't via API; you only need
   its name). Prove from this PC: without headers -> blocked by Access; with Access headers but no
   bearer token -> 401; with both -> 200.
4. Add cloudflared to the health check: if the tunnel service is stopped, start it; log it.

PHASE 3 - Frontend on Vercel (code change, branch + PR)
Create a folder `frontend/` that Vercel deploys from this GitHub repo (root directory = frontend):
- Serve the existing dashboard page research_agent/ui/research.html unchanged (copy it at build time,
  so there is one source of truth). It calls relative /api/research/... URLs.
- A Vercel serverless proxy for /api/research/* that forwards to BACKEND_URL (the tunnel hostname),
  adding CF-Access-Client-Id / CF-Access-Client-Secret and "Authorization: Bearer RESEARCH_API_TOKEN"
  server-side, so the browser never sees any secret. Stream the export.xlsx download. Handle backend
  down with a clear 502 message, not a blank page.
- A login in front of everything (page and proxy): one password (DASHBOARD_PASSWORD) -> httpOnly,
  Secure, SameSite=Lax signed session cookie (SESSION_SECRET), logout, rate-limit wrong passwords.
- Env vars (set in Vercel by me, names only in code): BACKEND_URL, CF_ACCESS_CLIENT_ID,
  CF_ACCESS_CLIENT_SECRET, RESEARCH_API_TOKEN, DASHBOARD_PASSWORD, SESSION_SECRET. Document them in
  frontend/README.md. No secrets in git, no NEXT_PUBLIC_/client-side secrets.
- Tests for the proxy (auth required, headers added, path restricted to /api/research/*, no open
  proxy to other URLs).
Then tell me exactly what to click in Vercel (import repo, root dir, env vars). After it deploys,
prove: the Vercel URL without login -> login page; wrong password -> refused; right password ->
dashboard loads live data from this PC; Start/Pause in the Control Center works through Vercel.

PHASE 4 - Keep it updated and report
- Updates: I push to GitHub -> Vercel redeploys the frontend by itself; the PC updates with
  .\deploy\windows\deploy.ps1 -FromGit -Online (backup, tests, automatic rollback). Propose (don't
  install without asking) a safe way to automate the PC side, e.g. a scheduled task that runs
  deploy.ps1 -FromGit nightly only when master has new commits.
- Final report: what runs where, URLs, services and scheduled tasks, where logs and backups are,
  test results, anything that failed or is still unverified, and the exact daily checks for me.
```

## Notes for you (not part of the prompt)

- **Why a proxy instead of calling the PC from the browser:** the Cloudflare and API secrets stay on
  Vercel's server side, there's no CORS to open up, and the dashboard HTML needs no change.
- **Cost guard:** before pressing Start, check the Agent Control Center settings: the per-run cap
  (`budget_usd`, default $5) and the daily cap (`daily_budget_usd`, default $20; no new run starts
  once today's spend would pass it). 24/7 runs spend Claude and FMP credits.
- **No domain on Cloudflare?** Phase 2 needs one for a stable named tunnel. Tell Claude what you
  have instead; a temporary quick tunnel URL changes on every restart and isn't suitable for Vercel.
