"""Same-weekday cross-sectional momentum in crypto (Long 2020, Finance
Research Letters): a coin's return on a given weekday k weeks ago positively
predicts its relative performance over the following week. Signals are formed
once per week (Friday close) and held flat, so turnover is low; the engine
still fills at the next open.

The signal universe is the extended Binance USDT cross-section (delisted names
included), but each day only the coins in the top of the trailing
dollar-volume ranking are eligible — a point-in-time liquidity filter, so
illiquid names never enter the book, and names that delist simply drop out of
the ranking before they stop trading.

Reference: Long, H. (2020). Seasonality in the Cross-Section of Cryptocurrency
Returns. Finance Research Letters.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

DEFAULT_LOOKBACK_WEEKS = 3
DEFAULT_N_LONG = 4
DEFAULT_ADV_WINDOW = 20
DEFAULT_N_LIQUID = 12


def weekday_momentum_signal(close: pd.DataFrame, k: int = DEFAULT_LOOKBACK_WEEKS) -> pd.DataFrame:
    """Coin return over the same weekday k weeks back, sampled at Friday close
    and forward-filled to the daily index. Causal: uses only completed weeks."""
    weekly = close.resample("W-FRI").last()
    return weekly.pct_change(k).reindex(close.index, method="ffill")


def liquidity_mask(volume: pd.DataFrame, close: pd.DataFrame,
                   window: int = DEFAULT_ADV_WINDOW,
                   n_liquid: int = DEFAULT_N_LIQUID) -> pd.DataFrame:
    """True where the coin's trailing dollar volume ranks in the daily top n_liquid."""
    adv = (volume * close).rolling(window).mean()
    return adv.rank(axis=1, ascending=False) <= n_liquid


def wd_momentum(close: pd.DataFrame, k: int = DEFAULT_LOOKBACK_WEEKS,
                n_long: int = DEFAULT_N_LONG,
                volume: pd.DataFrame | None = None,
                n_liquid: int | None = None) -> pd.DataFrame:
    """Equal-weight the n_long coins with the strongest same-weekday momentum.

    With `volume` passed, coins outside the top `n_liquid` by trailing dollar
    volume are ineligible. Zero when fewer than n_long coins have a completed,
    eligible signal (start of sample).
    """
    sig = weekday_momentum_signal(close, k=k)
    rank = sig.rank(axis=1, ascending=False)
    mask = (rank <= n_long).astype(float)
    if volume is not None and n_liquid is not None:
        mask = mask.where(liquidity_mask(volume, close, n_liquid=n_liquid), 0.0)
    mask = mask.where(sig.notna(), 0.0)
    gross = mask.sum(axis=1)
    expo = mask.div(gross.replace(0, pd.NA), axis=0)
    return expo.fillna(0.0)
