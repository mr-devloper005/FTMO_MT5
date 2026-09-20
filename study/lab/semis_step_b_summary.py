"""Step B of the Lab semiconductor research task: primary in-sample summary
tables built from the pre-existing sweep at semis-sized-2156-runs.csv
(14 symbols x 11 timeframes x 14 strategies = 2156 rows).

Read-only analysis script. Does not call any Lab function — pure CSV
aggregation. Kept per the study/lab/ convention; not part of the application.

Run: venv/bin/python3 study/lab/semis_step_b_summary.py
"""
import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CSV_PATH = REPO / "semis-sized-2156-runs.csv"
OUT_PATH = REPO / "study" / "lab" / "semis_step_b_summary.json"

SYMBOLS = ["AMAT", "AMD", "ARM", "CRDO", "DELL", "LITE", "MRVL", "MU",
           "NBIS", "NVDA", "SMH", "SNDK", "TSM", "WDC"]
TIMEFRAMES = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w"]

BOOL = lambda s: s.strip().lower() == "true"
FLT = lambda s: float(s) if s not in (None, "", "None") else None
INT = lambda s: int(float(s)) if s not in (None, "", "None") else None


def load_rows():
    with open(CSV_PATH, newline="") as f:
        r = csv.DictReader(f)
        rows = []
        for row in r:
            row["trades"] = INT(row["trades"])
            row["winRate"] = FLT(row["winRate"])
            row["net"] = FLT(row["net"])
            row["returnPct"] = FLT(row["returnPct"])
            row["bh"] = FLT(row["bh"])
            row["bhReturnPct"] = FLT(row["bhReturnPct"])
            row["vsBH"] = FLT(row["vsBH"])
            row["vsBHPct"] = FLT(row["vsBHPct"])
            row["beatPct"] = FLT(row.get("beatPct"))
            row["beatsBH"] = BOOL(row["beatsBH"])
            row["pf"] = FLT(row["pf"])
            row["maxDDPct"] = FLT(row["maxDDPct"])
            row["bhDDPct"] = FLT(row["bhDDPct"])
            row["sharpe"] = FLT(row["sharpe"])
            row["topTradeShare"] = FLT(row["topTradeShare"])
            row["timeInMarket"] = FLT(row["timeInMarket"])
            row["shares"] = INT(row["shares"])
            row["bars"] = INT(row["bars"])
            row["usableBars"] = INT(row["usableBars"])
            rows.append(row)
    return rows


def meaningful(row):
    """The Lab's own statistical-significance / real-opponent gates."""
    return (row["trades"] is not None and row["trades"] >= 10
            and row["bh"] is not None and abs(row["bh"]) >= 1000.0)


def main():
    rows = load_rows()
    print(f"Loaded {len(rows)} rows from {CSV_PATH.name}")

    combos = {(r["symbol"], r["timeframe"], r["strategy"]) for r in rows}
    expected = len(SYMBOLS) * len(TIMEFRAMES) * 14
    print(f"Distinct (symbol,timeframe,strategy) combos: {len(combos)} "
          f"(expected {expected} = 14 symbols x 11 tfs x 14 strategies)")

    missing = []
    for s in SYMBOLS:
        for tf in TIMEFRAMES:
            for strat in {r["strategy"] for r in rows}:
                pass
    strategies = sorted({r["strategy"] for r in rows})
    print(f"Distinct strategies present: {len(strategies)} -> {strategies}")

    for s in SYMBOLS:
        for tf in TIMEFRAMES:
            n = len([r for r in rows if r["symbol"] == s and r["timeframe"] == tf])
            if n != len(strategies):
                missing.append((s, tf, n))
    if missing:
        print("Symbol/timeframe cells with unexpected row counts:", missing)
    else:
        print("Every (symbol, timeframe) cell has exactly one row per strategy. No gaps.")

    out = {"meta": {}, "per_symbol": {}, "per_timeframe": {}, "per_strategy": {}}

    out["meta"] = {
        "totalRows": len(rows),
        "distinctCombos": len(combos),
        "strategies": strategies,
        "meaningfulRows": sum(1 for r in rows if meaningful(r)),
        "meaningfulBeatsBH": sum(1 for r in rows if meaningful(r) and r["beatsBH"]),
        "lowTradeRows": sum(1 for r in rows if r["trades"] is not None and r["trades"] < 10),
        "flatBenchmarkRows": sum(1 for r in rows if r["bh"] is not None and abs(r["bh"]) < 1000.0),
        "highConcentrationRows": sum(1 for r in rows if r["topTradeShare"] is not None and r["topTradeShare"] >= 50),
    }

    # ---------------- per symbol ----------------
    for s in SYMBOLS:
        srows = [r for r in rows if r["symbol"] == s]
        mrows = [r for r in srows if meaningful(r)]
        beats = [r for r in mrows if r["beatsBH"]]
        best = max(mrows, key=lambda r: r["vsBH"]) if mrows else None
        worst = min(mrows, key=lambda r: r["vsBH"]) if mrows else None
        out["per_symbol"][s] = {
            "totalCombos": len(srows),
            "meaningfulCombos": len(mrows),
            "meaningfulBeatsBH": len(beats),
            "beatRatePct": round(len(beats) / len(mrows) * 100, 1) if mrows else None,
            "best": {
                "timeframe": best["timeframe"], "strategy": best["strategy"],
                "vsBH": best["vsBH"], "vsBHPct": best["vsBHPct"], "trades": best["trades"],
                "net": best["net"], "bh": best["bh"], "topTradeShare": best["topTradeShare"],
                "sharpe": best["sharpe"], "pf": best["pf"],
            } if best else None,
            "worst": {
                "timeframe": worst["timeframe"], "strategy": worst["strategy"],
                "vsBH": worst["vsBH"], "trades": worst["trades"], "net": worst["net"],
                "bh": worst["bh"],
            } if worst else None,
            "tradeCountRange": [min(r["trades"] for r in srows), max(r["trades"] for r in srows)],
        }

    # ---------------- per timeframe ----------------
    for tf in TIMEFRAMES:
        trows = [r for r in rows if r["timeframe"] == tf]
        mrows = [r for r in trows if meaningful(r)]
        beats = [r for r in mrows if r["beatsBH"]]
        sharpes = [r["sharpe"] for r in trows if r["sharpe"] is not None]
        out["per_timeframe"][tf] = {
            "totalCombos": len(trows),
            "meaningfulCombos": len(mrows),
            "beatRatePctOfMeaningful": round(len(beats) / len(mrows) * 100, 1) if mrows else None,
            "beatRatePctOfAll": round(sum(1 for r in trows if r["beatsBH"]) / len(trows) * 100, 1) if trows else None,
            "avgVsBHPct": round(statistics.fmean(r["vsBHPct"] for r in trows if r["vsBHPct"] is not None), 2),
            "avgTrades": round(statistics.fmean(r["trades"] for r in trows if r["trades"] is not None), 1),
            "avgSharpe": round(statistics.fmean(sharpes), 3) if sharpes else None,
            "avgTopTradeShare": round(statistics.fmean(r["topTradeShare"] for r in trows if r["topTradeShare"] is not None), 1),
            "flatBenchmarkRows": sum(1 for r in trows if r["bh"] is not None and abs(r["bh"]) < 1000.0),
        }

    # ---------------- per strategy ----------------
    for strat in strategies:
        strows = [r for r in rows if r["strategy"] == strat]
        mrows = [r for r in strows if meaningful(r)]
        beats = [r for r in mrows if r["beatsBH"]]
        tts = [r["topTradeShare"] for r in strows if r["topTradeShare"] is not None]
        out["per_strategy"][strat] = {
            "totalCombos": len(strows),
            "meaningfulCombos": len(mrows),
            "beatRatePctOfMeaningful": round(len(beats) / len(mrows) * 100, 1) if mrows else None,
            "avgVsBHPct": round(statistics.fmean(r["vsBHPct"] for r in strows if r["vsBHPct"] is not None), 2),
            "avgTopTradeShare": round(statistics.fmean(tts), 1) if tts else None,
            "pctHighConcentration(>=50)": round(sum(1 for x in tts if x >= 50) / len(tts) * 100, 1) if tts else None,
            "avgTrades": round(statistics.fmean(r["trades"] for r in strows if r["trades"] is not None), 1),
        }

    # ---------------- top overall outliers (for Step C "too good to be true") ----------------
    ranked_by_vsbh = sorted([r for r in rows if r["bh"] is not None], key=lambda r: r["vsBH"] or 0, reverse=True)
    ranked_by_tts = sorted([r for r in rows if r["topTradeShare"] is not None], key=lambda r: r["topTradeShare"], reverse=True)
    out["topOutliersByVsBH"] = [
        {"symbol": r["symbol"], "timeframe": r["timeframe"], "strategy": r["strategy"],
         "vsBH": r["vsBH"], "vsBHPct": r["vsBHPct"], "trades": r["trades"], "bh": r["bh"],
         "topTradeShare": r["topTradeShare"], "beatsBH": r["beatsBH"]}
        for r in ranked_by_vsbh[:15]
    ]
    out["topOutliersByConcentration_amongBeaters"] = [
        {"symbol": r["symbol"], "timeframe": r["timeframe"], "strategy": r["strategy"],
         "vsBH": r["vsBH"], "trades": r["trades"], "topTradeShare": r["topTradeShare"], "beatsBH": r["beatsBH"]}
        for r in ranked_by_tts if r["beatsBH"]
    ][:15]

    OUT_PATH.write_text(json.dumps(out, indent=2))
    print(f"\nWrote {OUT_PATH}")

    # console summary
    print("\n=== PER-SYMBOL BEST (meaningful rows only) ===")
    for s, d in out["per_symbol"].items():
        b = d["best"]
        if b:
            print(f"{s:6} best={b['strategy']:22}{b['timeframe']:4} vsBH=${b['vsBH']:>10,.0f} "
                  f"({b['vsBHPct']:>6.1f}pp) trades={b['trades']:<4} tts={b['topTradeShare']:>5.1f}%  "
                  f"beatRate={d['beatRatePct']}% of {d['meaningfulCombos']} meaningful combos")
        else:
            print(f"{s:6} NO MEANINGFUL COMBOS")

    print("\n=== PER-TIMEFRAME ===")
    for tf, d in out["per_timeframe"].items():
        print(f"{tf:4} beatRate(meaningful)={d['beatRatePctOfMeaningful']}%  avgVsBHPct={d['avgVsBHPct']:>7.2f}  "
              f"avgTrades={d['avgTrades']:>6.1f}  avgSharpe={d['avgSharpe']}  flatBH={d['flatBenchmarkRows']}/{d['totalCombos']}")

    print("\n=== PER-STRATEGY ===")
    for strat, d in out["per_strategy"].items():
        print(f"{strat:22} beatRate(meaningful)={d['beatRatePctOfMeaningful']}%  avgVsBHPct={d['avgVsBHPct']:>7.2f}  "
              f"avgTTS={d['avgTopTradeShare']}%  %highConc={d['pctHighConcentration(>=50)']}%")


if __name__ == "__main__":
    main()
