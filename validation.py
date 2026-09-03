"""Overfitting diagnostics.

- deflated_sharpe: Bailey & Lopez de Prado (2014), "The Deflated Sharpe
  Ratio". Corrects a Sharpe ratio for the expected maximum Sharpe of N
  independent trials given the trial Sharpe dispersion.
- parameter_sensitivity: recompute a sleeve's OOS Sharpe over a small grid of
  its parameter(s) and report the surface.
- correlation_matrix: pairwise correlation of sleeve net returns (low
  correlation = combined result is not one lucky bet).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats


def sharpe(r: pd.Series, periods: int = 252) -> float:
    r = r.dropna()
    if r.std() == 0 or len(r) < 2:
        return 0.0
    return r.mean() / r.std() * np.sqrt(periods)


def psr_variance(T: int, skew: float, excess_kurt: float, sr_ann: float,
                 periods: int = 252) -> float:
    """Variance of the ANNUALISED Sharpe estimator (Lo 2002 / Bailey & Lopez de
    Prado).

    The published formula is in per-observation Sharpe units and uses the raw
    (non-excess) kurtosis, so both conversions happen here: `sr_ann` is
    de-annualised and `excess_kurt` (what pandas .kurtosis() returns) has 3
    added back. Getting either wrong inflates PSR to 1.0.
    """
    sr = sr_ann / np.sqrt(periods)
    kurt = excess_kurt + 3.0
    var_per_obs = (1 - skew * sr + (kurt - 1) / 4 * sr ** 2) / (T - 1)
    return float(var_per_obs * periods)


def deflated_sharpe(sr: float, T: int, skew: float, excess_kurt: float,
                    n_trials: int, sr_trials: np.ndarray | None = None) -> float:
    """Probability that the true SR > 0 after deflating for n_trials trials.

    If sr_trials (the Sharpe ratios of all candidates tried) is given, the
    expected max SR under the null is estimated empirically from them;
    otherwise an iid N(0, var) assumption across trials is used with an
    assumed cross-trial dispersion of 0.5 in annualized SR units.
    """
    sr = float(sr)
    T = int(T)
    if T < 20:
        return 0.0
    var_sr = psr_variance(T, skew, excess_kurt, sr)
    if sr_trials is not None and len(sr_trials) > 1:
        sr0 = expected_max_sr_empirical(np.asarray(sr_trials, float))
    else:
        # fallback: assume trial SRs ~ N(0, 0.5^2) annualized
        trials = 0.5
        gamma = np.euler_gamma
        sr0 = trials * ((1 - gamma) * stats.norm.ppf(1 - 1.0 / n_trials)
                        + gamma * stats.norm.ppf(1 - 1.0 / (n_trials * np.e)))
    dsr = stats.norm.cdf((sr - sr0) / np.sqrt(max(var_sr, 1e-12)))
    return float(dsr)


def expected_max_sr_empirical(sr_trials: np.ndarray) -> float:
    """Expected maximum Sharpe under the null, estimated by permutation:
    for k trials each of length T with zero mean, max Sharpe over k draws.
    Monte-Carlo over the observed trial dispersion."""
    rng = np.random.default_rng(0)
    k = len(sr_trials)
    sd = np.std(sr_trials)
    if sd == 0:
        sd = 0.5
    sims = np.max(rng.normal(0, sd, size=(2000, k)), axis=1)
    return float(np.mean(sims))


def parameter_sensitivity(sleeve_fn, grid: dict, data: dict,
                          oos_slice: slice, bt_factory) -> pd.DataFrame:
    """Recompute OOS net Sharpe for each parameter combination in `grid`
    (dict param -> list of values). Returns a DataFrame with one row per combo.
    `sleeve_fn(**params) -> exposure panel`; `bt_factory()` -> fresh Backtester.
    """
    rows = []
    keys = list(grid)
    from itertools import product
    for combo in product(*(grid[k] for k in keys)):
        params = dict(zip(keys, combo))
        expo = sleeve_fn(**params)
        bt = bt_factory()
        res = bt.run({"s": expo})
        sr = sharpe(res["ret_net"].iloc[oos_slice])
        rows.append({**params, "oos_net_sharpe": sr})
    return pd.DataFrame(rows)


def correlation_matrix(sleeve_nets: pd.DataFrame) -> pd.DataFrame:
    return sleeve_nets.corr()


def prob_backtest_overfit(sr_is: float, sr_oos: float, n_trials: int = 2) -> float:
    """Simple relative-performance proxy for PBO (Bailey et al.'s CSCV uses
    rank statistics across trials; with few candidates we report the OOS/IS
    Sharpe decay ratio instead, which is the practically relevant quantity)."""
    if sr_is == 0:
        return np.nan
    return sr_oos / sr_is
