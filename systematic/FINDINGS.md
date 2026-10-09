# Systematic pipeline — calibration findings (2026-09-26)

The pipeline's first run graded **everything FAILED** on a 10-stock mega-cap
sample and concluded the rules were worthless. Two calibration choices, not the
rules, were doing most of that work. Correcting them produced the **first
MODERATE grades in this project's history**.

## 1. The cost model is calibrated per contract, not per share

`metrics.COMMISSION` is **$2.50 per unit per side**. That is a futures/forex
per-contract figure. Charged per *share* it costs ~$1,095 on a 219-share round
trip. In the first real-data paper replay it consumed **$5,755 of $8,226 gross
profit (70%)** across 7 winning trades.

Realistic US equity cost is ~$0.005/share (IBKR pro) or $0 retail.

| Run (37-symbol diversified list, 600 daily bars) | FAILED | FRAGILE | MODERATE |
|---|---|---|---|
| Lab cost ($2.50/share) | 102 | 3 | 0 |
| Stock cost ($0.005/share) | 66 | **20** | 0 |

**17 candidates moved out of FAILED on the cost fix alone.**

## 2. The 600-bar window cannot reach the MODERATE trade gate

`verdict.validate` requires **≥30 trades** for MODERATE and ≥100 for ROBUST.
Daily mean reversion fires ~16 times in 600 bars (~2.4 years). The grade was
**window-limited, not quality-limited**: PG showed 5/5 folds, 100% of parameter
variations holding, and a 13.9% top-trade share — and still could not be graded
above FRAGILE, because 16 < 30.

| Run (stock costs) | FAILED | FRAGILE | MODERATE |
|---|---|---|---|
| 600 bars (~2.4y) | 66 | 20 | 0 |
| 2500 bars (~10y) | 45 | 29 | **7** |

Promoted at the default MODERATE floor: **VZ, IEF, TLT, GIS, NKE**.

Best candidate — VZ MEAN_REVERSION: 51 trades, 5/5 walk-forward folds, 100% of
parameter variations still beat, top trade 7.4% of gross profit, +16.4%
out-of-sample, 15.3% max drawdown, Sharpe 0.67. Short of ROBUST only on the
100-trade gate.

### Correction

`CLAUDE.md` previously said the all-FAILED result "isn't a cost or window
artefact." That was measured on the 10 mega-cap sample, where buy & hold
dominance swamps both effects. On a regime-diverse list it does not hold: costs
and window are jointly worth 0 → 7 MODERATE.

## 3. These rules breach FTMO's daily loss limit

The 250-day paper replay of the 5 promoted rules **halted on an MDL breach**:
balance had grown to ~$104.7k, four open positions carried ~-$5.3k of float, and
equity fell more than 5% below the day's starting balance.

That is correct behaviour by the risk system, and a real problem with the
strategies: daily mean reversion holds through drawdowns deep enough to fail an
FTMO challenge. **Position sizing, not rule selection, is the open work.** Equal
weight at 1/5 of equity is too large for rules that sit through this much float.

## Honest caveats

- **Multiple comparisons.** 37 symbols x 15 strategies = 555 screens. With that
  many tries some rules clear the gates by luck. The OOS, walk-forward and
  sensitivity gates reduce this but do not remove it. 7/555 clearing MODERATE is
  not obviously better than chance and **must be confirmed forward**.
- **The replay is look-ahead contaminated.** The promoted rules were validated on
  data that includes the replay window, so replay P&L is a *plumbing check*, not
  a forward test.
- **Nothing here is ROBUST.** Zero rules clear the strict gate. The standing
  conclusion — no validated edge yet — is unchanged in kind, only in degree.

## Reproducing

```bash
PYTHONPATH=. venv/bin/python3 -m systematic research \
  --watchlist systematic/watchlists/diversified.txt \
  --settings systematic/settings/stock-costs-10y.yaml \
  --state systematic/state-10y

PYTHONPATH=. venv/bin/python3 scripts/paper_replay.py \
  --state systematic/state-10y \
  --settings systematic/settings/stock-costs-10y.yaml --days 250
```

`--settings` must match between the two, or the replay charges different costs
than research graded under. (It did on the first attempt: $13,920 of phantom
commission produced a spurious halt.)
