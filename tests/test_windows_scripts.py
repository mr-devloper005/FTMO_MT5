"""The Windows deployment scripts (deploy/windows): static guards everywhere, and parse +
helper smoke tests when PowerShell 7 (pwsh) is available (set PWSH=/path/to/pwsh)."""
from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
WIN = REPO / "deploy" / "windows"
PS1 = sorted(WIN.glob("*.ps1"))
PWSH = os.environ.get("PWSH") or shutil.which("pwsh")


def test_scripts_exist():
    names = {p.name for p in PS1}
    assert {"common.ps1", "00-check-connectivity.ps1", "01-install-prereqs.ps1", "02-build-pgvector.ps1",
            "03-setup-database.ps1", "04-setup-app.ps1", "05-install-services.ps1", "healthcheck.ps1", "backup.ps1",
            "maintenance.ps1", "deploy.ps1", "uninstall-services.ps1"} <= names


@pytest.mark.parametrize("path", PS1, ids=lambda p: p.name)
def test_ascii_only(path):
    # Windows PowerShell 5.1 reads BOM-less scripts as ANSI: any non-ASCII byte becomes mojibake
    bad = [i for i, b in enumerate(path.read_bytes()) if b > 127]
    assert not bad, f"{path.name}: non-ASCII at byte {bad[0]}"


def test_powershell_scripts_check_out_with_crlf():
    attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
    assert re.search(r"^\*\.ps1\s+.*eol=crlf", attrs, re.M)
    assert re.search(r"^\*\.sh\s+.*eol=lf", attrs, re.M)


def _code(path: Path):
    for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        code = line.split("#", 1)[0]
        if code.strip():
            yield i, code


@pytest.mark.parametrize("path", PS1 + sorted(WIN.glob("*.sh")), ids=lambda p: p.name)
def test_no_broker_hub_or_bridge(path):
    """The research PC never runs the broker hub (:8600) or the MT5 bridge, never handles broker keys."""
    hits = [f"{i}: {c.strip()}" for i, c in _code(path)
            if re.search(r"8600|ftmo_mt5\.hub|mt5_api_server|MT5_PASSWORD|MT5_LOGIN|TT_SECRET|WEBULL_APP_SECRET|0\.0\.0\.0", c)]
    assert not hits, hits


def test_dashboard_binds_localhost_only():
    main = (REPO / "research_agent" / "__main__.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in main
    assert "$UiPort      = 8602" in (WIN / "common.ps1").read_text(encoding="utf-8")


def test_secrets_never_on_a_command_line():
    db = (WIN / "03-setup-database.ps1").read_text(encoding="utf-8")
    assert "\\getenv pw FTMO_DB_PW" in db and '"pw=' not in db
    for p in PS1:
        for i, c in _code(p):
            assert not re.search(r"Write-(Host|Output|FtmoLog).*\$(plain|secrets\[|cfg\[)", c), f"{p.name}:{i} may print a secret"


def test_every_script_loads_common_and_admin_where_it_changes_the_system():
    for p in PS1:
        if p.name == "common.ps1":
            continue
        text = p.read_text(encoding="utf-8")
        assert '. "$PSScriptRoot\\common.ps1"' in text, p.name
    for name in ("01-install-prereqs.ps1", "02-build-pgvector.ps1", "04-setup-app.ps1", "05-install-services.ps1",
                 "deploy.ps1", "uninstall-services.ps1"):
        assert "Assert-Admin" in (WIN / name).read_text(encoding="utf-8"), name


def test_service_runs_the_research_dashboard_only():
    svc = (WIN / "05-install-services.ps1").read_text(encoding="utf-8")
    assert "-m research_agent ui --port $UiPort" in svc
    assert "PYTHONUTF8=1" in svc and "SERVICE_DELAYED_AUTO_START" in svc and "DependOnService" in svc


def test_deploy_rolls_back_and_backs_up_first():
    d = (WIN / "deploy.ps1").read_text(encoding="utf-8")
    order = [d.index(s) for s in ("--is-ancestor", "'backup', '--dest'", "Stop-Research\n    try", "merge', '--ff-only",
                                  "$h = Start-Research", "ROLLING BACK", "reset', '--hard")]
    assert order == sorted(order)
    assert "Remove-Item $HoldFlag" in d


def test_lock_file_pins_everything_with_windows_essentials():
    lock = (WIN / "requirements-research.lock.txt").read_text(encoding="utf-8")
    pins = [l for l in lock.splitlines() if l and not l.startswith("#")]
    assert pins and all("==" in l for l in pins)
    names = {l.split("==")[0].lower() for l in pins}
    assert {"psutil", "tzdata", "claude-agent-sdk", "psycopg", "fastembed", "uvicorn"} <= names
    assert not names & {"metatrader5", "tastytrade", "webull", "ib-insync", "ib_insync"}


# ------------------------------------------------------------------ with PowerShell available
needs_pwsh = pytest.mark.skipif(not PWSH, reason="pwsh not installed (set PWSH=/path/to/pwsh)")


@needs_pwsh
def test_every_script_parses(tmp_path):
    checker = tmp_path / "parse.ps1"
    checker.write_text(
        "$bad = 0\nforeach ($f in $args) {\n  $t = $null; $e = $null\n"
        "  [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$t, [ref]$e) | Out-Null\n"
        "  foreach ($x in $e) { Write-Output ('{0}:{1} {2}' -f $f, $x.Extent.StartLineNumber, $x.Message); $bad++ }\n}\n"
        "exit $bad\n", encoding="utf-8")
    files = PS1 + [REPO / "tests" / "windows_scripts_smoke.ps1"]
    r = subprocess.run([PWSH, "-NoProfile", "-File", str(checker), *map(str, files)], capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@needs_pwsh
def test_common_helpers_against_a_live_dashboard(tmp_path):
    port = _free_port()
    state = tmp_path / "state"
    env = {**os.environ, "PYTHONUTF8": "1"}
    ui = subprocess.Popen([sys.executable, "-m", "research_agent", "--state", str(state), "ui", "--port", str(port)],
                          cwd=REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        r = subprocess.run([PWSH, "-NoProfile", "-File", str(REPO / "tests" / "windows_scripts_smoke.ps1"),
                            "-Root", str(tmp_path / "ftmo"), "-Py", sys.executable,
                            "-HealthUrlOverride", f"http://127.0.0.1:{port}/api/research/health"],
                           capture_output=True, text=True, timeout=180)
    finally:
        ui.terminate()
        ui.wait(timeout=30)
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
