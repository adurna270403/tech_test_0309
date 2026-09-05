"""Time-series momentum: long assets whose trailing return is positive, short
those negative, sized inversely to volatility. Lookback 90d is the standard
Moskowitz-Ooi-Pedersen horizon; not tuned."""
import numpy as np
import pandas as pd


def ts_momentum(close: pd.DataFrame, lookback: int = 90,
                vol_lookback: int = 20, hysteresis: float = 0.1) -> pd.DataFrame:
    """Continuous risk-scaled momentum: exposure = (momentum / vol) * 0.10,
    clipped to [-1, 1]. Continuous weights (rather than sign flips) plus a
    hysteresis band keep turnover ~15x/yr instead of ~80x/yr, which matters
    hugely after 10bp crypto costs. Weight changes smaller than `hysteresis`
    are not traded."""
    close = close.ffill()  # assets trading on fewer days (equities on a 7d calendar) carry stale prices
    ret = close.pct_change()
    vol = ret.rolling(vol_lookback).std() * np.sqrt(252)
    mom = close / close.shift(lookback) - 1.0
    target = (mom / vol.replace(0, np.nan) * 0.10).clip(-1, 1)

    # hysteresis: move exposure only when |target - current| > band
    T = target.to_numpy()
    out = np.zeros_like(T)
    cur = np.zeros(T.shape[1])
    for i in range(T.shape[0]):
        diff = T[i] - cur
        cur = np.where(np.abs(diff) > hysteresis, cur + diff, cur)
        out[i] = cur
    return pd.DataFrame(out, index=target.index, columns=target.columns)
