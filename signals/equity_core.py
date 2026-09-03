"""Equity core: long inverse-vol basket of US large caps, gated by the SPY
200-day trend. The hit-rate engine: a long-only basket is positive in most
months, and the gate shortens drawdowns in bear regimes."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _hysteresis(target: pd.DataFrame, band: float) -> pd.DataFrame:
    """Only trade when the target moves more than `band`."""
    T = target.to_numpy(dtype=float)
    out = np.zeros_like(T)
    cur = np.zeros(T.shape[1])
    for i in range(T.shape[0]):
        row = np.where(np.isfinite(T[i]), T[i], 0.0)
        diff = row - cur
        cur = np.where(np.abs(diff) > band, cur + diff, cur)
        out[i] = cur
    return pd.DataFrame(out, index=target.index, columns=target.columns)


def index_uptrend(close: pd.DataFrame, index_symbol: str = "SPY",
                  lookback: int = 200, slope_lookback: int = 20) -> pd.Series:
    px = close[index_symbol].ffill()
    sma = px.rolling(lookback).mean()
    return (px > sma) & (sma > sma.shift(slope_lookback))


def equity_core(
    close: pd.DataFrame,
    equities: list[str],
    index_symbol: str = "SPY",
    index_lookback: int = 200,
    vol_lookback: int = 60,
    on_weight: float = 0.60,
    off_weight: float = 0.15,
    hysteresis: float = 0.01,
) -> pd.DataFrame:
    """Return (T x assets) long-only equity exposure in [0, 1]."""
    px = close.ffill()
    names = [c for c in equities if c in px.columns]

    risk_on = index_uptrend(px, index_symbol, index_lookback).astype(float)
    regime_w = risk_on * on_weight + (1.0 - risk_on) * off_weight

    ret = px[names].pct_change()
    vol = ret.rolling(vol_lookback).std() * np.sqrt(252)
    inv = 1.0 / vol.replace(0, np.nan)
    # a name only enters once it has a full vol window of live history
    inv = inv.where(px[names].notna())
    w = inv.div(inv.sum(axis=1), axis=0)

    expo = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    expo[names] = w.mul(regime_w, axis=0).fillna(0.0)
    return _hysteresis(expo.clip(0.0, 1.0), hysteresis)
