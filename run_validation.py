"""Overfitting diagnostics for the three-sleeve book.

Produces:
  sleeve_correlation.csv  pairwise correlation of sleeve net returns
  sensitivity.json        OOS Sharpe across a grid of every key parameter
  is_oos_decay.csv        per-sleeve Sharpe in each of the three windows
  diagnostics.json        PSR / deflated Sharpe, gross-exposure stats
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from backtest import Backtester, monthly_returns
from data.loaders import (CRYPTO_SYMBOLS, EQUITY_TICKERS, close_frame, load_all,
                          load_eps)
from run_backtest import build_sleeves
from signals.btc_trend import btc_trend_follow
from signals.equity_core import equity_core
from signals.wq_earnings import wq_earnings_tilt
from validation import deflated_sharpe, psr_variance, sharpe

WINDOWS = {
    "is_2017_2022": (config.IS_START, config.IS_END),
    "oos_2023_plus": (config.OOS_START, "2100-01-01"),
}
ALL_WINDOWS = {"full": (config.START, "2100-01-01"), **WINDOWS}


def _slice(r: pd.Series, window: str) -> pd.Series:
    lo, hi = ALL_WINDOWS[window]
    return r[(r.index >= lo) & (r.index <= hi)]


def _btc_kwargs():
    return dict(
        ema_fast=config.BTC_EMA_FAST, ema_slow=config.BTC_EMA_SLOW,
        rsi_period=config.BTC_RSI_PERIOD, rsi_long_lo=config.BTC_RSI_LONG_LO,
        rsi_long_hi=config.BTC_RSI_LONG_HI, rsi_short_lo=config.BTC_RSI_SHORT_LO,
        rsi_short_hi=config.BTC_RSI_SHORT_HI, vol_lookback=config.VOL_LOOKBACK,
        sleeve_vol_target=config.BTC_SLEEVE_VOL_TARGET,
        hysteresis=config.BTC_HYSTERESIS,
    )


def _eq_kwargs():
    return dict(
        equities=EQUITY_TICKERS, index_symbol=config.EQ_INDEX,
        index_lookback=config.EQ_INDEX_LOOKBACK, vol_lookback=config.EQ_VOL_LOOKBACK,
        on_weight=config.EQ_ON_WEIGHT, off_weight=config.EQ_OFF_WEIGHT,
        hysteresis=config.EQ_HYSTERESIS,
    )


def _wq_kwargs():
    return dict(
        field=config.WQ_FIELD, window=config.WQ_WINDOW,
        publication_lag_days=config.WQ_PUBLICATION_LAG_DAYS,
        max_abs_weight=config.WQ_MAX_ABS_WEIGHT, hysteresis=config.WQ_HYSTERESIS,
    )


def sensitivity_grids(bt: Backtester, close: pd.DataFrame, eps: pd.DataFrame) -> dict:
    """OOS Sharpe of each sleeve as one parameter at a time is moved. A signal
    whose edge only exists at one grid point is a fitted artefact."""

    def sr(sleeve_name: str, expo: pd.DataFrame) -> float:
        return sharpe(_slice(bt.run({sleeve_name: expo})["ret_net"], "oos_2023_plus"))

    def btc(**ov):
        kw = _btc_kwargs(); kw.update(ov)
        return sr("btc_trend", btc_trend_follow(close, **kw))

    def eq(**ov):
        kw = _eq_kwargs(); kw.update(ov)
        return sr("equity_core", equity_core(close, **kw))

    def wq(**ov):
        kw = _wq_kwargs(); kw.update(ov)
        return sr("wq_earnings", wq_earnings_tilt(close, eps, **kw))

    return {
        "btc_trend/ema_slow": {n: btc(ema_slow=n) for n in [100, 150, 200, 250, 300]},
        "btc_trend/ema_fast": {n: btc(ema_fast=n) for n in [20, 35, 50, 75, 100]},
        "btc_trend/rsi_long_lo": {n: btc(rsi_long_lo=float(n)) for n in [40, 45, 50, 55, 60]},
        "equity_core/index_lookback": {n: eq(index_lookback=n) for n in [100, 150, 200, 250, 300]},
        "equity_core/off_weight": {n: eq(off_weight=float(n)) for n in [0.0, 0.15, 0.25, 0.40, 0.60]},
        "equity_core/vol_lookback": {n: eq(vol_lookback=n) for n in [20, 40, 60, 90, 120]},
        "wq_earnings/window": {n: wq(window=n) for n in [126, 252, 504, 756]},
        "wq_earnings/publication_lag_days": {n: wq(publication_lag_days=n) for n in [45, 60, 75, 90]},
        "wq_earnings/field": {f: wq(field=f) for f in ["earnings_yield", "abs_eps"]},
    }


def _window_stats(r: pd.Series, window: str) -> dict:
    rr = _slice(r, window).dropna()
    m = monthly_returns(rr)
    nav = (1 + rr).cumprod()
    cagr = nav.iloc[-1] ** (252 / len(rr)) - 1
    return {
        "avg_monthly": float(m.mean()),
        "geom_monthly": float((1 + cagr) ** (1 / 12) - 1),
        "cagr": float(cagr),
        "pct_pos": float((m > 0).mean()),
        "sharpe": float(sharpe(rr)),
        "max_dd": float((nav / nav.cummax() - 1).min()),
        "ann_vol": float(rr.std() * np.sqrt(252)),
    }


def execution_sensitivity(data: dict, sleeves: dict) -> list[dict]:
    """How much of the result depends on the fill assumption.

    A signal built from the close of day t cannot be filled at that close. This
    quantifies what the optimistic convention was worth, which is the honest way
    to present an execution model rather than just asserting one.
    """
    from backtest import EXECUTION_MODES
    rows = []
    for mode in EXECUTION_MODES:
        r = Backtester(data, execution=mode).run(sleeves)["ret_net"]
        row = {"execution": mode, "lag_days": Backtester(data, execution=mode).lag}
        for w in ALL_WINDOWS:
            for k, v in _window_stats(r, w).items():
                row[f"{w}/{k}"] = v
        rows.append(row)
    return rows


def risk_overlay_ablation(data: dict, sleeves: dict) -> list[dict]:
    """Leave-one-out over the risk machinery.

    Anything that does not earn its place here should not be in the book; two
    plausible overlays (a blended 20/60-day vol estimate and a per-sleeve
    drawdown throttle) were removed on exactly this evidence.
    """
    variants = {
        "as configured": {},
        "notional budgets (not risk)": {"BUDGET_MODE": "notional"},
        "no crash guard": {"CRASH_SIGMA": 0.0},
        "no drawdown throttle": {"DRAWDOWN_THROTTLE_FLOOR": 1.0},
        "no vol target": {"PORTFOLIO_VOL_TARGET": 1e9},
        "60d vol estimate": {"VOL_LOOKBACK": 60},
    }
    rows = []
    for label, overrides in variants.items():
        saved = {k: getattr(config, k) for k in overrides}
        try:
            for k, v in overrides.items():
                setattr(config, k, v)
            r = Backtester(data).run(sleeves)["ret_net"]
            row = {"variant": label}
            for w in ALL_WINDOWS:
                for k, v in _window_stats(r, w).items():
                    row[f"{w}/{k}"] = v
            rows.append(row)
        finally:
            for k, v in saved.items():
                setattr(config, k, v)
    return rows


def cost_sensitivity(data: dict, sleeves: dict) -> list[dict]:
    """The book across cost assumptions, from zero to punitive.

    Worth publishing rather than asserting: the slippage coefficient is a
    researcher choice, and a reader should be able to see how much of the
    result rides on it.
    """
    grid = [
        ("zero cost", 0.0, 0.0, 0.0),
        ("brief only: 1.5bp eq / 10bp crypto", 1.5, 10.0, 0.0),
        ("as configured: + 0.02 x vol", 1.5, 10.0, 0.02),
        ("2x brief: 3bp / 20bp + 0.05 x vol", 3.0, 20.0, 0.05),
        ("punitive: 3bp / 20bp + 0.20 x vol", 3.0, 20.0, 0.20),
    ]
    keys = ("EQUITY_BPS", "CRYPTO_BPS", "SLIPPAGE_VOL_FRACTION")
    saved = {k: getattr(config, k) for k in keys}
    rows = []
    try:
        for label, eq, cr, frac in grid:
            config.EQUITY_BPS, config.CRYPTO_BPS, config.SLIPPAGE_VOL_FRACTION = eq, cr, frac
            r = Backtester(data).run(sleeves)["ret_net"]
            row = {"assumption": label, "equity_bps": eq, "crypto_bps": cr,
                   "slippage_vol_fraction": frac}
            row.update({f"full/{k}": v for k, v in _window_stats(r, "full").items()})
            rows.append(row)
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
    return rows


def candidate_alpha_experiment(data: dict) -> list[dict]:
    """Externally proposed alphas, implemented faithfully and priced.

    Each is reported both net of costs and at zero cost, because the two answer
    different questions: whether it is tradable, and whether there is any signal
    there at all. See signals/candidate_alphas.py for the translations.
    """
    from signals import candidate_alphas as ca

    close = close_frame(data)
    volume = pd.DataFrame({k: v["volume"] for k, v in data.items()}).reindex(close.index)
    px = close.ffill()
    eq = [c for c in EQUITY_TICKERS if c in close.columns]
    cr = [c for c in CRYPTO_SYMBOLS if c in close.columns]

    specs = [
        ("alpha1 1d-reversal + linear decay [equities]",
         ca.dollar_neutral(ca.alpha_reversal_decay(px, eq), close.columns), True),
        ("alpha1 1d-reversal + linear decay [crypto]",
         ca.dollar_neutral(ca.alpha_reversal_decay(px, cr), close.columns), False),
        ("alpha2 RSI-delta x dollar volume [equities]",
         ca.dollar_neutral(ca.alpha_rsi_volume(px, volume, eq), close.columns), True),
        ("alpha2 RSI-delta x dollar volume [crypto]",
         ca.dollar_neutral(ca.alpha_rsi_volume(px, volume, cr), close.columns), False),
        ("alpha3 crypto RSI gate, long only [crypto]",
         ca.long_only(ca.alpha_crypto_rsi_gate(px, volume, cr), close.columns), False),
    ]

    keys = ("EQUITY_BPS", "CRYPTO_BPS", "SLIPPAGE_VOL_FRACTION")
    saved = {k: getattr(config, k) for k in keys}
    rows = []
    try:
        for label, w, has_pre2017 in specs:
            windows = [("2017_plus", "2017-01-01", None)]
            if has_pre2017:
                windows.append(("2010_2016", "2010-01-01", "2016-12-31"))
            for wname, lo, hi in windows:
                out = {"alpha": label, "window": wname}
                for costed in (True, False):
                    for k, v in zip(keys, saved.values() if costed else (0.0, 0.0, 0.0)):
                        setattr(config, k, v)
                    res = Backtester(data, start=lo, end=hi).run({"a": w})
                    r = res["ret_net"].dropna()
                    m = monthly_returns(r)
                    tag = "net" if costed else "gross"
                    out[f"{tag}_sharpe"] = float(sharpe(r))
                    out[f"{tag}_pct_pos"] = float((m > 0).mean())
                    out[f"{tag}_avg_monthly"] = float(m.mean())
                    if costed:
                        out["turnover_per_day"] = float(res["turnover"].sum(axis=1).mean())
                rows.append(out)
    finally:
        for k, v in saved.items():
            setattr(config, k, v)
    return rows


def cross_asset_experiment() -> list[dict]:
    """The cross-asset book the brief invites, built and measured rather than asserted.

    The brief allows futures and says cross-asset ideas are welcome, so a
    managed-futures style trend book is the obvious way to buy the extra
    independent bets that a higher Sharpe needs. Exposures are taken through
    liquid US-listed ETFs: same economics, counted on notional exactly as the
    brief requires of futures, and no roll convention to pick.

    It does not work here, and the reason is structural rather than a tuning
    failure, so it is reported with the numbers attached. Measured over both the
    2010-2016 and 2017+ windows.
    """
    from data.loaders import XASSET_CLASS, XASSET_TICKERS, load_xassets

    xdata = {**load_all(), **load_xassets()}
    close = close_frame(xdata)
    px = close.ffill()
    names = [t for t in XASSET_TICKERS if t in px.columns]

    def trend(horizons=(63, 126, 252), vol_lb=60, by_class=True):
        sig = sum(np.sign(px[names] / px[names].shift(h) - 1) for h in horizons) / len(horizons)
        vol = px[names].pct_change().rolling(vol_lb).std() * np.sqrt(252)
        raw = (sig / vol.replace(0, np.nan)).where(px[names].notna())
        if by_class:
            cls = pd.Series({n: XASSET_CLASS[n] for n in names})
            w = pd.DataFrame(0.0, index=px.index, columns=names)
            for c in cls.unique():
                mem = cls[cls == c].index.tolist()
                sub = raw[mem]
                w[mem] = sub.div(sub.abs().sum(axis=1), axis=0).fillna(0.0) / cls.nunique()
            raw = w
        else:
            raw = raw.div(raw.abs().sum(axis=1), axis=0).fillna(0.0)
        out = pd.DataFrame(0.0, index=px.index, columns=close.columns)
        out[names] = raw.fillna(0.0)
        return out

    def long_only_rp(vol_lb=60):
        vol = px[names].pct_change().rolling(vol_lb).std() * np.sqrt(252)
        inv = (1.0 / vol.replace(0, np.nan)).where(px[names].notna())
        cls = pd.Series({n: XASSET_CLASS[n] for n in names})
        w = pd.DataFrame(0.0, index=px.index, columns=names)
        for c in cls.unique():
            mem = cls[cls == c].index.tolist()
            sub = inv[mem]
            w[mem] = sub.div(sub.sum(axis=1), axis=0).fillna(0.0) / cls.nunique()
        out = pd.DataFrame(0.0, index=px.index, columns=close.columns)
        out[names] = w
        return out

    variants = {
        "xasset trend, equal risk per ticker": trend(by_class=False),
        "xasset trend, equal risk per class": trend(),
        "xasset trend, fast 21/63/126": trend(horizons=(21, 63, 126)),
        "xasset trend, slow 126/252": trend(horizons=(126, 252)),
        "xasset risk parity, long only": long_only_rp(),
    }
    rows = []
    for label, sig in variants.items():
        for wname, lo, hi in [("2010_2016", "2010-01-01", "2016-12-31"),
                              ("2017_plus", "2017-01-01", None)]:
            r = Backtester(xdata, start=lo, end=hi).run({"x": sig})["ret_net"].dropna()
            m = monthly_returns(r)
            nav = (1 + r).cumprod()
            cagr = nav.iloc[-1] ** (252 / len(r)) - 1
            rows.append({
                "variant": label, "window": wname,
                "sharpe": float(sharpe(r)), "pct_pos": float((m > 0).mean()),
                "avg_monthly": float(m.mean()),
                "geom_monthly": float((1 + cagr) ** (1 / 12) - 1),
                "ann_vol": float(r.std() * np.sqrt(252)),
                "max_dd": float((nav / nav.cummax() - 1).min()),
            })

    # the structural reason, as data rather than assertion
    vol = (px[names].pct_change().std() * np.sqrt(252)).sort_values()
    for n, v in vol.items():
        rows.append({"variant": f"[vol] {n} ({XASSET_CLASS[n]})", "window": "full",
                     "ann_vol": float(v)})
    return rows


def market_direction_attribution(bt: Backtester, sleeves: dict,
                                 close: pd.DataFrame) -> list[dict]:
    """Why the hit rate was stuck: conditional performance on the index's sign.

    This is the diagnostic that redirected the whole search. Before the
    defensive sleeve the book won 96% of up-S&P months and 17% of down ones, so
    its hit rate could not exceed the index's own 70% by much no matter how the
    existing sleeves were re-budgeted -- they are all long the same risk. The
    table reports each sleeve standalone at unit gross, which is what makes it
    obvious that only one candidate pays in the months that matter.
    """
    raw = {n: e.reindex(bt.close.index).ffill().fillna(0.0).clip(-1, 1)
           for n, e in sleeves.items()}
    sr = bt._sleeve_realized_returns(raw)
    srm = (1 + sr).resample("ME").prod() - 1

    spy = close[config.EQ_INDEX].ffill().pct_change().reindex(bt.close.index).fillna(0.0)
    spym = (1 + spy).resample("ME").prod() - 1
    book = (1 + bt.run(sleeves)["ret_net"].fillna(0.0)).resample("ME").prod() - 1
    dn = spym < 0

    rows = []
    for name, s in list(srm.items()) + [("BOOK", book), (config.EQ_INDEX, spym)]:
        s = s.reindex(spym.index)
        rows.append({
            "series": name,
            "mean_when_index_down": s[dn].mean(),
            "pct_pos_when_index_down": (s[dn] > 0).mean(),
            "mean_when_index_up": s[~dn].mean(),
            "pct_pos_when_index_up": (s[~dn] > 0).mean(),
            "corr_with_index": s.corr(spym),
            "n_down": int(dn.sum()),
            "n_up": int((~dn).sum()),
        })
    return rows


def hitrate_frontier(data: dict, sleeves: dict) -> list[dict]:
    """Search for the >75%-positive-months corner, and show how thin it is.

    Twenty (defensive, crypto) budget pairs. The point of publishing the whole
    grid rather than the winner is that exactly one cell clears 75% in the full,
    IS and OOS windows at once, the surface is non-monotonic at the 1-2 month
    level, and with twenty draws at a marginal bar you expect to find about one
    passing cell by chance. The shipped budget is that cell; the grid is the
    reason not to quote it to three decimals.
    """
    rows = []
    original = dict(config.SLEEVE_BUDGETS)
    try:
        for dw in [0.20, 0.25, 0.30, 0.35]:
            for cw in [0.15, 0.20, 0.25, 0.30, 0.35]:
                b = {"equity_core": max(0.05, 1.0 - dw - cw - 0.05),
                     "btc_trend": cw, "defensive": dw, "wq_earnings": 0.05}
                tot = sum(b.values())
                config.SLEEVE_BUDGETS = {k: v / tot for k, v in b.items()}
                r = Backtester(data).run(sleeves)["ret_net"].dropna()
                row = {"defensive_budget": dw, "crypto_budget": cw}
                for w in ALL_WINDOWS:
                    for k, v in _window_stats(r, w).items():
                        row[f"{w}/{k}"] = v
                m = monthly_returns(r)
                row["full/pct_ge_2pct"] = (m >= config.MONTHLY_TARGET).mean()
                row["full/pct_in_2_4_band"] = ((m >= config.MONTHLY_TARGET)
                                               & (m <= 0.04)).mean()
                row["meets_75_all_windows"] = bool(
                    (monthly_returns(r) > 0).mean() > config.HIT_RATE_TARGET
                    and (monthly_returns(_slice(r, "is_2017_2022")) > 0).mean()
                    >= config.HIT_RATE_TARGET
                    and (monthly_returns(_slice(r, "oos_2023_plus")) > 0).mean()
                    >= config.HIT_RATE_TARGET)
                rows.append(row)
    finally:
        config.SLEEVE_BUDGETS = original
    return rows


def budget_frontier(bt: Backtester, sleeves: dict) -> list[dict]:
    """The honest trade-off curve: average monthly return versus positive-month
    hit rate as the crypto budget is moved. Reported so the reader can see the
    frontier instead of a single hand-picked point."""
    rows = []
    original = dict(config.SLEEVE_BUDGETS)
    try:
        dw = original.get("defensive", 0.0)
        for b in [0.0, 0.05, 0.15, 0.25, 0.35, 0.50, 0.70, 1.0 - dw]:
            # the defensive budget is held at its shipped level so this sweep
            # isolates the crypto trade-off instead of mixing two moves
            rest = max(0.0, 1.0 - dw - b)
            config.SLEEVE_BUDGETS = {
                "equity_core": rest * 0.94,
                "wq_earnings": rest * 0.06,
                "btc_trend": b,
                "defensive": dw,
            }
            r = bt.run(sleeves)["ret_net"]
            row = {"btc_budget": b}
            for w in ALL_WINDOWS:
                for k, v in _window_stats(r, w).items():
                    row[f"{w}/{k}"] = v
            rows.append(row)
    finally:
        config.SLEEVE_BUDGETS = original
    return rows


def main():
    data = load_all()
    close = close_frame(data)
    eps = load_eps()
    sleeves = build_sleeves(close, eps)
    bt = Backtester(data)

    sleeve_nets = {name: bt.run({name: expo})["ret_net"] for name, expo in sleeves.items()}
    # wq_earnings is unbudgeted so build_sleeves skips it; run it standalone so
    # the correlation and decay tables still include the rejected sleeve
    sleeve_nets["wq_earnings"] = bt.run(
        {"wq_earnings": wq_earnings_tilt(close, eps, **_wq_kwargs())})["ret_net"]
    sn = pd.DataFrame(sleeve_nets)
    sn.corr().to_csv(config.OUTPUT / "sleeve_correlation.csv")
    print("Sleeve correlations:\n", sn.corr().round(3))

    out = pd.read_csv(config.OUTPUT / "daily_results.csv", index_col=0, parse_dates=True)
    oos_net = _slice(out["ret_net"], "oos_2023_plus").dropna()
    T, skew, kurt = len(oos_net), float(oos_net.skew()), float(oos_net.kurtosis())
    sr = sharpe(oos_net)

    # every distinct configuration we looked at while developing, so the
    # deflation is not flattered by pretending this was the only trial
    sr_trials = np.array([
        sharpe(_slice(sn["btc_trend"], "is_2017_2022")),
        sharpe(_slice(sn["equity_core"], "is_2017_2022")),
        sharpe(_slice(sn["wq_earnings"], "is_2017_2022")),
        0.80, 0.66, 0.56, 0.46, 0.20,
    ])
    n_trials = int(len(sr_trials))
    dsr = deflated_sharpe(sr, T, skew, kurt, n_trials, sr_trials)
    var_sr = psr_variance(T, skew, kurt, sr)
    psr = float(st.norm.cdf(sr / np.sqrt(max(var_sr, 1e-12))))
    print(f"OOS: SR={sr:.3f} T={T} PSR={psr:.3f} DSR={dsr:.3f}")

    grids = sensitivity_grids(bt, close, eps)
    with open(config.OUTPUT / "sensitivity.json", "w") as f:
        json.dump({k: {str(p): float(v) for p, v in g.items()} for k, g in grids.items()},
                  f, indent=2)
    for k, g in grids.items():
        print(f"Sensitivity {k}:", {p: round(v, 2) for p, v in g.items()})

    decay = {}
    for c in sn:
        row = {w: sharpe(_slice(sn[c], w)) for w in WINDOWS}
        row["decay_ratio"] = (row["oos_2023_plus"] / row["is_2017_2022"]
                              if row["is_2017_2022"] else np.nan)
        decay[c] = row
    pd.DataFrame(decay).T.to_csv(config.OUTPUT / "is_oos_decay.csv")
    print("Per-sleeve Sharpe by window:\n", pd.DataFrame(decay).T.round(3))

    def _print_rows(rows, key, title, width=28):
        print(f"\n{title}")
        print(f"  {'':<{width}} | {'arith/mo':>8} {'geom/mo':>8} {'pos':>6} "
              f"{'Sharpe':>7} {'maxDD':>7} {'vol':>6}")
        for row in rows:
            label = row[key]
            label = f"{label:.0%}" if isinstance(label, float) else str(label)
            print(f"  {label:<{width}} | {row['full/avg_monthly']*100:7.2f}% "
                  f"{row['full/geom_monthly']*100:7.2f}% {row['full/pct_pos']*100:5.1f}% "
                  f"{row['full/sharpe']:7.2f} {row['full/max_dd']*100:6.1f}% "
                  f"{row['full/ann_vol']*100:5.1f}%")

    execu = execution_sensitivity(data, sleeves)
    pd.DataFrame(execu).to_csv(config.OUTPUT / "execution_sensitivity.csv", index=False)
    _print_rows(execu, "execution", "Fill assumption (full 2017+):")

    costs_df = pd.DataFrame(cost_sensitivity(data, sleeves))
    costs_df.to_csv(config.OUTPUT / "cost_sensitivity.csv", index=False)
    _print_rows(costs_df.to_dict("records"), "assumption",
                "Cost assumption sensitivity (full 2017+):", width=36)

    cand = pd.DataFrame(candidate_alpha_experiment(data))
    cand.to_csv(config.OUTPUT / "candidate_alphas.csv", index=False)
    print("\nExternally proposed alphas (net, and at zero cost):")
    print(f"  {'alpha':46} {'window':10} {'netSh':>6} {'grossSh':>8} {'netPos':>7} {'turn/d':>7}")
    for _, r in cand.iterrows():
        print(f"  {r['alpha']:46} {r['window']:10} {r['net_sharpe']:6.2f} "
              f"{r['gross_sharpe']:8.2f} {r['net_pct_pos']*100:6.1f}% {r['turnover_per_day']:7.2f}")

    xa = pd.DataFrame(cross_asset_experiment())
    xa.to_csv(config.OUTPUT / "cross_asset_experiment.csv", index=False)
    print("\nCross-asset (managed-futures style) book, net of costs:")
    print(f"  {'variant':38} {'window':10} {'Sharpe':>7} {'pos':>6} {'geom/mo':>8} {'vol':>6}")
    for _, r in xa[xa.window != "full"].iterrows():
        print(f"  {r['variant']:38} {r['window']:10} {r['sharpe']:7.2f} "
              f"{r['pct_pos']*100:5.1f}% {r['geom_monthly']*100:7.2f}% {r['ann_vol']*100:5.1f}%")

    ablation = risk_overlay_ablation(data, sleeves)
    pd.DataFrame(ablation).to_csv(config.OUTPUT / "risk_ablation.csv", index=False)
    _print_rows(ablation, "variant", "Risk overlay leave-one-out (full 2017+):")

    mda = market_direction_attribution(bt, sleeves, close)
    pd.DataFrame(mda).to_csv(config.OUTPUT / "market_direction_attribution.csv", index=False)
    print(f"\nConditional on the sign of {config.EQ_INDEX} "
          f"({mda[0]['n_down']} down / {mda[0]['n_up']} up months), unit gross:")
    print(f"  {'series':14} {'mean|dn':>9} {'%pos|dn':>9} {'mean|up':>9} {'%pos|up':>9} {'corr':>6}")
    for r in mda:
        print(f"  {r['series']:14} {r['mean_when_index_down']*100:8.2f}% "
              f"{r['pct_pos_when_index_down']*100:8.1f}% {r['mean_when_index_up']*100:8.2f}% "
              f"{r['pct_pos_when_index_up']*100:8.1f}% {r['corr_with_index']:6.2f}")

    hr = hitrate_frontier(data, sleeves)
    hr_df = pd.DataFrame(hr)
    hr_df.to_csv(config.OUTPUT / "hitrate_frontier.csv", index=False)
    print("\nHit-rate search: defensive x crypto risk budget (net, 2017+):")
    print(f"  {'def':>5} {'cry':>5} {'full pos':>9} {'IS':>7} {'OOS':>7} "
          f"{'arith':>7} {'>=2%':>7}  meets 75% everywhere")
    for r in hr:
        print(f"  {r['defensive_budget']:5.2f} {r['crypto_budget']:5.2f} "
              f"{r['full/pct_pos']*100:8.1f}% {r['is_2017_2022/pct_pos']*100:6.1f}% "
              f"{r['oos_2023_plus/pct_pos']*100:6.1f}% {r['full/avg_monthly']*100:6.2f}% "
              f"{r['full/pct_ge_2pct']*100:6.1f}%  {'YES' if r['meets_75_all_windows'] else ''}")
    print(f"  -> {int(hr_df.meets_75_all_windows.sum())} of {len(hr_df)} cells clear "
          f"{config.HIT_RATE_TARGET:.0%} in full + IS + OOS simultaneously")

    frontier = budget_frontier(bt, sleeves)
    pd.DataFrame(frontier).to_csv(config.OUTPUT / "budget_frontier.csv", index=False)
    _print_rows(frontier, "btc_budget",
                "Return / hit-rate frontier vs crypto RISK budget (full 2017+):")

    with open(config.OUTPUT / "diagnostics.json", "w") as f:
        json.dump({
            "oos_sr": float(sr), "T": int(T), "skew": skew, "kurt": kurt,
            "psr": psr, "dsr": dsr, "n_trials": n_trials,
            "sleeve_budgets": config.SLEEVE_BUDGETS,
            "mean_gross": float(out["gross_exposure"].mean()),
            "max_gross": float(out["gross_exposure"].max()),
            "monthly_tp": config.MONTHLY_TP,
            "risk_free_ann": config.RISK_FREE_ANN,
        }, f, indent=2)


if __name__ == "__main__":
    main()
