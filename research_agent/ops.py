"""Operations for running the research agent 24/7 (macOS and Windows alike).

    health       is everything working?  research.db, the worker, the RAG database, disk
    maintenance  keep the disk in check: prune old daily price caches, rotate the worker log
    backup       consistent copy of everything that cannot be re-downloaded (+ checksum manifest)
    restore      put a backup (or a Mac->Windows transfer) back, refusing to overwrite data
    transfer     backup + git bundle + embedding model + a filtered secrets file, for a new PC

Only research data is touched: no broker, MT5 or order state, and the generated secrets file
carries research keys only (broker credentials are filtered out).
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from research_agent.config import REPO, Settings

CACHE_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# research state that cannot be re-downloaded (the price cache can, and is 860 MB per day)
STATE_KEEP = ("runs", "reports", "exports", "universe", "quality", "strategy_log.jsonl", "sheets.json")
STATE_SKIP_SUFFIX = (".log", ".log.1", ".log.2", ".log.3")
RESEARCH_TABLES = ("universe", "screens", "strategies", "tests", "runs", "quality", "agent_events",
                   "company_research_log", "company_context")
RAG_TABLES = ("documents", "chunks", "financial_statements", "earnings", "company_data", "fetch_log")
# secrets the research PC needs - everything else in .env (broker credentials) stays behind
# (no paths: a Mac path such as LOG_DIR or RAG_MODEL_CACHE would be wrong on Windows)
ENV_ALLOWED = ("FMP_API_KEY", "SEC_USER_AGENT", "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_DB", "POSTGRES_USER",
               "POSTGRES_PASSWORD", "POSTGRES_TEST_DB", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "LOG_LEVEL")
BROKER_KEY = re.compile(r"^(MT5_|TT_|WEBULL_|IB_|TASTY|DHAN|ZERODHA|UPSTOX|BROKER$|HUB_PORT$)", re.I)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=60).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


# ------------------------------------------------------------------ PostgreSQL tools (pg_dump / pg_restore)
PG_BIN_GUESSES = ("/opt/homebrew/opt/postgresql@16/bin", "/usr/local/opt/postgresql@16/bin", "/usr/lib/postgresql/16/bin",  # cross-platform
                  r"C:\Program Files\PostgreSQL\16\bin", r"C:\Program Files\PostgreSQL\17\bin")


def pg_tool(name: str) -> str:
    exe = name + (".exe" if os.name == "nt" else "")
    for d in filter(None, [os.environ.get("PG_BIN")] + list(PG_BIN_GUESSES)):
        if (Path(d) / exe).exists():
            return str(Path(d) / exe)
    found = shutil.which(name)
    if not found:
        raise FileNotFoundError(f"{name} not found: install PostgreSQL client tools or set PG_BIN")
    return found


def _pg_env(cfg) -> Dict[str, str]:
    env = dict(os.environ)
    if cfg.password:
        env["PGPASSWORD"] = cfg.password
    return env


def _pg_args(cfg) -> List[str]:
    return ["-h", cfg.host, "-p", str(cfg.port), "-U", cfg.user, "-d", cfg.dbname]


def _rag_cfg(pg_cfg=None):
    from rag.config import Settings as RagSettings
    return pg_cfg or RagSettings().pg_config()


def rag_counts(cfg) -> Dict[str, int]:
    from rag import db
    with db.connect(cfg) as conn:
        if not conn.execute("SELECT to_regclass('rag.chunks') IS NOT NULL AS ok").fetchone()["ok"]:
            return {}
        out = {t: conn.execute(f"SELECT count(*) AS n FROM rag.{t}").fetchone()["n"] for t in RAG_TABLES
               if conn.execute("SELECT to_regclass(%s) IS NOT NULL AS ok", (f"rag.{t}",)).fetchone()["ok"]}
        out["embeddings"] = conn.execute("SELECT count(*) AS n FROM rag.chunks WHERE embedding IS NOT NULL").fetchone()["n"]
        return out


def research_counts(db_path: Path) -> Dict[str, int]:
    if not db_path.exists():
        return {}
    con = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in RESEARCH_TABLES if t in have}
    finally:
        con.close()


# ------------------------------------------------------------------ health
def health(settings: Optional[Settings] = None, pg_cfg=None, min_free_gb: float = 5.0) -> Dict[str, Any]:
    """{'status': ok|degraded|down, 'problems': [...], 'checks': {...}}. Read-only."""
    settings = settings or Settings()
    checks: Dict[str, Any] = {}
    problems: List[str] = []
    down = False
    try:
        checks["research_db"] = research_counts(settings.db_path)
        if not checks["research_db"]:
            problems.append("research.db missing or empty")
            down = True
    except sqlite3.Error as e:
        checks["research_db"] = {"error": str(e)}
        problems.append(f"research.db unreadable: {e}")
        down = True
    try:
        from research_agent.control import Control, _parse
        st = Control(settings).status()
        hb = st.get("heartbeat_at")
        age = round((datetime.now(timezone.utc) - _parse(hb)).total_seconds()) if hb else None
        checks["worker"] = {"desired": st["desired"], "state": st["state"], "alive": st["worker_alive"], "pid": st["pid"],
                            "heartbeat_age_s": age, "current_run_id": st["current_run_id"],
                            "last_run_id": st["last_run_id"], "last_run_at": st["last_run_at"],
                            "last_outcome": (st.get("last_result") or {}).get("outcome"),
                            "consecutive_failures": st["consecutive_failures"], "last_error": st["last_error"],
                            "spent_today_usd": st["spent_today_usd"]}
        if st["desired"] in ("running", "paused") and not st["worker_alive"]:
            problems.append(f"worker should be {st['desired']} but is not alive")
        if (st["consecutive_failures"] or 0) >= 3:
            problems.append(f"{st['consecutive_failures']} consecutive failed runs (agent auto-pauses)")
    except Exception as e:                                           # noqa: BLE001
        checks["worker"] = {"error": f"{type(e).__name__}: {e}"}
        problems.append("worker status unavailable")
    try:
        from rag import db as ragdb
        info = ragdb.check(_rag_cfg(pg_cfg))
        checks["rag_db"] = {k: info.get(k) for k in ("server", "pgvector", "schema_versions", "documents", "chunks",
                                                      "embeddings")}
        if not info.get("pgvector"):
            problems.append("pgvector extension missing")
    except Exception as e:                                           # noqa: BLE001
        checks["rag_db"] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        problems.append("RAG database unreachable (company research unavailable)")
    usage = shutil.disk_usage(settings.state_dir if settings.state_dir.exists() else REPO)
    free_gb = usage.free / 1e9
    checks["disk"] = {"free_gb": round(free_gb, 1), "used_pct": round(100 * usage.used / usage.total, 1)}
    if free_gb < min_free_gb:
        problems.append(f"low disk space: {free_gb:.1f} GB free")
    cache = settings.state_dir / "cache"
    days = sorted(p.name for p in cache.iterdir() if p.is_dir() and CACHE_DAY.match(p.name)) if cache.exists() else []
    checks["price_cache_days"] = days
    from rag.embed import model_cache_dir
    checks["embedding_model_cached"] = model_cache_dir().exists() and any(model_cache_dir().rglob("*.onnx"))
    checks["platform"] = f"{platform.system()} {platform.release()} / Python {platform.python_version()}"
    status = "down" if down else ("degraded" if problems else "ok")
    return {"status": status, "checked_at": _now(), "problems": problems, "checks": checks}


# ------------------------------------------------------------------ maintenance
def prune_price_cache(settings: Settings, keep_days: int = 3, today: Optional[date] = None) -> List[str]:
    """Delete daily price-cache folders older than ``keep_days`` (never today's). They are
    re-downloaded on demand; each is ~860 MB, so a 24/7 PC would otherwise fill its disk."""
    cache = settings.state_dir / "cache"
    if not cache.exists():
        return []
    cutoff = (today or date.today()) - timedelta(days=keep_days)
    removed = []
    for p in sorted(cache.iterdir()):
        if p.is_dir() and CACHE_DAY.match(p.name) and date.fromisoformat(p.name) < cutoff:
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p.name)
    return removed


def maintenance(settings: Optional[Settings] = None, keep_cache_days: int = 3) -> Dict[str, Any]:
    from research_agent.control import rotate_log
    settings = settings or Settings()
    return {"pruned_cache_days": prune_price_cache(settings, keep_cache_days),
            "worker_log_rotated": rotate_log(settings.state_dir / "worker.log")}


# ------------------------------------------------------------------ backup
def _zip_tree(zf: zipfile.ZipFile, root: Path, rel_base: Path, skip_dirs: Iterable[str] = ()) -> int:
    n = 0
    skip = set(skip_dirs)
    for p in sorted(root.rglob("*")):
        if any(part in skip for part in p.relative_to(rel_base).parts):
            continue
        if p.is_file() and not p.name.endswith(STATE_SKIP_SUFFIX):
            zf.write(p, p.relative_to(rel_base).as_posix())           # follows symlinks: real bytes go in
            n += 1
    return n


def backup(dest_root: Path, settings: Optional[Settings] = None, pg_cfg=None, keep: int = 14,
           include_rag_db: bool = True, include_models: bool = False, label: str = "backup") -> Dict[str, Any]:
    """A self-contained folder: research.db (SQLite online backup), state files, rag/state files,
    the rag schema (pg_dump), and manifest.json with checksums and row counts."""
    settings = settings or Settings()
    from rag.config import STATE as RAG_STATE
    dest_root = Path(dest_root)
    out = dest_root / f"ftmo-{label}-{datetime.now():%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=False)
    files: Dict[str, Dict[str, Any]] = {}

    if settings.db_path.exists():                                     # consistent even while the agent writes
        src = sqlite3.connect(str(settings.db_path))
        dst = sqlite3.connect(str(out / "research.db"))
        with dst:
            src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")                      # one self-contained file, no -wal/-shm
        src.close()
        dst.close()
    with zipfile.ZipFile(out / "research_state.zip", "w", zipfile.ZIP_DEFLATED) as zf:
        for name in STATE_KEEP:
            p = settings.state_dir / name
            if p.is_dir():
                _zip_tree(zf, p, settings.state_dir)
            elif p.is_file():
                zf.write(p, name)
    if RAG_STATE.exists():
        with zipfile.ZipFile(out / "rag_state.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            _zip_tree(zf, RAG_STATE, RAG_STATE, skip_dirs=["models", "migrations"])
            models = RAG_STATE / "models"
            if include_models and models.is_dir():
                # Hugging Face cache: snapshots/ link into blobs/ (and a shared xet store); zipping the
                # resolved snapshots alone carries the model once and loads offline after restore.
                _zip_tree(zf, models, RAG_STATE, skip_dirs=["blobs", ".locks"])
    rag_rows: Dict[str, int] = {}
    if include_rag_db:
        cfg = _rag_cfg(pg_cfg)
        rag_rows = rag_counts(cfg)
        r = subprocess.run([pg_tool("pg_dump"), *_pg_args(cfg), "-Fc", "-n", "rag", "--no-owner", "--no-privileges",
                            "-f", str(out / "ftmo_rag.dump")], env=_pg_env(cfg), capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"pg_dump failed: {r.stderr.strip()[:400]}")
    for p in sorted(out.iterdir()):
        files[p.name] = {"bytes": p.stat().st_size, "sha256": _sha256(p)}
    manifest = {"kind": "ftmo-research-backup", "label": label, "created_at": _now(), "host": platform.node(),
                "platform": platform.platform(), "git_commit": _git("rev-parse", "HEAD"),
                "research_db_rows": research_counts(out / "research.db") if (out / "research.db").exists() else {},
                "rag_rows": rag_rows, "files": files}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    if keep:
        olds = sorted(p for p in dest_root.glob(f"ftmo-{label}-*") if p.is_dir())
        for p in olds[:-keep]:
            shutil.rmtree(p, ignore_errors=True)
    return {"path": str(out), **{k: manifest[k] for k in ("research_db_rows", "rag_rows")}, "files": files}


def verify_backup(src: Path) -> Dict[str, Any]:
    src = Path(src)
    m = json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    bad = [n for n, meta in m["files"].items() if not (src / n).exists() or _sha256(src / n) != meta["sha256"]]
    return {"ok": not bad, "corrupt_or_missing": bad, "manifest": m}


# ------------------------------------------------------------------ restore
class RefusedRestore(RuntimeError):
    pass


def restore(src: Path, settings: Optional[Settings] = None, pg_cfg=None, replace: bool = False,
            restore_rag_db: bool = True, rag_state_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Put a backup back. Refuses to overwrite an existing research.db or a non-empty rag schema
    unless ``replace`` (and then the current data is backed up first). Verifies row counts."""
    settings = settings or Settings()
    from rag.config import STATE as RAG_STATE
    rag_state_dir = Path(rag_state_dir or RAG_STATE)
    src = Path(src)
    v = verify_backup(src)
    if not v["ok"]:
        raise RefusedRestore(f"backup is corrupt or incomplete: {v['corrupt_or_missing']}")
    m = v["manifest"]
    cfg = None
    if restore_rag_db and (src / "ftmo_rag.dump").exists():
        cfg = _rag_cfg(pg_cfg)
    existing_research = research_counts(settings.db_path)
    existing_rag = rag_counts(cfg) if cfg else {}
    if not replace and (any(existing_research.values()) or any(existing_rag.get(t, 0) for t in RAG_TABLES)):
        raise RefusedRestore("target already has research data; pass replace=True (it is backed up first)")
    safety = None
    if replace and (existing_research or existing_rag):
        safety = backup(settings.state_dir.parent / "pre-restore-backups", settings, cfg,
                        include_rag_db=bool(existing_rag), label="pre-restore")["path"]
    settings.ensure_dirs()
    if (src / "research.db").exists():
        for suffix in ("-wal", "-shm"):
            Path(str(settings.db_path) + suffix).unlink(missing_ok=True)
        shutil.copy2(src / "research.db", settings.db_path)
    with zipfile.ZipFile(src / "research_state.zip") as zf:
        zf.extractall(settings.state_dir)
    if (src / "rag_state.zip").exists():
        rag_state_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(src / "rag_state.zip") as zf:
            zf.extractall(rag_state_dir)
    rag_rows: Dict[str, int] = {}
    if cfg:
        from rag import db as ragdb
        with ragdb.connect(cfg) as conn:
            conn.execute("DROP SCHEMA IF EXISTS rag CASCADE")
        ragdb._READY.clear()
        r = subprocess.run([pg_tool("pg_restore"), *_pg_args(cfg), "--no-owner", "--no-privileges", "--exit-on-error",
                            str(src / "ftmo_rag.dump")], env=_pg_env(cfg), capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(f"pg_restore failed: {r.stderr.strip()[:400]}")
        ragdb.init_db(cfg)                                            # applies any newer migrations; no-op otherwise
        rag_rows = rag_counts(cfg)
    got_research = research_counts(settings.db_path)
    mismatch = {k: (m["research_db_rows"].get(k), got_research.get(k)) for k in m["research_db_rows"]
                if m["research_db_rows"].get(k) != got_research.get(k)}
    mismatch.update({f"rag.{k}": (m["rag_rows"].get(k), rag_rows.get(k)) for k in m.get("rag_rows", {})
                     if cfg and m["rag_rows"].get(k) != rag_rows.get(k)})
    return {"ok": not mismatch, "mismatch": mismatch, "research_db_rows": got_research, "rag_rows": rag_rows,
            "safety_backup": safety, "from": str(src)}


# ------------------------------------------------------------------ Mac -> Windows transfer
def env_for_research(env_path: Path) -> Dict[str, str]:
    """The research keys from a .env file; broker credentials and everything else are dropped."""
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        k, sep, v = line.partition("=")
        k = k.strip()
        if not sep or k.startswith("#") or BROKER_KEY.match(k) or k not in ENV_ALLOWED:
            continue
        out[k] = v.strip()
    return out


def load_research_env(env_path: Optional[Path] = None, environ=None) -> List[str]:
    """Put the research keys from the FTMO ``.env`` into the process environment (never
    overriding a value that is already set). A Windows service does not inherit a login shell,
    so this is how the dashboard - and the worker it spawns - get FMP / Claude / PostgreSQL
    settings without storing secrets in the service registry. Broker keys are never loaded."""
    environ = os.environ if environ is None else environ
    loaded = []
    for k, v in env_for_research(Path(env_path or REPO / ".env")).items():
        v = v.strip().strip('"').strip("'")
        if v and not environ.get(k):
            environ[k] = v
            loaded.append(k)
    return loaded


def transfer_export(out_root: Path, settings: Optional[Settings] = None, pg_cfg=None, secrets_dir: Optional[Path] = None,
                    wheel_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Everything a new PC needs, in one folder: the code (git bundle, full history), the data
    (a backup incl. the embedding model) and optionally an offline Windows wheelhouse. The filtered
    secrets file is written SEPARATELY (``secrets_dir``) so it is never copied with the data."""
    out_root = Path(out_root)
    b = backup(out_root, settings, pg_cfg, keep=0, include_models=True, label="transfer")
    folder = Path(b["path"])
    bundle = folder / "ftmo.bundle"
    r = subprocess.run(["git", "bundle", "create", str(bundle), "--all"], cwd=REPO, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"git bundle failed: {r.stderr.strip()[:300]}")
    if wheel_dir and Path(wheel_dir).exists():
        shutil.copytree(wheel_dir, folder / "wheels-win")
    m = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for p in (bundle,):
        m["files"][p.name] = {"bytes": p.stat().st_size, "sha256": _sha256(p)}
    m["git_branch"] = _git("rev-parse", "--abbrev-ref", "HEAD")
    m["uncommitted_changes"] = bool(_git("status", "--porcelain"))
    (folder / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    secrets_file = None
    if secrets_dir:
        env = env_for_research(REPO / ".env")
        sd = Path(secrets_dir)
        sd.mkdir(parents=True, exist_ok=True)
        secrets_file = sd / ".env.windows"
        secrets_file.write_text("# FTMO research PC secrets (research keys only; broker credentials removed).\n"
                                "# Carry separately from the data folder; delete after copying to C:\\ftmo\\app\\.env\n"
                                + "".join(f"{k}={v}\n" for k, v in env.items()), encoding="utf-8")
        try:
            os.chmod(secrets_file, 0o600)
        except OSError:
            pass
    return {"folder": str(folder), "secrets_file": str(secrets_file) if secrets_file else None,
            "uncommitted_changes": m["uncommitted_changes"], "git_commit": m["git_commit"],
            "research_db_rows": b["research_db_rows"], "rag_rows": b["rag_rows"],
            "size_mb": round(sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()) / 1e6, 1)}
