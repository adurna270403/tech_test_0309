"""Cross-sectional momentum: rank assets by trailing return, long the top
half, short the bottom half (dollar-neutral within the sleeve). The classic
Jegadeesh-Titman effect (12-1 month for equities); for crypto a 30d lookback
is the documented horizon. Rankings use only data through t."""
import numpy as np
import pandas as pd


def xs_momentum(close: pd.DataFrame, lookback: int = 252, skip: int = 21,
                vol_lookback: int = 20) -> pd.DataFrame:
    px = close.ffill()
    ret = px.pct_change()
    vol = ret.rolling(vol_lookback).std() * np.sqrt(252)
    mom = px / px.shift(lookback) - 1.0
    ranks = mom.rank(axis=1, pct=True) - 0.5  # in [-0.5, 0.5]
    # long top half, short bottom half
    sig = np.sign(ranks) * (ranks.abs() >= 0.25).astype(float)
    # risk scale per asset
    expo = sig * (0.10 / vol.replace(0, np.nan))
    return expo.clip(-1, 1)
