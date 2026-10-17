"""24/7 operations: health, maintenance, backup/restore and the Mac -> Windows transfer.

Uses temporary state folders and the *_test PostgreSQL database only (real pg_dump / pg_restore)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from research_agent import ops  # noqa: E402
from research_agent.config import Settings  # noqa: E402
from research_agent.store import Store as ResearchStore  # noqa: E402
from rag_fixtures import cfg, store  # noqa: E402,F401  (pytest fixtures)


def research_state(tmp: Path) -> Settings:
    s = Settings(state_dir=tmp / "state", workers=1)
    s.ensure_dirs()
    st = ResearchStore(s.db_path)
    st.start_run("agent-20261011-000000-abcd", "agent")
    st.save_test({"fingerprint": "f1", "symbol": "TSLA", "strategy": "SIMPLE_PULLBACK", "params": {}, "phase": "screen",
                  "as_of": "2026-10-08", "bars": 2500, "timeframe": "1h",
                  "metrics": {"returnPct": 121.9, "bhReturnPct": 71.4, "vsBHPct": 50.5, "trades": 11}, "grade": None,
                  "why": []})
    (s.state_dir / "reports").mkdir(exist_ok=True)
    (s.reports_dir / "r.md").write_text("# Report → ≥ 50 pts — ✓\n", encoding="utf-8")
    (s.state_dir / "strategy_log.jsonl").write_text('{"name": "X"}\n', encoding="utf-8")
    cache = s.state_dir / "cache" / "2026-10-01"
    cache.mkdir(parents=True)
    (cache / "AAPL_1d_3650.json").write_text("[]", encoding="utf-8")
    return s


def rag_data(store):
    v = np.random.default_rng(1).normal(size=(2, 384)).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    base = {"symbol": "TSLA", "company": "Tesla", "document_type": "earnings call transcript (FMP)", "form": "TRANSCRIPT",
            "filing_date": "2026-07-22", "fiscal_year": 2026, "fiscal_quarter": "Q2", "section": "Earnings call - Q&A",
            "subsection": "Elon Musk", "page": None, "kind": "text", "source_url": "https://x", "source": "FMP",
            "period": "Q2 FY2026"}
    store.replace_document({"doc_id": "FMP:transcript:TSLA:2026:Q2", "symbol": "TSLA", "form": "TRANSCRIPT",
                            "n_chunks": 2, "source": "FMP"},
                           [{**base, "chunk_id": f"c{i}", "doc_id": "FMP:transcript:TSLA:2026:Q2",
                             "text": f"Demand → strong ({i})"} for i in range(2)], v)
    return v


@pytest.fixture
def rag_state(tmp_path, monkeypatch):
    import rag.config
    d = tmp_path / "rag_state"
    (d / "fmp").mkdir(parents=True)
    (d / "fmp" / "probe.txt").write_text("PASS → transcripts", encoding="utf-8")
    (d / "models").mkdir()
    (d / "models" / "model.onnx").write_bytes(b"onnx")
    (d / "models" / "m" / "blobs").mkdir(parents=True)                   # Hugging Face duplicate store
    (d / "models" / "m" / "blobs" / "abc").write_bytes(b"onnx")
    monkeypatch.setattr(rag.config, "STATE", d)
    return d


# ------------------------------------------------------------------ maintenance
def test_prune_price_cache_keeps_recent_days_and_today(tmp_path):
    s = Settings(state_dir=tmp_path / "s", workers=1)
    for d in ("2026-10-01", "2026-10-05", "2026-10-08", "2026-10-11", "not-a-date"):
        (s.state_dir / "cache" / d).mkdir(parents=True)
    removed = ops.prune_price_cache(s, keep_days=3, today=date(2026, 10, 11))
    assert removed == ["2026-10-01", "2026-10-05"]
    assert sorted(p.name for p in (s.state_dir / "cache").iterdir()) == ["2026-10-08", "2026-10-11", "not-a-date"]


# ------------------------------------------------------------------ health
def test_health_states(tmp_path, cfg, store):
    s = Settings(state_dir=tmp_path / "missing", workers=1)
    assert ops.health(s, pg_cfg=cfg)["status"] == "down"                         # no research.db
    s = research_state(tmp_path)
    h = ops.health(s, pg_cfg=cfg)
    assert h["status"] == "ok", h["problems"]
    assert h["checks"]["rag_db"]["pgvector"] and h["checks"]["research_db"]["tests"] == 1
    from research_agent.control import Control
    Control(s).update(desired="running", pid=999_999, heartbeat_at="2026-01-01T00:00:00+00:00")
    h = ops.health(s, pg_cfg=cfg)
    assert h["status"] == "degraded" and any("not alive" in p for p in h["problems"])
    assert ops.health(s, pg_cfg=cfg, min_free_gb=10 ** 9)["status"] == "degraded"   # low disk


# ------------------------------------------------------------------ backup / restore round trip
def test_backup_restore_round_trip_is_exact(tmp_path, cfg, store, rag_state):
    s = research_state(tmp_path)
    vec = rag_data(store)
    b = ops.backup(tmp_path / "backups", s, pg_cfg=cfg)
    assert b["rag_rows"]["chunks"] == 2 and b["research_db_rows"]["tests"] == 1
    names = set(b["files"])
    assert {"research.db", "research_state.zip", "rag_state.zip", "ftmo_rag.dump"} <= names
    import zipfile
    zs = zipfile.ZipFile(Path(b["path"]) / "research_state.zip").namelist()
    assert "reports/r.md" in zs and not any(n.startswith("cache/") for n in zs)          # price cache excluded
    assert "models/model.onnx" not in zipfile.ZipFile(Path(b["path"]) / "rag_state.zip").namelist()
    assert ops.verify_backup(Path(b["path"]))["ok"]
    assert not [n for n in os.listdir(b["path"]) if n.endswith(("-wal", "-shm"))]        # single-file research.db

    # a fresh PC: empty state folder, empty rag schema
    s2 = Settings(state_dir=tmp_path / "pc" / "state", workers=1)
    new_rag_state = tmp_path / "pc" / "rag_state"
    from rag import db
    with db.connect(cfg) as conn:
        conn.execute("DROP SCHEMA IF EXISTS rag CASCADE")
    db._READY.clear()
    r = ops.restore(Path(b["path"]), s2, pg_cfg=cfg, rag_state_dir=new_rag_state)
    assert r["ok"], r["mismatch"]
    assert r["research_db_rows"]["tests"] == 1 and r["rag_rows"]["chunks"] == 2 and r["rag_rows"]["embeddings"] == 2
    assert (s2.reports_dir / "r.md").read_text(encoding="utf-8") == "# Report → ≥ 50 pts — ✓\n"
    assert (new_rag_state / "fmp" / "probe.txt").read_text(encoding="utf-8") == "PASS → transcripts"
    from rag.store import Store as RagStore, parse_vec
    with RagStore(cfg) as st2:
        got = st2.chunks(["c0", "c1"], with_embedding=True)
    assert np.array_equal(parse_vec(got[0]["embedding"]), vec[0]) and got[1]["text"] == "Demand → strong (1)"


def test_restore_refuses_to_overwrite_then_replaces_with_safety_backup(tmp_path, cfg, store, rag_state):
    s = research_state(tmp_path)
    rag_data(store)
    b = Path(ops.backup(tmp_path / "backups", s, pg_cfg=cfg)["path"])
    with pytest.raises(ops.RefusedRestore):
        ops.restore(b, s, pg_cfg=cfg)                                             # same, non-empty target
    r = ops.restore(b, s, pg_cfg=cfg, replace=True, rag_state_dir=tmp_path / "rs")
    assert r["ok"] and r["safety_backup"] and Path(r["safety_backup"]).exists()


def test_corrupt_backup_is_refused(tmp_path, cfg, store, rag_state):
    s = research_state(tmp_path)
    b = Path(ops.backup(tmp_path / "backups", s, pg_cfg=cfg, include_rag_db=False)["path"])
    with (b / "research.db").open("r+b") as fh:
        fh.seek(200)
        fh.write(b"\x00garbage")
    assert not ops.verify_backup(b)["ok"]
    with pytest.raises(ops.RefusedRestore):
        ops.restore(b, Settings(state_dir=tmp_path / "other", workers=1), pg_cfg=cfg)


def test_backup_rotation_keeps_the_newest(tmp_path, cfg, store, rag_state):
    s = research_state(tmp_path)
    for name in ("ftmo-backup-20260101-000000", "ftmo-backup-20260102-000000"):
        (tmp_path / "backups" / name).mkdir(parents=True)
    ops.backup(tmp_path / "backups", s, pg_cfg=cfg, keep=2, include_rag_db=False)
    left = sorted(p.name for p in (tmp_path / "backups").iterdir())
    assert len(left) == 2 and left[0] == "ftmo-backup-20260102-000000"


# ------------------------------------------------------------------ transfer to a new PC
def test_secrets_file_carries_research_keys_only(tmp_path):
    env = tmp_path / ".env"
    env.write_text("FMP_API_KEY=fmp\nPOSTGRES_PASSWORD=pg\nMT5_PASSWORD=broker\nTT_SECRET=t\nWEBULL_APP_SECRET=w\n"
                   "BROKER=mt5\nHUB_PORT=8600\nANTHROPIC_API_KEY=a\n# FMP_API_KEY=old\nRANDOM=x\nLOG_DIR=/Users/me/logs\n", encoding="utf-8")
    assert ops.env_for_research(env) == {"FMP_API_KEY": "fmp", "POSTGRES_PASSWORD": "pg", "ANTHROPIC_API_KEY": "a"}


def test_transfer_export_bundles_code_data_and_model(tmp_path, cfg, store, rag_state, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@example.com"], ["git", "config", "user.name", "t"]):
        subprocess.run(cmd, cwd=repo, check=True)
    (repo / "a.py").write_text("print('hi')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    (repo / ".env").write_text("FMP_API_KEY=k\nMT5_PASSWORD=never\n", encoding="utf-8")
    monkeypatch.setattr(ops, "REPO", repo)
    s = research_state(tmp_path)
    rag_data(store)
    out = ops.transfer_export(tmp_path / "transfer", s, pg_cfg=cfg, secrets_dir=tmp_path / "usb")
    folder = Path(out["folder"])
    assert subprocess.run(["git", "bundle", "verify", str(folder / "ftmo.bundle")], cwd=repo,
                          capture_output=True).returncode == 0
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(folder / "ftmo.bundle"), str(clone)], check=True)
    assert (clone / "a.py").exists()
    import zipfile
    names = zipfile.ZipFile(folder / "rag_state.zip").namelist()
    assert "models/model.onnx" in names and "models/m/blobs/abc" not in names        # offline model, stored once
    secrets = Path(out["secrets_file"]).read_text(encoding="utf-8")
    assert "FMP_API_KEY=k" in secrets and "MT5" not in secrets and not (folder / ".env.windows").exists()
    assert ops.verify_backup(folder)["ok"]
    assert out["rag_rows"]["chunks"] == 2


def test_service_loads_research_keys_from_env_file_without_overriding(tmp_path):
    env = tmp_path / ".env"
    env.write_text('FMP_API_KEY="fmp"\nANTHROPIC_API_KEY=from-file\nMT5_PASSWORD=broker\nPOSTGRES_DB=\n', encoding="utf-8")
    environ = {"ANTHROPIC_API_KEY": "from-shell"}
    assert ops.load_research_env(env, environ) == ["FMP_API_KEY"]
    assert environ == {"ANTHROPIC_API_KEY": "from-shell", "FMP_API_KEY": "fmp"}
