"""Step C of the Lab semiconductor research task: out-of-sample / robustness
validation via ftmo_mt5.lab.service.validate() (which wraps
ftmo_mt5.lab.verdict.validate()) for a documented set of candidates drawn
from the pre-existing in-sample sweep (semis-sized-2156-runs.csv).

Candidate selection (all read from semis-sized-2156-runs.csv, all default
strategy parameters as returned by service.strategy_defaults(), all using the
same 600-bar window and per-symbol position sizing the original sweep used):

  (a) Per symbol (14), the single best-vsBH combo among rows with
      beatsBH=True, trades>=10, bh not flat (>=$1,000), topTradeShare<70.
      Where NO combo for a symbol clears that filter (CRDO, DELL, MRVL, NVDA,
      TSM), the single best nominally-"beatsBH" row is used instead and
      explicitly labelled non-qualifying in the output -- because the honest
      finding for that symbol IS that nothing clears the filter, and that has
      to show up in the validated evidence, not be silently dropped.

  (b) Per symbol (14), the best 1d-timeframe combo, since 1d carries the
      longest, most reliable real-calendar history and none of (a)'s picks
      landed on 1d.

  (c) A fixed set of "too good to be true" outliers: the top absolute-vsBH
      rows and the highest topTradeShare-among-beaters rows across the whole
      2156-row dataset, run through validate() to see whether they collapse
      under OOS / removal / sensitivity.

Uses ftmo_mt5.lab.service.validate(symbol, timeframe, spec, bars=600,
param_key=None) for every candidate -- real Lab function calls, not
re-implemented math. All data comes from the already-cached
study/lab/cache/*.json series (no network calls).

Run: venv/bin/python3 study/lab/semis_validate.py
"""
import csv
import json
import time
from pathlib import Path

from ftmo_mt5.lab import service

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "semis-sized-2156-runs.csv"
OUT_PATH = REPO / "study" / "lab" / "semis_validate_results.json"

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
BARS = 600


def load_rows():
    with open(CSV_PATH, newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["trades"] = int(float(r["trades"]))
        r["bh"] = float(r["bh"])
        r["vsBH"] = float(r["vsBH"])
        r["topTradeShare"] = float(r["topTradeShare"])
        r["beatsBH"] = r["beatsBH"].strip() == "True"
        r["shares"] = int(float(r["shares"]))
    return rows


def select_candidates(rows):
    defaults = service.strategy_defaults()
    candidates = []

    # --- (a) best qualifying combo per symbol, or best nominal if none qualify ---
    for s in SYMBOLS:
        pool = [r for r in rows if r["symbol"] == s]
        qualifying = [r for r in pool if r["beatsBH"] and r["trades"] >= 10
                      and abs(r["bh"]) >= 1000 and r["topTradeShare"] < 70]
        if qualifying:
            best = max(qualifying, key=lambda r: r["vsBH"])
            label = "stepC_a_qualifying"
        else:
            beats = [r for r in pool if r["beatsBH"]]
            best = max(beats, key=lambda r: r["vsBH"]) if beats else max(pool, key=lambda r: r["vsBH"])
            label = "stepC_a_NONQUALIFYING_no_combo_clears_filter"
        candidates.append({"group": label, "symbol": s, "timeframe": best["timeframe"],
                            "strategy": best["strategy"], "shares": best["shares"],
                            "sourceVsBH": best["vsBH"], "sourceTrades": best["trades"],
                            "sourceTopTradeShare": best["topTradeShare"]})

    # --- (b) best 1d combo per symbol ---
    for s in SYMBOLS:
        d1 = [r for r in rows if r["symbol"] == s and r["timeframe"] == "1d"]
        if not d1:
            continue
        best1d = max(d1, key=lambda r: r["vsBH"])
        candidates.append({"group": "stepC_b_best_1d", "symbol": s, "timeframe": "1d",
                            "strategy": best1d["strategy"], "shares": best1d["shares"],
                            "sourceVsBH": best1d["vsBH"], "sourceTrades": best1d["trades"],
                            "sourceTopTradeShare": best1d["topTradeShare"],
                            "sourceBeatsBH": best1d["beatsBH"]})

    # --- (c) too-good-to-be-true outliers ---
    ranked_vsbh = sorted(rows, key=lambda r: r["vsBH"], reverse=True)
    seen = {(c["symbol"], c["timeframe"], c["strategy"]) for c in candidates}
    added = 0
    for r in ranked_vsbh:
        key = (r["symbol"], r["timeframe"], r["strategy"])
        if key in seen or r["trades"] < 5:
            continue
        candidates.append({"group": "stepC_c_outlier_top_vsBH", "symbol": r["symbol"],
                            "timeframe": r["timeframe"], "strategy": r["strategy"],
                            "shares": r["shares"], "sourceVsBH": r["vsBH"],
                            "sourceTrades": r["trades"],
                            "sourceTopTradeShare": r["topTradeShare"]})
        seen.add(key)
        added += 1
        if added >= 5:
            break

    ranked_tts = sorted([r for r in rows if r["beatsBH"] and r["trades"] >= 10],
                        key=lambda r: r["topTradeShare"], reverse=True)
    added = 0
    for r in ranked_tts:
        key = (r["symbol"], r["timeframe"], r["strategy"])
        if key in seen:
            continue
        candidates.append({"group": "stepC_c_outlier_high_concentration", "symbol": r["symbol"],
                            "timeframe": r["timeframe"], "strategy": r["strategy"],
                            "shares": r["shares"], "sourceVsBH": r["vsBH"],
                            "sourceTrades": r["trades"],
                            "sourceTopTradeShare": r["topTradeShare"]})
        seen.add(key)
        added += 1
        if added >= 5:
            break

    return candidates, defaults


def main():
    rows = load_rows()
    candidates, defaults = select_candidates(rows)
    print(f"Selected {len(candidates)} candidates for validate()\n")

    results = []
    t0 = time.time()
    for i, c in enumerate(candidates):
        strat = c["strategy"]
        spec = {"strategy": strat, "strategy_parameters": dict(defaults[strat]),
                "position_size": float(c["shares"]), "allow_long": True, "allow_short": True}
        try:
            out = service.validate(c["symbol"], c["timeframe"], spec, bars=BARS, param_key=None)
            grade = out["verdict"]
            row = {
                **c, "verdict": grade, "gates": out["gates"], "why": out["why"],
                "fullNet": out["full"]["net"], "fullBh": out["full"]["bh"],
                "fullVsBH": out["full"]["vsBH"], "fullTrades": out["full"]["trades"],
                "fullTopTradeShare": out["full"]["topTradeShare"],
                "fullSharpe": out["full"]["sharpe"],
                "devBeatsBH": out["dev"]["beatsBH"], "devTrades": out["dev"]["trades"],
                "oosBeatsBH": out["oos"]["beatsBH"], "oosTrades": out["oos"]["trades"],
                "oosVsBH": out["oos"]["vsBH"],
                "survivesTicks": out["survivesTicks"],
                "topRemoval": out["topRemoval"],
                "foldsWon": out["foldsWon"], "foldsRun": out["foldsRun"],
                "folds": out["folds"],
                "paramKey": out["paramKey"], "variationsHeldPct": out["variationsHeldPct"],
                "variations": out["variations"],
                "warnings": out.get("warnings", []),
            }
        except Exception as e:
            row = {**c, "verdict": "ERROR", "error": f"{type(e).__name__}: {e}"}
        results.append(row)
        print(f"[{i+1:>2}/{len(candidates)}] {c['group']:38} {c['symbol']:5}{c['timeframe']:4}"
              f"{c['strategy']:22} -> {row['verdict']}")

    elapsed = time.time() - t0
    print(f"\n{len(results)} validate() calls in {elapsed:.1f}s")

    dist = {}
    for r in results:
        dist[r["verdict"]] = dist.get(r["verdict"], 0) + 1
    print("Grade distribution:", dist)

    OUT_PATH.write_text(json.dumps(results, indent=2))
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
