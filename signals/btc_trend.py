"""BTC trend-follow: the return engine. EMA(50/200) regime with RSI
confirmation, applied to the whole crypto complex, inverse-vol sized."""
from __future__ import annotations

import numpy as np
import pandas as pd


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100.0 - (100.0 / (1.0 + rs))


def _apply_hysteresis(target: pd.DataFrame, band: float) -> pd.DataFrame:
    T = target.to_numpy(dtype=float)
    out = np.zeros_like(T)
    cur = np.zeros(T.shape[1])
    for i in range(T.shape[0]):
        row = np.where(np.isfinite(T[i]), T[i], 0.0)
        diff = row - cur
        cur = np.where(np.abs(diff) > band, cur + diff, cur)
        out[i] = cur
    return pd.DataFrame(out, index=target.index, columns=target.columns)


def btc_regime(
    close: pd.DataFrame,
    btc: str = "BTCUSDT",
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    rsi_long_lo: float = 50.0,
    rsi_long_hi: float = 85.0,
    rsi_short_lo: float = 15.0,
    rsi_short_hi: float = 50.0,
) -> pd.Series:
    """+1 bull / -1 bear / 0 neutral, from BTC price only."""
    if btc not in close.columns:
        raise KeyError(f"{btc} missing from close panel")
    px = close[btc].ffill()
    # min_periods: without it the first ema_slow values of an "EMA200" are a
    # short-window average, which would hand the sleeve a spurious signal over
    # BTC's earliest history
    ema_f = px.ewm(span=ema_fast, min_periods=ema_fast, adjust=False).mean()
    ema_s = px.ewm(span=ema_slow, min_periods=ema_slow, adjust=False).mean()
    rsi = _rsi(px, rsi_period)

    bull = (px > ema_s) & (ema_f > ema_s) & (rsi >= rsi_long_lo) & (rsi <= rsi_long_hi)
    bear = (px < ema_s) & (ema_f < ema_s) & (rsi >= rsi_short_lo) & (rsi <= rsi_short_hi)
    regime = pd.Series(0.0, index=px.index)
    return regime.mask(bull, 1.0).mask(bear, -1.0)


def btc_trend_follow(
    close: pd.DataFrame,
    btc: str = "BTCUSDT",
    ema_fast: int = 50,
    ema_slow: int = 200,
    rsi_period: int = 14,
    rsi_long_lo: float = 50.0,
    rsi_long_hi: float = 85.0,
    rsi_short_lo: float = 15.0,
    rsi_short_hi: float = 50.0,
    vol_lookback: int = 20,
    sleeve_vol_target: float = 0.15,
    gross: float = 1.00,
    hysteresis: float = 0.06,
) -> pd.DataFrame:
    """Return (T x assets) crypto-only exposure, gross budget = `gross`."""
    px = close.ffill()
    crypto = [c for c in px.columns if c.endswith("USDT")]

    regime = btc_regime(close, btc, ema_fast, ema_slow, rsi_period,
                        rsi_long_lo, rsi_long_hi, rsi_short_lo, rsi_short_hi)

    ret = px[crypto].pct_change()
    vol = ret.rolling(vol_lookback).std() * np.sqrt(252)
    size = (sleeve_vol_target / vol.replace(0, np.nan)).clip(upper=1.0)
    size = size.where(px[crypto].notna())

    raw = size.mul(regime, axis=0)
    denom = raw.abs().sum(axis=1).replace(0, np.nan)
    w = raw.div(denom, axis=0).mul(regime.abs() * gross, axis=0).fillna(0.0)

    expo = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    expo[crypto] = w
    return _apply_hysteresis(expo.clip(-1, 1), hysteresis)
