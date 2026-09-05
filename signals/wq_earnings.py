"""Cross-sectional earnings tilt, adapted from a WorldQuant Brain expression:

    zscore(normalize(last_diff_value(zscore(abs(anl4_adjusted_netincome_ft)), 504),
                     useStd = False, limit = 0.0))

The licensed analyst-estimate input is substituted with realised TTM EPS
(default field: scale-free earnings yield = TTM EPS / price; `abs_eps`
reproduces the original scale-dependent input). Sign fixed a priori (long high
earnings yield). This sleeve is currently NOT in the shipped book: under
next-open fills its edge is negative in both windows -- it rebalanced on
freshly published fundamentals, so its return sat in the same-bar close we no
longer trade at. Kept because the negative result is part of the record.

Lookahead control: EPS is indexed by fiscal period end, which is not when the
number became public; every value is delayed `publication_lag_days` calendar
days before it can enter the signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def zscore(x: pd.DataFrame) -> pd.DataFrame:
    mu = x.mean(axis=1)
    sd = x.std(axis=1, ddof=0)
    return x.sub(mu, axis=0).div(sd.replace(0, np.nan), axis=0)


def normalize(x: pd.DataFrame, use_std: bool = False, limit: float = 0.0) -> pd.DataFrame:
    out = x.sub(x.mean(axis=1), axis=0)
    if use_std:
        out = out.div(x.std(axis=1, ddof=0).replace(0, np.nan), axis=0)
    if limit and limit > 0:
        out = out.clip(-limit, limit)
    return out


def last_diff_value(x: pd.DataFrame, d: int) -> pd.DataFrame:
    """Most recent value within the last `d` days differing from the current
    one. Strictly backward-looking."""
    A = x.to_numpy(dtype=float)
    n, m = A.shape
    out = np.full_like(A, np.nan)
    for i in range(n):
        cur = A[i]
        found = np.full(m, np.nan)
        pending = np.isfinite(cur)
        lo = max(0, i - d)
        for j in range(i - 1, lo - 1, -1):
            if not pending.any():
                break
            prev = A[j]
            hit = pending & np.isfinite(prev) & (prev != cur)
            if hit.any():
                found[hit] = prev[hit]
                pending &= ~hit
        out[i] = found
    return pd.DataFrame(out, index=x.index, columns=x.columns)


def _daily_fundamental(eps: pd.DataFrame, index: pd.DatetimeIndex,
                       publication_lag_days: int) -> pd.DataFrame:
    """Step-expand quarterly EPS onto the daily grid, delayed by the lag."""
    lagged = eps.copy()
    lagged.index = lagged.index + pd.Timedelta(days=publication_lag_days)
    lagged = lagged[~lagged.index.duplicated(keep="last")].sort_index()
    return lagged.reindex(lagged.index.union(index)).ffill().reindex(index)


def wq_earnings_tilt(
    close: pd.DataFrame,
    eps: pd.DataFrame,
    field: str = "earnings_yield",
    window: int = 504,
    publication_lag_days: int = 60,
    max_abs_weight: float = 0.06,
    gross: float = 0.30,
    hysteresis: float = 0.01,
) -> pd.DataFrame:
    """(T x assets) dollar-neutral exposure; `gross` = sleeve sum|w|, on top of
    which the book allocator and 100% gross cap apply."""
    px = close.ffill()
    names = [c for c in eps.columns if c in px.columns]
    if not names:
        raise ValueError("no overlap between EPS panel and price universe")

    eps_daily = _daily_fundamental(eps[names], px.index, publication_lag_days)

    if field == "earnings_yield":
        raw = eps_daily / px[names]
    elif field == "abs_eps":
        raw = eps_daily.abs()
    else:
        raise ValueError(f"unknown field {field!r}")

    # zscore(normalize(last_diff_value(zscore(raw), window), useStd=False, limit=0))
    inner = zscore(raw)
    stepped = last_diff_value(inner, window)
    alpha = zscore(normalize(stepped, use_std=False, limit=0.0))

    alpha = alpha.replace([np.inf, -np.inf], np.nan)
    # re-demean so the sleeve stays dollar-neutral when names drop in/out
    alpha = alpha.sub(alpha.mean(axis=1), axis=0)
    w = alpha.div(alpha.abs().sum(axis=1).replace(0, np.nan), axis=0) * gross
    w = w.clip(-max_abs_weight, max_abs_weight).fillna(0.0)

    expo = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    expo[names] = w

    from signals.equity_core import _hysteresis
    return _hysteresis(expo, hysteresis)
