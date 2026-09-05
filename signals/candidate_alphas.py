"""Externally proposed alphas, implemented faithfully and evaluated.

None of these are in the shipped book. They are kept because a rejection is
only worth anything if it is reproducible: `run_validation.py` runs them and
writes output/candidate_alphas.csv, and the report quotes the numbers.

The operator vocabulary below mirrors the platform the expressions were written
for (WorldQuant-style `across_*` for cross-sectional, `ts_*`/`roll_*` for
time-series) so the translation can be checked line by line against the source.

All three are fast signals, which is exactly the class that the trading-calendar
lag bug used to flatter -- see test_lag_counted_in_each_assets_own_calendar.
They are evaluated on the fixed engine.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# --- operator vocabulary ----------------------------------------------------


def roll_returns(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x / x.shift(d) - 1.0


def roll_delta(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x - x.shift(d)


ts_delta = roll_delta


def roll_mean(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d).mean()


def ts_std(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d).std()


def ts_var(x: pd.DataFrame, d: int) -> pd.DataFrame:
    return x.rolling(d).var()


def across_rank(x: pd.DataFrame) -> pd.DataFrame:
    return x.rank(axis=1, pct=True)


def across_zscore(x: pd.DataFrame) -> pd.DataFrame:
    return x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1).replace(0, np.nan), axis=0)


def across_winsorize(x: pd.DataFrame, n: float) -> pd.DataFrame:
    mu, sd = x.mean(axis=1), x.std(axis=1)
    return x.clip(lower=mu - n * sd, upper=mu + n * sd, axis=0)


def roll_decay_linear(x: pd.DataFrame, d: int) -> pd.DataFrame:
    w = np.arange(d, 0, -1, dtype=float)
    w /= w.sum()
    return x.rolling(d).apply(lambda v: float(v @ w), raw=True)


def ta_rsi(x: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    delta = x.diff()
    up = delta.clip(lower=0.0)
    down = (-delta).clip(lower=0.0)
    roll_up = up.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    roll_dn = down.ewm(alpha=1 / n, min_periods=n, adjust=False).mean()
    return 100 - 100 / (1 + roll_up / roll_dn.replace(0, np.nan))


def ts_mdd_abs(ret: pd.DataFrame, d: int) -> pd.DataFrame:
    """Worst peak-to-trough of the compounded path inside a d-day window (<= 0)."""
    def _mdd(v):
        nav = np.cumprod(1.0 + v)
        return float((nav / np.maximum.accumulate(nav) - 1.0).min())
    return ret.rolling(d).apply(_mdd, raw=True)


def ts_quantile(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Where today's value sits inside its own trailing d-day distribution."""
    return x.rolling(d).apply(lambda v: float((v[:-1] < v[-1]).mean()), raw=True)


def if_else(cond: pd.DataFrame, a, b) -> pd.DataFrame:
    def _frame(z):
        return z if isinstance(z, pd.DataFrame) else pd.DataFrame(
            z, index=cond.index, columns=cond.columns)
    return _frame(a).where(cond.astype(bool), _frame(b))


bool_if = if_else


# --- portfolio construction -------------------------------------------------


def dollar_neutral(alpha: pd.DataFrame, columns: pd.Index, gross: float = 1.0) -> pd.DataFrame:
    a = alpha.replace([np.inf, -np.inf], np.nan)
    a = a.sub(a.mean(axis=1), axis=0)
    w = a.div(a.abs().sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0) * gross
    out = pd.DataFrame(0.0, index=alpha.index, columns=columns)
    out[alpha.columns] = w
    return out


def long_only(alpha: pd.DataFrame, columns: pd.Index, gross: float = 1.0) -> pd.DataFrame:
    a = alpha.replace([np.inf, -np.inf], np.nan).clip(lower=0.0).fillna(0.0)
    w = a.div(a.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0) * gross
    out = pd.DataFrame(0.0, index=alpha.index, columns=columns)
    out[alpha.columns] = w
    return out


# --- the alphas -------------------------------------------------------------


def alpha_reversal_decay(close: pd.DataFrame, names: list[str]) -> pd.DataFrame:
    """roll_decay_linear(across_winsorize(across_rank(-roll_returns(close,1)),4),5)

    One-day cross-sectional reversal, smoothed with a 5-day linear decay. Note
    that the winsorize is a no-op as written: `across_rank` already returns
    values in [0,1], whose cross-sectional standard deviation is ~0.29, so a
    4-sigma clip never binds.
    """
    px = close[names]
    return roll_decay_linear(across_winsorize(across_rank(-roll_returns(px, 1)), 4), 5)


def alpha_rsi_volume(close: pd.DataFrame, volume: pd.DataFrame,
                     names: list[str]) -> pd.DataFrame:
    """RSI delta momentum scaled by relative dollar volume, vol-filtered."""
    px, vol = close[names], volume[names]
    rsi_mom = roll_delta(ta_rsi(px, 14), 3)
    dollar_vol = vol * px
    vol_ratio = dollar_vol / roll_mean(dollar_vol, 20)
    alpha_combined = rsi_mom * vol_ratio

    rets = px.pct_change()
    rel_vol = ts_std(rets, 5) / (ts_std(rets, 20) + 1e-6)
    vol_filter = rel_vol < 1.2

    dd = ts_mdd_abs(rets, 5)
    risk_scale = bool_if(dd < -0.03, 1.3, 1.0)

    signal = bool_if(vol_filter, alpha_combined, 0.0)
    return across_zscore(across_rank(signal) * risk_scale)


def alpha_crypto_rsi_gate(close: pd.DataFrame, volume: pd.DataFrame,
                          names: list[str]) -> pd.DataFrame:
    """Long-only crypto: RSI/return/vol entry gates, inverse-variance sizing.

    `risk_adjust` sits in the denominator, so a deeper recent drawdown shrinks
    the position rather than growing it.
    """
    px, vol = close[names], volume[names]
    rets = px.pct_change()

    rsi = ta_rsi(px, 14)
    rsi_delta = ts_delta(rsi, 1)
    entry = (((rsi > 50) & (rsi < 80)).astype(float)
             + (rsi < 29).astype(float)
             + ((rsi_delta > 0) & (rsi > 30)).astype(float))
    entry1 = ((rets >= 0.03) | (rets <= -0.01)).astype(float)
    entry2 = (ts_std(rets, 7) <= 0.02693).astype(float)

    dd_index = ts_mdd_abs(rets, 5)
    risk_adjust = if_else(dd_index < -0.04, 1.5,
                          if_else(dd_index < -0.03, 1.3,
                                  if_else(dd_index < -0.02, 1.15,
                                          if_else(dd_index < -0.01, 1.1, 1.0))))
    denom = (ts_var(rets, 60) * risk_adjust) ** 1.1
    alpha_raw = ts_quantile(px * vol, 60) / denom.replace(0, np.nan)

    gate = ((1 - entry1) * entry * entry2) > 0
    return if_else(gate, alpha_raw, 0.0)
