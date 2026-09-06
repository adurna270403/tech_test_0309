"""Post-review validation protocol (2025-09 review, points 3-8).

Re-runs selection honestly under a three-epoch split, adds walk-forward,
block bootstrap, leave-one-out contribution and a re-counted DSR:
  1. Budget selection on RESEARCH+VALIDATION (2019-01..2024-12) only --
     the shipped grid previously used OOS results to pick the budget.
  2. HOLDOUT (2025-01..present) scored once, with the frozen budgets.
  3. Walk-forward: choose the budget cell on trailing data, test the next
     12-month block, chain all blocks into one performance record.
  4. Stationary block bootstrap of daily net returns: distributions of
     Sharpe / MaxDD / % positive months instead of single numbers.
  5. Leave-one-out per sleeve: full book vs book minus equity/btc/def/wd_mom.
  6. DSR charged with the honest trial count (every grid cell and variant
     ever evaluated, ~120+), not the 8 reported before.

Run:  conda run -n sandbox python run_protocol.py
"""
import json
import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from backtest import Backtester, monthly_returns
from run_backtest import build_sleeves
from validation import psr_variance, sharpe

OUT = config.OUTPUT

BUDGET_GRID = [(dw, cw) for dw in [0.10, 0.12, 0.15, 0.18]
               for cw in [0.20, 0.25, 0.30, 0.35, 0.40]]


def load_data():
    from data.loaders import close_frame, load_all, load_eps
    data = load_all()
    return data, close_frame(data), load_eps()


def run_budget(data, sleeves, budgets, start=None, end=None):
    """One backtest with a given sleeve-budget dict; returns daily net ret."""
    original = dict(config.SLEEVE_BUDGETS)
    try:
        tot = sum(budgets.values())
        config.SLEEVE_BUDGETS = {k: v / tot for k, v in budgets.items()}
        bt = Backtester(data, start=start, end=end)
        return bt.run(sleeves)["ret_net"].dropna()
    finally:
        config.SLEEVE_BUDGETS = original


def slice_win(r: pd.Series, lo: str, hi: str) -> pd.Series:
    return r[(r.index >= lo) & (r.index <= hi)]


def window_stats(r: pd.Series) -> dict:
    from run_backtest import metrics
    m = metrics(r)
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}


# --- 1. budget selection on research+validation only -------------------------
def select_budgets(data, sleeves) -> tuple[dict, pd.DataFrame]:
    """Score all 20 budget cells on RESEARCH+VALIDATION only; pick by the
    same rule the shipped config used (hit rate first, then avg month), but
    with no access to 2025+ data."""
    rows = []
    for dw, cw in BUDGET_GRID:
        b = {"equity_core": max(0.05, 1.0 - dw - cw),
             "btc_trend": cw, "defensive": dw}
        r = run_budget(data, sleeves, b)
        trv = slice_win(r, config.START, config.VALIDATION_END)
        hold = slice_win(r, config.HOLDOUT_START, "2100-01-01")
        m_trv = monthly_returns(trv)
        rows.append({
            "defensive_budget": dw, "crypto_budget": cw,
            "trv_avg_monthly": m_trv.mean(),
            "trv_pct_pos": (m_trv > 0).mean(),
            "trv_sharpe": sharpe(trv),
            # recorded for the report table only; NOT used for selection
            "holdout_avg_monthly": monthly_returns(hold).mean(),
            "holdout_pct_pos": (monthly_returns(hold) > 0).mean(),
            "holdout_sharpe": sharpe(hold),
        })
    df = pd.DataFrame(rows)
    # selection rule, fixed a priori: max hit rate; tie-break on avg month
    df["selected"] = (df.trv_pct_pos == df.trv_pct_pos.max())
    sel = df[df.selected].sort_values("trv_avg_monthly", ascending=False).iloc[0]
    chosen = {"equity_core": max(0.05, 1.0 - sel.defensive_budget - sel.crypto_budget),
              "btc_trend": sel.crypto_budget, "defensive": sel.defensive_budget}
    df.to_csv(OUT / "protocol_budget_selection.csv", index=False)
    return chosen, df


# --- 2. holdout score (once, frozen) ------------------------------------------
def holdout_score(data, sleeves, budgets) -> dict:
    r = run_budget(data, sleeves, budgets)
    out = {}
    for name, lo, hi in [("research", config.IS_START, config.IS_END),
                         ("validation", config.OOS_START, config.VALIDATION_END),
                         ("holdout", config.HOLDOUT_START, "2100-01-01"),
                         ("research+validation", config.IS_START,
                          config.VALIDATION_END)]:
        out[name] = window_stats(slice_win(r, lo, hi))
    return out


# --- 3. walk-forward -----------------------------------------------------------
def walk_forward(data, sleeves, train_years: int = 4, test_years: int = 1):
    """Anchored-expanding walk-forward: pick the budget cell on all data
    before year Y (hit rate first, avg month tie-break), test year Y.
    2019-2022 is pure research; selection starts testing 2023 onward."""
    rows = []
    years = list(range(2023, 2027))  # test blocks 2023, 2024, 2025, 2026YTD
    full_ret = {  # precompute each cell's full daily returns once
        (dw, cw): run_budget(
            data, sleeves,
            {"equity_core": max(0.05, 1.0 - dw - cw),
             "btc_trend": cw, "defensive": dw})
        for dw, cw in BUDGET_GRID}
    for test_year in years:
        lo, hi = f"{test_year}-01-01", f"{test_year}-12-31"
        rows_wf = []
        for (dw, cw), r in full_ret.items():
            train = slice_win(r, config.START, f"{test_year - 1}-12-31")
            m = monthly_returns(train)
            rows_wf.append({"dw": dw, "cw": cw,
                            "train_pct_pos": (m > 0).mean(),
                            "train_avg": m.mean()})
        tr = pd.DataFrame(rows_wf)
        tr["sel"] = tr.train_pct_pos == tr.train_pct_pos.max()
        pick = tr[tr.sel].sort_values("train_avg", ascending=False).iloc[0]
        r = full_ret[(pick.dw, pick.cw)]
        test = slice_win(r, lo, hi)
        m = monthly_returns(test)
        rows.append({
            "test_year": test_year,
            "picked_defensive": pick.dw, "picked_crypto": pick.cw,
            "train_pct_pos": round(pick.train_pct_pos, 4),
            "test_avg_monthly": m.mean(),
            "test_pct_pos": (m > 0).mean(),
            "test_sharpe": sharpe(test),
            "test_max_dd": (1 + test).cumprod().div(
                (1 + test).cumprod().cummax()).sub(1).min(),
        })
    wf = pd.DataFrame(rows)
    wf.to_csv(OUT / "walkforward.csv", index=False)
    return wf, full_ret


# --- 4. stationary block bootstrap ---------------------------------------------
def block_bootstrap(r: pd.Series, n_boot: int = 2000, block: int = 20,
                    seed: int = 0) -> pd.DataFrame:
    """Stationary bootstrap (Politis-Romano) on daily net returns. Returns
    bootstrap distribution of Sharpe / MaxDD / pct positive months / avg month."""
    rng = np.random.default_rng(seed)
    r = r.dropna().to_numpy()
    n = len(r)
    p = 1.0 / block  # expected block length ~ `block` days
    stats = []
    for _ in range(n_boot):
        idx = np.empty(n, dtype=int)
        t = 0
        while t < n:
            start = rng.integers(0, n)
            ln = rng.geometric(p)
            end = min(n, start + ln)
            take = np.arange(start, end)
            k = min(len(take), n - t)
            idx[t:t + k] = take[:k]
            t += k
        x = r[idx]
        ann = np.sqrt(252)
        sr = x.mean() / x.std() * ann if x.std() > 0 else 0.0
        cum = np.cumprod(1 + x)
        dd = (cum / np.maximum.accumulate(cum) - 1).min()
        mon = pd.Series(1 + x, index=pd.date_range(
            "2000-01-01", periods=n, freq="D")).resample("ME").prod() - 1
        stats.append((sr, dd, (mon > 0).mean(), mon.mean()))
    return pd.DataFrame(stats, columns=["sharpe", "max_dd", "pct_pos", "avg_month"])


# --- 5. leave-one-out sleeve contribution ---------------------------------------
def leave_one_out(data, sleeves) -> pd.DataFrame:
    """Full portfolio vs drop-one-sleeve variants, per epoch."""
    full = {n: b for n, b in config.SLEEVE_BUDGETS.items()}
    variants = {"full": full}
    for drop in ["equity_core", "btc_trend", "defensive", "wd_mom"]:
        rest = {n: b for n, b in full.items() if n != drop}
        tot = sum(rest.values())
        variants[f"drop_{drop}"] = {n: b / tot for n, b in rest.items()}
    rows = []
    for name, b in variants.items():
        r = run_budget(data, sleeves, b)
        for epoch, lo, hi in [("full", config.START, "2100-01-01"),
                              ("research", config.IS_START, config.IS_END),
                              ("validation", config.OOS_START, config.VALIDATION_END),
                              ("holdout", config.HOLDOUT_START, "2100-01-01")]:
            s = window_stats(slice_win(r, lo, hi))
            rows.append({"variant": name, "epoch": epoch, **s})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "leave_one_out.csv", index=False)
    return df


# --- 6. honest DSR ---------------------------------------------------------------
# Every configuration whose full-sample performance was ever computed while
# developing this book (grid cells, ablations, alternative sleeves, execution
# modes, cost models). Counted from the outputs actually saved in output/.
def honest_trial_count() -> int:
    import csv
    n = 0
    for f, row_kw in [("hitrate_frontier.csv", None), ("budget_frontier.csv", None),
                      ("risk_ablation.csv", None), ("cost_sensitivity.csv", None),
                      ("execution_sensitivity.csv", None),
                      ("candidate_alphas.csv", None),
                      ("cross_asset_experiment.csv", None)]:
        p = OUT / f
        if p.exists():
            with open(p) as fh:
                n += sum(1 for _ in csv.reader(fh)) - 1
    n += len(pd.read_csv(OUT / "monthly_take_profit_grid.csv", index_col=0)) \
        if (OUT / "monthly_take_profit_grid.csv").exists() else 0
    n += 20  # protocol re-selection grid (this run)
    return max(n, 1)


def deflated_sharpe_honest(ret: pd.Series, n_trials: int) -> dict:
    from validation import deflated_sharpe
    r = ret.dropna()
    sr = sharpe(r)
    return {
        "n_trials": n_trials,
        "sr": sr,
        "dsr": deflated_sharpe(sr, len(r), float(r.skew()),
                               float(r.kurtosis()), n_trials),
        "dsr_old_8": deflated_sharpe(sr, len(r), float(r.skew()),
                                     float(r.kurtosis()), 8),
    }


def main():
    data, close, eps = load_data()
    sleeves = build_sleeves(close, eps,
                            data_volumes={s: d["volume"] for s, d in data.items()})
    print(f"data: {close.shape[1]} assets {close.index[0].date()}..{close.index[-1].date()}")

    results = {}

    print("[1/6] budget selection on research+validation ...")
    chosen, sel_df = select_budgets(data, sleeves)
    results["selected_budgets"] = chosen
    print("  chosen:", chosen)

    print("[2/6] holdout score with frozen budgets ...")
    results["epochs"] = holdout_score(data, sleeves, chosen)
    for k, v in results["epochs"].items():
        print(f"  {k:22s} avg_month={v['AvgMonthly']:.4f} "
              f"pos={v['PctPosMonths']:.3f} sharpe={v['Sharpe']:.2f}")

    print("[3/6] walk-forward ...")
    wf, full_ret = walk_forward(data, sleeves)
    pooled = pd.concat([
        slice_win(full_ret[(float(r.picked_defensive), float(r.picked_crypto))],
                  f"{int(r.test_year)}-01-01", f"{int(r.test_year)}-12-31")
        for _, r in wf.iterrows()])
    wf_monthly = monthly_returns(pooled)
    results["walkforward_summary"] = {
        "n_blocks": len(wf),
        "avg_monthly": float(wf_monthly.mean()),
        "pct_pos": float((wf_monthly > 0).mean()),
        "sharpe_pooled": sharpe(pooled),
        "block_pct_pos": [float(x) for x in wf.test_pct_pos],
        "picks": [[int(r.test_year), float(r.picked_defensive),
                   float(r.picked_crypto)] for _, r in wf.iterrows()],
    }
    print(f"  blocks: {len(wf)}, avg month {results['walkforward_summary']['avg_monthly']:.4f}")

    print("[4/6] block bootstrap ...")
    r_full = run_budget(data, sleeves, chosen)
    boots = block_bootstrap(r_full)
    boots.to_csv(OUT / "bootstrap_sharpe_dd.csv", index=False)
    q = boots.quantile([0.05, 0.5, 0.95])
    results["bootstrap"] = {
        stat: {"p05": float(q.loc[0.05, stat]), "median": float(q.loc[0.5, stat]),
               "p95": float(q.loc[0.95, stat])}
        for stat in ["sharpe", "max_dd", "pct_pos", "avg_month"]}
    print("  bootstrap Sharpe 5-95%:",
          round(results["bootstrap"]["sharpe"]["p05"], 2), "-",
          round(results["bootstrap"]["sharpe"]["p95"], 2))

    print("[5/6] leave-one-out ...")
    loo = leave_one_out(data, sleeves)
    print("  variants:", sorted(loo.variant.unique()))

    print("[6/6] honest DSR ...")
    n = honest_trial_count()
    results["dsr"] = deflated_sharpe_honest(r_full, n)
    print(f"  n_trials={n}, DSR={results['dsr']['dsr']:.3f} "
          f"(old 8-trial DSR={results['dsr']['dsr_old_8']:.3f})")

    loo.to_csv(OUT / "leave_one_out.csv", index=False)
    wf.to_csv(OUT / "walkforward.csv", index=False)
    with open(OUT / "protocol_summary.json", "w") as f:
        json.dump(results, f, indent=2, default=str)

    print("\nSaved:", OUT / "protocol_summary.json",
          OUT / "protocol_budget_selection.csv",
          OUT / "walkforward.csv",
          OUT / "bootstrap_sharpe_dd.csv",
          OUT / "leave_one_out.csv")


if __name__ == "__main__":
    main()
