"""Allocation layer: sleeve risk budgets -> portfolio vol target -> drawdown
throttle + crash guard -> hard gross cap. All scalers are causal (trailing
windows only)."""
import numpy as np
import pandas as pd

import config


def _raw_budgets(sleeve_names: list[str]) -> dict[str, float]:
    raw = {n: config.SLEEVE_BUDGETS.get(n, 0.0) for n in sleeve_names}
    if sum(raw.values()) <= 0:
        raw = {n: 1.0 for n in sleeve_names}
    return raw


def budget_scale(sleeve_names: list[str], index: pd.Index,
                 sleeve_returns: pd.DataFrame | None = None) -> pd.DataFrame:
    """Sleeve weights from config.SLEEVE_BUDGETS (constant, hand-set).

    BUDGET_MODE "notional": budgets are shares of gross exposure.
    BUDGET_MODE "risk" (default): weights are scaled by inverse trailing sleeve
    vol so a budget really buys that share of risk -- the crypto sleeve runs at
    3-4x the equity basket's vol, so the two readings describe different books.
    """
    raw = _raw_budgets(sleeve_names)
    if config.BUDGET_MODE == "notional" or sleeve_returns is None:
        total = sum(raw.values())
        return pd.DataFrame({n: v / total for n, v in raw.items()}, index=index)

    vol = (sleeve_returns.rolling(config.BUDGET_VOL_LOOKBACK,
                                  min_periods=config.BUDGET_VOL_MIN_PERIODS).std()
           * np.sqrt(252))
    # a sleeve with no history yet falls back to the cross-sleeve median vol
    vol = vol.where(vol > 0).apply(lambda c: c.fillna(vol.median(axis=1)))
    inv = pd.DataFrame({n: raw[n] / vol[n] for n in sleeve_names}, index=vol.index)
    w = inv.div(inv.sum(axis=1), axis=0)
    return w.reindex(index).ffill().fillna(
        pd.Series({n: v / sum(raw.values()) for n, v in raw.items()}))


def realized_vol(ret: pd.Series, lookback: int | None = None) -> pd.Series:
    lb = lookback or config.VOL_LOOKBACK
    return ret.rolling(lb, min_periods=min(lb, 20)).std() * np.sqrt(252)


def vol_target_scaler(sleeve_ret: pd.Series, target: float,
                      floor: float | None = None,
                      cap: float | None = None) -> pd.Series:
    floor = config.VOL_TARGET_MIN_WEIGHT if floor is None else floor
    cap = config.VOL_TARGET_MAX_WEIGHT if cap is None else cap
    scaler = target / realized_vol(sleeve_ret)
    return scaler.clip(lower=floor, upper=cap).fillna(1.0)


def crash_guard(ret: pd.Series) -> pd.Series:
    """Cut exposure after a single outsized adverse day (in daily sds), then
    ramp back. A rolling vol window cannot react to a gap this fast."""
    sigma = config.CRASH_SIGMA
    if sigma <= 0:
        return pd.Series(1.0, index=ret.index)
    floor, rec = config.CRASH_FLOOR, config.CRASH_RECOVERY_DAYS

    daily_sd = realized_vol(ret) / np.sqrt(252)
    shocked = (ret <= -sigma * daily_sd).astype(float)
    idx = pd.Series(np.arange(len(ret)), index=ret.index)
    last = idx.where(shocked > 0).ffill()
    age = (idx - last).fillna(rec)
    ramp = (age / rec).clip(0.0, 1.0)
    return (floor + (1.0 - floor) * ramp).clip(floor, 1.0)


def drawdown_throttle(nav: pd.Series, scale: float | None = None,
                      floor: float | None = None) -> pd.Series:
    scale = config.DRAWDOWN_THROTTLE_SCALE if scale is None else scale
    floor = config.DRAWDOWN_THROTTLE_FLOOR if floor is None else floor
    dd = nav / nav.cummax() - 1.0
    return (1.0 - dd.abs() / scale).clip(lower=floor, upper=1.0)


def delta_neutral_net_gross(exposure: pd.DataFrame) -> pd.Series:
    """Gross exposure counting each matched spot/short-perp pair once.

    A cash-and-carry pair (long spot X, short X-PERP of equal size) has zero
    net delta and is margined on one leg by prime brokers/exchanges. The
    matched notional is counted once; any UNMATCHED exposure (a perp leg larger
    than its spot leg, or a naked directional position) counts leg by leg. This
    is a disclosed accounting convention for hedged books, applied uniformly
    and before the cap -- it is not extra capacity for directional risk.
    """
    gross = exposure.abs().sum(axis=1)
    for c in [c for c in exposure.columns if c.endswith("USDT-PERP")]:
        spot = c.replace("-PERP", "")
        if spot not in exposure.columns:
            continue
        matched = np.minimum(exposure[spot], -exposure[c]).clip(lower=0.0)
        gross = gross - matched  # one matched leg counted once instead of twice
    return gross


def apply_gross_cap(exposure: pd.DataFrame, cap: float | None = None,
                    net_delta_neutral: bool | None = None) -> pd.DataFrame:
    cap = config.GROSS_CAP if cap is None else cap
    if net_delta_neutral is None:
        net_delta_neutral = config.NET_DELTA_NEUTRAL_GROSS
    if net_delta_neutral:
        gross = delta_neutral_net_gross(exposure)
    else:
        gross = exposure.abs().sum(axis=1)
    factor = np.minimum(1.0, cap / gross.replace(0, np.nan))
    return exposure.mul(factor, axis=0)
