"""Main run: load data, build sleeves, run backtest, save outputs.

Parameters are fixed a priori in config.py and never re-estimated, so there is
no fitting that could leak between IS and OOS; IS/OOS measures development
honesty (2023+ is declared out-of-sample).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from backtest import Backtester, monthly_returns
from data.loaders import (DEFENSIVE_TICKERS, EQUITY_TICKERS, close_frame,
                          data_fingerprint, load_all, load_eps)
from signals.btc_trend import btc_trend_follow
from signals.defensive import defensive_basket
from signals.equity_core import equity_core
from signals.wq_earnings import wq_earnings_tilt


def build_sleeves(close: pd.DataFrame, eps: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Sleeves, each on a different driver:
      equity_core  long US equity basket gated by the S&P 500 trend
      btc_trend    crypto long/short on the BTC EMA+RSI regime
      defensive    trend-gated gold / duration / dollar
      wq_earnings  cross-sectional earnings tilt (rejected; off by default)

    Only sleeves in config.SLEEVE_BUDGETS are built: an unbudgeted sleeve would
    otherwise still enter the risk-budget normalisation.
    """
    all_sleeves = {
        "equity_core": equity_core(
            close,
            equities=EQUITY_TICKERS,
            index_symbol=config.EQ_INDEX,
            index_lookback=config.EQ_INDEX_LOOKBACK,
            vol_lookback=config.EQ_VOL_LOOKBACK,
            on_weight=config.EQ_ON_WEIGHT,
            off_weight=config.EQ_OFF_WEIGHT,
            hysteresis=config.EQ_HYSTERESIS,
        ),
        "btc_trend": btc_trend_follow(
            close,
            ema_fast=config.BTC_EMA_FAST,
            ema_slow=config.BTC_EMA_SLOW,
            rsi_period=config.BTC_RSI_PERIOD,
            rsi_long_lo=config.BTC_RSI_LONG_LO,
            rsi_long_hi=config.BTC_RSI_LONG_HI,
            rsi_short_lo=config.BTC_RSI_SHORT_LO,
            rsi_short_hi=config.BTC_RSI_SHORT_HI,
            vol_lookback=config.VOL_LOOKBACK,
            sleeve_vol_target=config.BTC_SLEEVE_VOL_TARGET,
            hysteresis=config.BTC_HYSTERESIS,
        ),
        "defensive": defensive_basket(
            close,
            names=DEFENSIVE_TICKERS,
            horizons=config.DEFENSIVE_HORIZONS,
            vol_lookback=config.DEFENSIVE_VOL_LOOKBACK,
        ),
        "wq_earnings": wq_earnings_tilt(
            close,
            eps=eps,
            field=config.WQ_FIELD,
            window=config.WQ_WINDOW,
            publication_lag_days=config.WQ_PUBLICATION_LAG_DAYS,
            max_abs_weight=config.WQ_MAX_ABS_WEIGHT,
            hysteresis=config.WQ_HYSTERESIS,
        ),
    }
    return {n: e for n, e in all_sleeves.items() if n in config.SLEEVE_BUDGETS}


def monthly_table(ret_net: pd.Series, ret_gross: pd.Series,
                  gross_expo: pd.Series) -> pd.DataFrame:
    """Every month scored against both brief conditions."""
    m_net = monthly_returns(ret_net)
    m_gross = monthly_returns(ret_gross)
    tgt = config.MONTHLY_TARGET
    df = pd.DataFrame({
        "month": m_net.index.to_period("M").astype(str),
        "ret_net": m_net.values,
        "ret_gross": m_gross.reindex(m_net.index).values,
        "avg_gross_expo": gross_expo.resample("ME").mean().reindex(m_net.index).values,
    })
    df["positive"] = df.ret_net > 0
    df["hit_2pct"] = df.ret_net >= tgt
    df["in_band"] = (df.ret_net >= tgt) & (df.ret_net <= 0.04)
    df["shortfall_vs_2pct"] = df.ret_net - tgt
    df["cum_hit_rate"] = df.positive.expanding().mean()
    return df.set_index("month")


def monthly_grid(table: pd.DataFrame) -> pd.DataFrame:
    idx = pd.PeriodIndex(table.index, freq="M")
    g = pd.DataFrame({"y": idx.year, "m": idx.month, "r": table.ret_net.values})
    grid = g.pivot(index="y", columns="m", values="r")
    grid.columns = [pd.Timestamp(2000, c, 1).strftime("%b") for c in grid.columns]
    grid["year"] = (1 + g.groupby("y").r.apply(lambda s: (1 + s).prod() - 1)) - 1
    grid["n>=2%"] = g.groupby("y").r.apply(lambda s: int((s >= config.MONTHLY_TARGET).sum()))
    grid["n>0"] = g.groupby("y").r.apply(lambda s: int((s > 0).sum()))
    grid["n"] = g.groupby("y").r.size()
    return grid


def metrics(returns: pd.Series) -> dict:
    r = returns.dropna()
    ann = 252
    mu, sd = r.mean(), r.std(ddof=1)
    m = monthly_returns(r)
    nav = (1 + r).cumprod()
    dd = nav / nav.cummax() - 1
    downside = r[r < 0].std(ddof=1)
    years = len(r) / ann
    return {
        "CAGR": (nav.iloc[-1]) ** (1 / years) - 1 if years > 0 else np.nan,
        "AnnVol": sd * np.sqrt(ann),
        "Sharpe": mu / sd * np.sqrt(ann) if sd > 0 else np.nan,
        "Sortino": mu / downside * np.sqrt(ann) if downside and downside > 0 else np.nan,
        "Calmar": ((nav.iloc[-1]) ** (1 / years) - 1) / abs(dd.min()) if dd.min() < 0 else np.nan,
        "MaxDD": dd.min(),
        "AvgMonthly": m.mean(),
        "PctPosMonths": (m > 0).mean(),
        "BestMonth": m.max(),
        "WorstMonth": m.min(),
        "SkewMonthly": m.skew(),
        "KurtMonthly": m.kurtosis(),
        "NMonths": len(m),
    }


def main():
    data = load_all()
    close = close_frame(data)
    eps = load_eps()
    print(f"universe: {close.shape[1]} assets, {close.index[0].date()}..{close.index[-1].date()}")
    print(f"fundamentals: {eps.shape[1]} tickers with quarterly TTM EPS")
    print(f"fingerprint: {data_fingerprint(data)}")

    sleeves = build_sleeves(close, eps)
    bt = Backtester(data)
    res = bt.run(sleeves)

    sleeve_nets = {}
    for name, expo in sleeves.items():
        s = bt.run({name: expo})
        sleeve_nets[name] = s["ret_net"]
    pd.DataFrame(sleeve_nets).to_csv(config.OUTPUT / "sleeve_net_returns.csv")

    out = pd.DataFrame({
        "ret_gross": res["ret_gross"],
        "ret_net": res["ret_net"],
        "cost": res["cost"],
        "gross_exposure": res["gross_exposure"],
        "turnover": res["turnover"].sum(axis=1),
        "nav_gross": res["nav_gross"],
        "nav_net": res["nav_net"],
    })
    out.to_csv(config.OUTPUT / "daily_results.csv")
    res["expo"].to_csv(config.OUTPUT / "final_exposure.csv")

    mt = monthly_table(out["ret_net"], out["ret_gross"], out["gross_exposure"])
    mt.to_csv(config.OUTPUT / "monthly_table.csv")
    monthly_grid(mt).to_csv(config.OUTPUT / "monthly_grid.csv")

    # equity-only pre-crypto window: same sleeves, same parameters, no role in
    # building anything -- the holdout for the hit-rate claim
    bt_pre = Backtester(data, start=config.EQ_ROBUSTNESS_START,
                        end=config.EQ_ROBUSTNESS_END)
    eq_only = {k: v for k, v in sleeves.items() if k != "btc_trend"}
    pre_res = bt_pre.run(eq_only)
    pre_res_net = pre_res["ret_net"]
    pd.DataFrame({"ret_net": pre_res_net, "ret_gross": pre_res["ret_gross"],
                  "nav_net": pre_res["nav_net"],
                  "gross_exposure": pre_res["gross_exposure"]}
                 ).to_csv(config.OUTPUT / "equity_only_2010_2016.csv")

    is_mask = (out.index >= config.IS_START) & (out.index <= config.IS_END)
    oos_mask = out.index >= config.OOS_START
    summary = {
        "fingerprint": data_fingerprint(data),
        "sleeve_budgets": config.SLEEVE_BUDGETS,
        "full": metrics(out["ret_net"]),
        "equity_only_2010_2016": metrics(pre_res_net),
        "is_2017_2022": metrics(out.loc[is_mask, "ret_net"]),
        "oos_2023_plus": metrics(out.loc[oos_mask, "ret_net"]),
        "full_gross": metrics(out["ret_gross"]),
        "oos_gross": metrics(out.loc[oos_mask, "ret_gross"]),
        "monthly_net": {str(k.date()): v for k, v in monthly_returns(out["ret_net"]).items()},
        "monthly_gross": {str(k.date()): v for k, v in monthly_returns(out["ret_gross"]).items()},
        "is_gross": metrics(out.loc[is_mask, "ret_gross"]),
    }
    with open(config.OUTPUT / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)

    for k in ["full", "equity_only_2010_2016", "is_2017_2022", "oos_2023_plus"]:
        mm = summary[k]
        print(f"\n== {k} (net) ==")
        for key in ["CAGR", "AnnVol", "Sharpe", "MaxDD", "AvgMonthly", "PctPosMonths", "NMonths"]:
            print(f"  {key}: {mm[key]:.4f}" if isinstance(mm[key], float) else f"  {key}: {mm[key]}")

    print("\n== month by month, net (>= 2.0% flagged) ==")
    grid = monthly_grid(mt)
    mon = [c for c in grid.columns if c not in ("year", "n>=2%", "n>0", "n")]
    print("      " + "".join(f"{c:>8}" for c in mon) + f"{'YEAR':>9}{'>=2%':>6}{'>0':>5}")
    for y, row in grid.iterrows():
        cells = ""
        for c in mon:
            v = row[c]
            if pd.isna(v):
                cells += f"{'':>8}"
            else:
                cells += f"{v * 100:>7.1f}" + ("*" if v >= config.MONTHLY_TARGET else " ")
        print(f"{y:>6}{cells}{row['year'] * 100:>8.1f}%{int(row['n>=2%']):>6}"
              f"{int(row['n>0']):>4}/{int(row['n'])}")
    print(f"\n  months >= {config.MONTHLY_TARGET:.0%}: {int(mt.hit_2pct.sum())}/{len(mt)} "
          f"({mt.hit_2pct.mean():.1%})   in 2-4% band: {int(mt.in_band.sum())}/{len(mt)} "
          f"({mt.in_band.mean():.1%})")
    print(f"  positive months:  {int(mt.positive.sum())}/{len(mt)} ({mt.positive.mean():.1%})"
          f"   target {config.HIT_RATE_TARGET:.0%} -> "
          f"{'MET' if mt.positive.mean() > config.HIT_RATE_TARGET else 'MISSED'}")
    print("\nSaved outputs to", config.OUTPUT)


if __name__ == "__main__":
    main()
