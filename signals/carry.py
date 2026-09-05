"""Carry sleeve: collect perp funding by holding spot against a short perp
(delta-neutral cash-and-carry). P&L = funding + basis convergence, no price
risk beyond basis noise. On when trailing funding is positive, off when it is
not -- the gate is on the carry itself, never on price.

The perp is traded through a synthetic funding-adjusted price panel
(<SYM>-PERP.parquet): its return vs spot equals -(daily funding), so standard
backtester P&L on the pair equals the funding stream. A position of size s in
both legs uses 2s of gross notional (counted by the engine's gross cap), so
carry displaces other gross one-for-one on each leg.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def carry_pair_exposure(funding: pd.DataFrame, lookback: int = 21,
                        max_pair: float = 1.0) -> pd.DataFrame:
    """(T x pairs) exposure for the SPOT leg of each pair; the engine's sleeve
    return is computed on the spot panel, so the perp leg is added separately
    (see run_backtest). Pair is on when trailing funding sums positive over
    `lookback` days, else flat. 21 days is a monthly rhythm, not a tuned
    threshold; the sign of the carry, not its magnitude, is the signal."""
    on = (funding.rolling(lookback, min_periods=lookback).sum() > 0).astype(float)
    return (on * max_pair).where(funding.notna(), 0.0)


def carry_sleeve_exposure(close: pd.DataFrame, funding: pd.DataFrame,
                          pairs: list[tuple[str, str]],
                          lookback: int = 21) -> pd.DataFrame:
    """(T x assets) exposure across both legs of every pair. Each on-pair gets
    0.5 spot + 0.5 short-perp so a fully-on sleeve is 1.0 gross per pair."""
    spot_syms = [s for s, _ in pairs]
    perp_syms = [p for _, p in pairs]
    on = carry_pair_exposure(funding[spot_syms], lookback)
    expo = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    for s in spot_syms:
        if s in close.columns:
            expo[s] = on[s] * 0.5
    for p in perp_syms:
        if p in close.columns:
            expo[p] = -on[p.replace("-PERP", "")] * 0.5
    return expo.fillna(0.0)


def route_through_perp(expo: pd.DataFrame, funding: pd.DataFrame,
                       lookback: int = 21) -> pd.DataFrame:
    """Instrument choice for directional crypto exposure: hold a position in
    the perp rather than spot whenever that side collects funding (short perp
    when funding is positive, long perp when negative). Same exposure, same
    gross; the trailing-funding gate is the same causal rule the carry sleeve
    uses. Spot leg keeps the position otherwise."""
    out = expo.copy()
    for c in [c for c in expo.columns if c.endswith("USDT")]:
        perp = f"{c}-PERP"
        if perp not in expo.columns or c not in funding.columns:
            continue
        favours_short = (funding[c].rolling(lookback, min_periods=lookback).sum() > 0)
        favours_long = -favours_short
        short_in_perp = (out[c] < 0) & favours_short.reindex(out.index).ffill().fillna(False)
        long_in_perp = (out[c] > 0) & favours_long.reindex(out.index).ffill().fillna(False)
        move = out[c].where(short_in_perp | long_in_perp, 0.0)
        out[c] = out[c] - move
        out[perp] = out.get(perp, 0.0) + move
    return out.fillna(0.0)
