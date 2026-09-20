"""Black-76 pricing for options on futures.

Why a model at all
------------------
Backtesting an options strategy properly needs historical option prices. No
source available to this application has them for futures options: Yahoo
publishes none for /NQ, and tastytrade serves only the live chain. Paid feeds
(CME/OPRA history via Databento or CBOE DataShop) are the real answer.

So option prices here are *modelled* from the futures price, which we do have.
Black-76 is the standard model for options on futures — the same one exchanges
use to publish settlement greeks. That makes the result a defensible estimate
and emphatically not a record of what these contracts actually traded at. Every
result carries that warning.

What the model gets wrong, and which way
---------------------------------------
* Implied volatility is not observable here, so it is estimated from the
  underlying's own realized volatility. Real option IV is usually *higher* than
  realized — the variance risk premium — which means a short-premium strategy
  backtested this way collects **less** credit than reality and looks worse
  than it was. That direction is the safe one to be wrong in.
* No volatility smile: every strike gets the same vol. ATM options are least
  affected by this, which is why the ATM case is the one implemented.
* No bid/ask spread on the option itself. Configure slippage to approximate it.
* No early exercise. Most futures options are American; this prices them
  European. For short ATM positions held to expiry the difference is small.
"""

__all__ = ["black76_price", "black76_delta", "intrinsic_value", "realized_volatility"]

import math
from typing import Sequence

# Below this, time value is meaningless and the option is worth its intrinsic.
_MIN_YEARS = 1e-9
_MIN_VOL = 1e-9


def _norm_cdf(x: float) -> float:
    """Standard normal CDF, via the error function."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def intrinsic_value(option_type: str, future: float, strike: float) -> float:
    """What the option is worth if it expired right now."""
    return (max(future - strike, 0.0) if option_type.upper().startswith("C")
            else max(strike - future, 0.0))


def black76_price(option_type: str, future: float, strike: float,
                  years_to_expiry: float, volatility: float,
                  rate: float = 0.0) -> float:
    """Black-76 premium, in the same units as `future`.

    Args:
        option_type: "call"/"C" or "put"/"P".
        future: Current futures price.
        strike: Option strike.
        years_to_expiry: Time remaining, in years. Zero or less gives intrinsic.
        volatility: Annualized volatility as a decimal (0.20 = 20%).
        rate: Continuously compounded risk-free rate, for discounting.

    Returns:
        Premium in index points. Multiply by the contract multiplier for cash.
    """
    if years_to_expiry <= _MIN_YEARS or volatility <= _MIN_VOL:
        return intrinsic_value(option_type, future, strike)
    if future <= 0 or strike <= 0:
        return intrinsic_value(option_type, future, strike)

    root_t = math.sqrt(years_to_expiry)
    d1 = (math.log(future / strike) + 0.5 * volatility ** 2 * years_to_expiry) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discount = math.exp(-rate * years_to_expiry)

    if option_type.upper().startswith("C"):
        return discount * (future * _norm_cdf(d1) - strike * _norm_cdf(d2))
    return discount * (strike * _norm_cdf(-d2) - future * _norm_cdf(-d1))


def black76_delta(option_type: str, future: float, strike: float,
                  years_to_expiry: float, volatility: float,
                  rate: float = 0.0) -> float:
    """Sensitivity of the premium to a one-point move in the future."""
    if years_to_expiry <= _MIN_YEARS or volatility <= _MIN_VOL or future <= 0 or strike <= 0:
        itm = intrinsic_value(option_type, future, strike) > 0
        sign = 1.0 if option_type.upper().startswith("C") else -1.0
        return sign if itm else 0.0

    root_t = math.sqrt(years_to_expiry)
    d1 = (math.log(future / strike) + 0.5 * volatility ** 2 * years_to_expiry) / (volatility * root_t)
    discount = math.exp(-rate * years_to_expiry)

    if option_type.upper().startswith("C"):
        return discount * _norm_cdf(d1)
    return -discount * _norm_cdf(-d1)


def realized_volatility(closes: Sequence[float], periods_per_year: float,
                        lookback: int = 30) -> float:
    """Annualized volatility of log returns over the last `lookback` bars.

    Returns 0.0 when there is not enough history; callers treat that as
    "cannot price yet" rather than "the market is still".
    """
    if len(closes) < lookback + 1 or periods_per_year <= 0:
        return 0.0

    window = closes[-(lookback + 1):]
    returns = []
    for prev, cur in zip(window[:-1], window[1:]):
        if prev > 0 and cur > 0:
            returns.append(math.log(cur / prev))

    if len(returns) < 2:
        return 0.0

    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(periods_per_year)
