"""Short-horizon mean reversion: fade the trailing N-day return. Equities only
(crypto exhibits short-horizon momentum, not reversal, in the literature).
Sized inversely to volatility; no threshold parameters."""
import numpy as np
import pandas as pd


def mean_reversion(close: pd.DataFrame, lookback: int = 5,
                   vol_lookback: int = 20) -> pd.DataFrame:
    close = close.ffill()
    ret = close.pct_change()
    vol = ret.rolling(vol_lookback).std() * np.sqrt(252)
    mom = close / close.shift(lookback) - 1.0
    expo = -mom / vol.replace(0, np.nan) * 0.10
    return expo.clip(-1, 1)
