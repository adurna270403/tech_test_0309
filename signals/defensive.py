"""Defensive sleeve: trend-gated gold / long duration / dollar. Its job is to
be positive in the months equities fall, which is what lifts the book's monthly
hit rate; see the market-direction attribution in the report."""
from __future__ import annotations

import numpy as np
import pandas as pd


def trend_gate(close: pd.DataFrame, names: list[str],
               horizons: tuple[int, ...] = (63, 126, 252)) -> pd.DataFrame:
    """1.0 where an asset is above its price at EVERY horizon, else 0.0.
    Unanimity avoids turning the sleeve on during early reversals, when a
    defensive asset is least useful."""
    px = close[names]
    votes = sum((px / px.shift(h) - 1.0 > 0).astype(float) for h in horizons)
    return (votes >= len(horizons)).astype(float).where(px.notna(), 0.0)


def defensive_basket(
    close: pd.DataFrame,
    names: list[str],
    horizons: tuple[int, ...] = (63, 126, 252),
    vol_lookback: int = 60,
) -> pd.DataFrame:
    """(T x assets) long-only exposure; sleeve gross = fraction of legs passing
    the gate, so nothing trending means genuinely flat."""
    px = close.ffill()
    names = [c for c in names if c in px.columns]

    on = trend_gate(px, names, horizons)
    vol = px[names].pct_change().rolling(vol_lookback).std() * np.sqrt(252)
    inv = (on / vol.replace(0, np.nan)).where(px[names].notna())
    w = inv.div(inv.sum(axis=1).replace(0, np.nan), axis=0).fillna(0.0)
    # scale to the fraction of legs actually trending
    w = w.mul(on.sum(axis=1) / max(len(names), 1), axis=0)

    expo = pd.DataFrame(0.0, index=px.index, columns=px.columns)
    expo[names] = w.fillna(0.0)
    return expo.clip(0.0, 1.0)
