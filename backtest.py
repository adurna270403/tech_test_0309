"""Daily backtest engine. Signal from close of day t -> fill at next open,
P&L measured open-to-open. Gross cap is applied to the *held* panel, since on a
mixed calendar the book on date t is assembled from decisions of different dates.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import config
import costs
from portfolio.allocator import (apply_gross_cap, budget_scale, crash_guard,
                                 drawdown_throttle, vol_target_scaler)


def apply_monthly_take_profit(expo: pd.DataFrame, ret: pd.DataFrame,
                              tp: float | None) -> pd.DataFrame:
    """After month-to-date P&L (through yesterday) reaches `tp`, go flat for
    the rest of the month."""
    if tp is None or tp <= 0:
        return expo
    E = expo.to_numpy(dtype=float)
    R = ret.reindex(columns=expo.columns).fillna(0.0).to_numpy(dtype=float)
    months = expo.index.month.to_numpy() + expo.index.year.to_numpy() * 12
    n, m = E.shape
    scale = np.ones(n)
    active = 1.0
    mtd = 0.0
    prev = np.zeros(m)
    for i in range(n):
        if i == 0 or months[i] != months[i - 1]:
            active = 1.0
            mtd = 0.0
        scale[i] = active
        day = float(prev @ R[i])
        mtd = (1.0 + mtd) * (1.0 + day) - 1.0
        if active > 0.0 and mtd >= tp:
            active = 0.0
        prev = E[i] * scale[i]
    return expo.mul(pd.Series(scale, index=expo.index), axis=0)


EXECUTION_MODES = {
    "same_close": ("close", 0),   # optimistic convention, sensitivity only
    "next_open": ("open", 1),     # decide at close t, fill at open t+1 (default)
    "next_close": ("close", 1),   # slower MOC-style fill
}


class Backtester:
    def __init__(self, data: dict[str, pd.DataFrame], start: str | None = None,
                 end: str | None = None, execution: str | None = None):
        self.execution = execution or config.EXECUTION_MODE
        if self.execution not in EXECUTION_MODES:
            raise ValueError(f"unknown execution mode {self.execution!r}")
        price_field, extra_lag = EXECUTION_MODES[self.execution]
        # one day because the signal only knows the previous close, plus fill delay
        self.lag = 1 + extra_lag

        close = pd.DataFrame({k: v["close"] for k, v in data.items()}).sort_index()
        fill = pd.DataFrame({k: v[price_field] for k, v in data.items()}).sort_index()
        valid = close.notna()
        ret = fill.reindex(close.index).ffill().pct_change()
        lo = pd.Timestamp(start or config.START)
        hi = pd.Timestamp(end) if end else close.index[-1]
        mask = (close.index >= lo) & (close.index <= hi)
        # signals are always built on closes, whatever the fill price
        self.close, self.ret, self.valid = close[mask], ret[mask], valid[mask]

    def _held(self, expo: pd.DataFrame) -> pd.DataFrame:
        """Exposure held over each return period. The lag is counted in each
        asset's OWN trading days, not panel rows: a flat shift() over a mixed
        crypto/equity calendar lets a signal be paid for a move that happened
        before the signal existed. The panel is re-capped here because per-
        calendar lagging means the held book mixes decisions of different dates.
        """
        e = expo.reindex(columns=self.valid.columns).fillna(0.0)
        out = {}
        for c in e.columns:
            own = e[c][self.valid[c]]
            out[c] = own.shift(self.lag).reindex(e.index).ffill()
        return apply_gross_cap(pd.DataFrame(out, index=e.index).fillna(0.0))

    def build_final_exposure(self, sleeves: dict[str, pd.DataFrame]) -> pd.DataFrame:
        idx = self.close.index
        raw = {name: e.reindex(idx).ffill().fillna(0.0).clip(-1, 1)
               for name, e in sleeves.items()}
        assets = sorted({a for e in raw.values() for a in e.columns})

        sleeve_rets = self._sleeve_realized_returns(raw)
        w = budget_scale(list(raw), idx, sleeve_rets)
        combined = sum(
            raw[name].mul(w[name], axis=0).reindex(columns=assets).fillna(0.0)
            for name in raw
        )

        # risk overlays may use information through day t: proxy_ret[t] is
        # already-held exposure times a return completed by t
        proxy_ret = (self._held(combined) * self.ret.fillna(0.0)).sum(axis=1)
        s_v = vol_target_scaler(proxy_ret, config.PORTFOLIO_VOL_TARGET)
        nav = (1 + proxy_ret.fillna(0)).cumprod()

        # cap first, then de-risk: the vol target usually wants to scale UP, so
        # any multiplier applied before the cap is absorbed by it and never bites
        sized = apply_gross_cap(combined.mul(s_v.fillna(1.0), axis=0))

        derisk = (drawdown_throttle(nav) * crash_guard(proxy_ret)).clip(0.0, 1.0)
        final = sized.mul(derisk.fillna(1.0), axis=0)
        final = apply_monthly_take_profit(final, self.ret, config.MONTHLY_TP)
        return apply_gross_cap(final)

    def _sleeve_realized_returns(self, raw: dict[str, pd.DataFrame]) -> pd.DataFrame:
        out = {}
        for name, expo in raw.items():
            aligned = expo.reindex(columns=self.ret.columns).fillna(0.0)
            out[name] = (self._held(aligned) * self.ret.fillna(0.0)).sum(axis=1)
        return pd.DataFrame(out)

    def run(self, sleeves: dict[str, pd.DataFrame]) -> dict:
        final_expo = self.build_final_exposure(sleeves)
        held = self._held(final_expo)
        pnl = (held * self.ret.fillna(0.0)).sum(axis=1)
        # cost charged on the bar the new position starts earning
        turnover = held.diff().abs().fillna(0.0)
        cost = costs.turnover_cost(turnover, self.ret)
        gross = held.abs().sum(axis=1)
        cash = (1.0 - gross).clip(lower=0.0)
        rf = cash * (config.RISK_FREE_ANN / 252.0)

        ret_gross = pnl + rf
        ret_net = pnl - cost + rf
        return {
            "ret_gross": ret_gross,
            "ret_net": ret_net,
            "cost": cost,
            "rf": rf,
            "gross_exposure": gross,
            "nav_gross": (1 + ret_gross.fillna(0)).cumprod(),
            "nav_net": (1 + ret_net.fillna(0)).cumprod(),
            "expo": final_expo,
            "held": held,
            "turnover": turnover,
            "execution": self.execution,
        }


def monthly_returns(ret: pd.Series) -> pd.Series:
    return (1 + ret.fillna(0)).resample("ME").prod() - 1
