"""Cost model: flat commission per side + slippage proportional to realized vol.

commission per side:
  - liquid US equities: 1.5 bps (mid of the stated 1-2 bp band; universe is
    large-cap so the low end is defensible)
  - crypto spot: 10 bps (typical taker fee on major venues)
slippage per side: SLIPPAGE_VOL_FRACTION * one-day realized vol of the asset
  (captures spread + impact scaling with volatility; crypto and high-vol names
  automatically cost more).
"""
import numpy as np
import pandas as pd

import config


def cost_bps_panel(assets: list[str]) -> pd.Series:
    bps = {}
    for a in assets:
        bps[a] = config.CRYPTO_BPS if a.endswith("USDT") else config.EQUITY_BPS
    return pd.Series(bps, dtype=float)


def turnover_cost(turnover: pd.DataFrame, ret: pd.DataFrame) -> pd.DataFrame:
    """Daily cost return series from turnover (|change in exposure| per asset).

    turnover, ret: (T x N) indexed the same. Cost charged on the day the trade
    is placed, in return terms: sum_i turnover[t,i] * cost_rate[i].
    """
    rates = cost_bps_panel(list(turnover.columns)) / 1e4
    slip = ret.rolling(config.VOL_LOOKBACK).std().fillna(0.0) * config.SLIPPAGE_VOL_FRACTION
    total_rate = rates + slip
    return (turnover.abs() * total_rate).sum(axis=1)
