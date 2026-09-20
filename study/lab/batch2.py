"""Batch 2 — the five new strategy families across all markets and timeframes."""
import study.lab.strats as strats
from study.lab import harness, board, data


def main():
    for sym in ("/NQ", "/GC", "/CL"):
        for tf in ("30m", "1h", "4h"):
            c = data.load(sym, tf)
            print(f"\n  {sym} {tf}  ({len(c)} bars)")
            for name, spec in strats.specs(tf):
                try:
                    r = harness.run(sym, tf, spec, c)
                except Exception as e:
                    print(f"      {name:<30} FAILED {str(e)[:70]}"); continue
                n = board.add(batch="new-families", symbol=sym, tf=tf, name=name,
                              spec=spec, result=r)
                print(board.line(n, sym, tf, name, r))


if __name__ == "__main__":
    main()
