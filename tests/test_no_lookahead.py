"""No-lookahead / leakage tests. These fail loudly if a signal ever uses
future data: shifting raw prices FORWARD must not change any signal value on
past dates (future data is invisible to a causal function).
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals.trend import ts_momentum
from signals.reversion import mean_reversion
from signals.btc_trend import btc_trend_follow
from signals.defensive import defensive_basket
from signals.equity_core import equity_core
from signals.wq_earnings import last_diff_value, wq_earnings_tilt


def make_prices(n: int = 300, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2020-01-01", periods=n)
    prices = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, size=(n, 3)), axis=0))
    return pd.DataFrame(prices, index=idx, columns=["A", "B", "C"])


def make_btc_panel(n: int = 400, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    btc = 10000 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    eth = 200 * np.exp(np.cumsum(rng.normal(0.001, 0.04, n)))
    eq = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.015, n)))
    return pd.DataFrame({"BTCUSDT": btc, "ETHUSDT": eth, "AAPL": eq}, index=idx)


CAUSAL_SIGNALS = [
    lambda px: ts_momentum(px),
    lambda px: mean_reversion(px),
]


@pytest.mark.parametrize("i", range(len(CAUSAL_SIGNALS)))
def test_signal_invariant_to_future_data(i):
    px = make_prices()
    sig = CAUSAL_SIGNALS[i]
    base = sig(px)
    shifted = px.copy()
    # append 60 future days: a leaking signal would change past values after
    # recompute only if it used data beyond day t; appending future data must
    # leave past rows identical
    future = px.tail(60) * 1.5 + 5
    px_ext = pd.concat([px, future])
    ext = sig(px_ext)
    # past rows (all of base's index) must be unchanged
    past = ext.iloc[: len(base)]
    # compare excluding the warmup rows where the rolling windows differ only
    # through legitimately available data (they shouldn't differ at all)
    pd.testing.assert_frame_equal(
        base.ffill().fillna(0), past.ffill().fillna(0), check_exact=False, rtol=1e-10,
        check_freq=False,
    )


def test_signal_invariant_to_truncation():
    """Complement: dropping future rows must not change past signal values."""
    px = make_prices()
    sig = ts_momentum
    full = sig(px)
    trunc = sig(px.iloc[:-40])
    pd.testing.assert_frame_equal(
        full.iloc[:-40].ffill().fillna(0), trunc.ffill().fillna(0),
        check_exact=False, rtol=1e-10, check_freq=False,
    )


def test_btc_trend_invariant_to_future_data():
    px = make_btc_panel()
    base = btc_trend_follow(px)
    future = px.tail(60) * 1.2 + 3
    ext = btc_trend_follow(pd.concat([px, future]))
    pd.testing.assert_frame_equal(
        base.ffill().fillna(0),
        ext.iloc[: len(base)].ffill().fillna(0),
        check_exact=False,
        rtol=1e-10,
        check_freq=False,
    )


def _single_asset_data(n: int = 200, seed: int = 3):
    """One asset whose open and close differ, so the fill-price assumption is
    actually observable in the P&L."""
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(seed)
    close = pd.Series(100 * (1 + rng.normal(0.001, 0.01, n)).cumprod(), index=idx)
    # open is a distinct price, not a copy of the close
    open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.004, n))
    df = pd.DataFrame({"open": open_, "high": np.maximum(open_, close) * 1.01,
                       "low": np.minimum(open_, close) * 0.99, "close": close,
                       "volume": 1.0}, index=idx)
    return {"X": df}, idx, close, open_


@pytest.mark.parametrize("mode,price,lag", [
    ("same_close", "close", 1),
    ("next_close", "close", 2),
    ("next_open", "open", 2),
])
def test_execution_lag_and_fill_price(mode, price, lag):
    """P&L must equal exposure lagged by the mode's lag, times the return of the
    mode's fill price. This is what stops a signal computed from the close of
    day t from being filled at that same close."""
    import config
    from backtest import Backtester

    old_tp = config.MONTHLY_TP
    config.MONTHLY_TP = 0.0
    try:
        data, idx, close, open_ = _single_asset_data()
        prices = {"close": close, "open": open_}[price]
        rets = prices.pct_change()

        # an exposure panel that "knows" tomorrow: only the lag protects us
        cheat = pd.DataFrame({"X": np.sign(close.pct_change().shift(-1))}, index=idx)
        bt = Backtester(data, execution=mode)
        assert bt.lag == lag
        res = bt.run({"s": cheat})

        manual = res["expo"]["X"].shift(lag).fillna(0) * rets.fillna(0)
        pd.testing.assert_series_equal(
            (res["ret_gross"] - res["rf"]).rename(None), manual.rename(None),
            check_exact=False, rtol=1e-8,
        )
        # nothing can be earned before a position has been filled
        assert (res["ret_gross"] - res["rf"]).iloc[:lag].abs().max() == 0.0
    finally:
        config.MONTHLY_TP = old_tp


def test_same_close_fill_is_more_optimistic():
    """Sanity check on the fill assumption itself: a perfect one-day-ahead
    forecast must be worth strictly more when it is filled at the signal's own
    close than when it is filled at the next open."""
    import config
    from backtest import Backtester

    old_tp = config.MONTHLY_TP
    config.MONTHLY_TP = 0.0
    try:
        data, idx, close, _ = _single_asset_data()
        cheat = pd.DataFrame({"X": np.sign(close.pct_change().shift(-1))}, index=idx)
        navs = {}
        for mode in ("same_close", "next_open"):
            res = Backtester(data, execution=mode).run({"s": cheat})
            navs[mode] = res["nav_gross"].iloc[-1]
        assert navs["same_close"] > navs["next_open"]
    finally:
        config.MONTHLY_TP = old_tp


def test_lag_counted_in_each_assets_own_calendar():
    """A mixed crypto/equity panel must not shrink the equity fill lag.

    Regression test. With a flat `.shift(lag)` over the union calendar, the
    Monday row picks up Saturday's exposure -- which is Friday's close forward
    filled -- while the Monday open-to-open return spans Friday's whole session.
    A signal that reads Friday's close then gets paid for Friday's move. Here a
    5-day cross-sectional momentum signal is the canary: under the leak it earns
    a wildly implausible Sharpe, and under a correct lag it earns ~nothing.
    """
    import config
    from backtest import Backtester

    old_tp = config.MONTHLY_TP
    config.MONTHLY_TP = 0.0
    try:
        rng = np.random.default_rng(7)
        full = pd.date_range("2020-01-01", periods=900, freq="D")
        weekday = full[full.dayofweek < 5]

        data = {}
        for i in range(6):  # equities: weekdays only
            idx = weekday
            close = pd.Series(100 * (1 + rng.normal(0, 0.015, len(idx))).cumprod(), idx)
            open_ = close.shift(1).fillna(close.iloc[0]) * (1 + rng.normal(0, 0.008, len(idx)))
            data[f"E{i}"] = pd.DataFrame(
                {"open": open_, "high": np.maximum(open_, close),
                 "low": np.minimum(open_, close), "close": close, "volume": 1.0})
        # one 7-day asset, which is what forces the union calendar
        close = pd.Series(100 * (1 + rng.normal(0, 0.03, len(full))).cumprod(), full)
        data["CUSDT"] = pd.DataFrame(
            {"open": close.shift(1).fillna(close.iloc[0]), "high": close,
             "low": close, "close": close, "volume": 1.0})

        bt = Backtester(data, start="2020-01-01")
        px = bt.close[[f"E{i}" for i in range(6)]].ffill()
        score = px / px.shift(5) - 1
        score = score.sub(score.mean(axis=1), axis=0)
        sig = score.div(score.abs().sum(axis=1), axis=0).fillna(0.0)

        r = bt.run({"m": sig})["ret_gross"]
        sharpe = r.mean() / r.std() * np.sqrt(252)
        # prices are pure noise, so any real Sharpe here is leakage
        assert abs(sharpe) < 0.75, f"5-day signal on noise earned Sharpe {sharpe:.2f}"
    finally:
        config.MONTHLY_TP = old_tp


def test_engine_exposure_invariant_to_future_data():
    """The risk overlays (vol target, drawdown throttle, crash guard, risk
    budgets) run inside the engine and are not covered by the signal-level
    causality tests. Appending future bars must not change any past exposure."""
    import config
    from backtest import Backtester

    old_tp = config.MONTHLY_TP
    config.MONTHLY_TP = 0.0
    try:
        data, idx, close, _ = _single_asset_data(n=400, seed=11)
        # a second asset so the sleeve/budget machinery has something to weigh
        data2, _, close2, _ = _single_asset_data(n=400, seed=12)
        data = {"X": data["X"], "Y": data2["X"]}

        sig = pd.DataFrame({"X": 0.5, "Y": -0.3}, index=idx)
        base = Backtester(data).run({"a": sig[["X"]], "b": sig[["Y"]]})["expo"]

        future_idx = pd.bdate_range(idx[-1] + pd.Timedelta(days=1), periods=80)
        ext_data = {}
        for k, df in data.items():
            # a violent future path: anything that leaks would show up loudly
            tail = df.tail(80).to_numpy() * 3.0
            ext_data[k] = pd.concat(
                [df, pd.DataFrame(tail, index=future_idx, columns=df.columns)])
        sig_ext = pd.concat(
            [sig, pd.DataFrame({"X": 0.5, "Y": -0.3}, index=future_idx)])
        ext = Backtester(ext_data).run(
            {"a": sig_ext[["X"]], "b": sig_ext[["Y"]]})["expo"]

        pd.testing.assert_frame_equal(
            base, ext.iloc[: len(base)], check_exact=False, rtol=1e-10,
            check_freq=False,
        )
    finally:
        config.MONTHLY_TP = old_tp


def make_equity_panel(n: int = 500, seed: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2019-01-01", periods=n)
    cols = ["SPY", "AAA", "BBB", "CCC", "DDD"]
    px = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.013, size=(n, len(cols))), axis=0))
    return pd.DataFrame(px, index=idx, columns=cols)


def make_eps_panel(index: pd.DatetimeIndex, seed: int = 6) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    quarters = pd.date_range(index[0] - pd.Timedelta(days=400), index[-1], freq="QE")
    cols = ["AAA", "BBB", "CCC", "DDD"]
    return pd.DataFrame(rng.uniform(1.0, 9.0, size=(len(quarters), len(cols))),
                        index=quarters, columns=cols)


def test_equity_core_invariant_to_future_data():
    px = make_equity_panel()
    eqs = ["AAA", "BBB", "CCC", "DDD"]
    base = equity_core(px, eqs)
    ext = equity_core(pd.concat([px, px.tail(60) * 1.3 + 4]), eqs)
    pd.testing.assert_frame_equal(
        base.ffill().fillna(0), ext.iloc[: len(base)].ffill().fillna(0),
        check_exact=False, rtol=1e-10, check_freq=False,
    )


def test_defensive_invariant_to_future_data():
    px = make_equity_panel()
    names = ["AAA", "BBB"]
    base = defensive_basket(px, names, horizons=(5, 10, 20), vol_lookback=10)
    ext = defensive_basket(pd.concat([px, px.tail(60) * 1.3 + 4]), names,
                           horizons=(5, 10, 20), vol_lookback=10)
    pd.testing.assert_frame_equal(
        base.ffill().fillna(0), ext.iloc[: len(base)].ffill().fillna(0),
        check_exact=False, rtol=1e-10, check_freq=False,
    )


def test_defensive_gate_is_unanimous_and_flat_when_nothing_trends():
    """The sleeve must be able to hold nothing.

    A defensive basket that always rotates into its least-bad leg is a
    permanent short in returns, so a falling panel has to produce zero gross,
    and the gate has to require every horizon to agree rather than a majority.
    """
    idx = pd.bdate_range("2015-01-01", periods=400)
    down = pd.DataFrame({"AAA": np.linspace(100, 40, 400),
                         "BBB": np.linspace(100, 55, 400)}, index=idx)
    expo = defensive_basket(down, ["AAA", "BBB"], horizons=(5, 10, 20), vol_lookback=10)
    assert expo.abs().sum(axis=1).max() == 0.0

    up = pd.DataFrame({"AAA": np.linspace(40, 100, 400),
                       "BBB": np.linspace(55, 100, 400)}, index=idx)
    assert defensive_basket(up, ["AAA", "BBB"], horizons=(5, 10, 20),
                            vol_lookback=10).abs().sum(axis=1).iloc[-1] > 0.9

    # one leg up, one leg down -> half gross, and only the trending leg is held
    mixed = pd.DataFrame({"AAA": np.linspace(40, 100, 400),
                          "BBB": np.linspace(100, 55, 400)}, index=idx)
    mx = defensive_basket(mixed, ["AAA", "BBB"], horizons=(5, 10, 20), vol_lookback=10)
    assert mx["BBB"].abs().max() == 0.0
    assert abs(mx["AAA"].iloc[-1] - 0.5) < 1e-9


def test_wq_earnings_invariant_to_future_data():
    """Appending genuinely future prices AND future earnings must leave every
    past signal value untouched."""
    px = make_equity_panel()
    eps = make_eps_panel(px.index)
    base = wq_earnings_tilt(px, eps)

    future_idx = pd.bdate_range(px.index[-1] + pd.Timedelta(days=1), periods=60)
    px_ext = pd.concat([px, pd.DataFrame(px.tail(60).to_numpy() * 1.3 + 4,
                                         index=future_idx, columns=px.columns)])
    future_q = pd.date_range(eps.index[-1] + pd.Timedelta(days=1), periods=2, freq="QE")
    eps_ext = pd.concat([eps, pd.DataFrame(eps.tail(2).to_numpy() * 3.0 + 7,
                                           index=future_q, columns=eps.columns)])
    ext = wq_earnings_tilt(px_ext, eps_ext)

    pd.testing.assert_frame_equal(
        base.ffill().fillna(0), ext.iloc[: len(base)].ffill().fillna(0),
        check_exact=False, rtol=1e-10, check_freq=False,
    )


def test_eps_publication_lag_respected():
    """A fundamental stamped at fiscal period end D must be invisible to the
    signal until D + publication_lag_days. Perturbing one EPS observation must
    leave every signal date before that boundary untouched, and must change the
    signal afterwards."""
    px = make_equity_panel()
    eps = make_eps_panel(px.index)
    lag = 60
    base = wq_earnings_tilt(px, eps, publication_lag_days=lag,
                            max_abs_weight=1.0, hysteresis=0.0)

    # perturb a period well inside the sample so both sides of the boundary are
    # long enough to be meaningful
    period = eps.index[-5]
    bumped = eps.copy()
    bumped.loc[period, "AAA"] = bumped.loc[period, "AAA"] * 5.0 + 20.0
    after = wq_earnings_tilt(px, bumped, publication_lag_days=lag,
                             max_abs_weight=1.0, hysteresis=0.0)

    boundary = period + pd.Timedelta(days=lag)
    before = px.index < boundary
    assert before.sum() > 100 and (~before).sum() > 100, "test window too small"
    pd.testing.assert_frame_equal(
        base.loc[before], after.loc[before], check_exact=False, rtol=1e-10,
    )
    assert not np.allclose(base.loc[~before].to_numpy(),
                           after.loc[~before].to_numpy())


def test_last_diff_value_is_backward_looking():
    """last_diff_value must return the previous DISTINCT value, never a future
    one, and must respect the lookback window."""
    idx = pd.bdate_range("2020-01-01", periods=8)
    x = pd.DataFrame({"A": [1.0, 1.0, 2.0, 2.0, 2.0, 3.0, 3.0, 1.0]}, index=idx)
    got = last_diff_value(x, 504)["A"].tolist()
    #        t0   t1(=1, none differ)  t2(prev 1)  t3(prev 1) t4(prev 1) t5(prev 2) t6(prev 2) t7(prev 3)
    expected = [np.nan, np.nan, 1.0, 1.0, 1.0, 2.0, 2.0, 3.0]
    for g, e in zip(got, expected):
        assert (np.isnan(g) and np.isnan(e)) or abs(g - e) < 1e-12, (got, expected)

    # window truncation: with d=1 only yesterday can be looked at
    got1 = last_diff_value(x, 1)["A"].tolist()
    assert np.isnan(got1[1]), got1          # yesterday equals today -> nothing
    assert abs(got1[2] - 1.0) < 1e-12, got1  # yesterday differs -> found


def test_gross_cap_enforced():
    from backtest import Backtester
    n = 60
    idx = pd.bdate_range("2020-01-01", periods=n)
    px = pd.DataFrame({f"A{i}": 100.0 for i in range(5)}, index=idx)
    data = {c: pd.DataFrame({"close": px[c], "open": px[c], "high": px[c],
                             "low": px[c], "volume": 1.0}, index=idx)
            for c in px.columns}
    bt = Backtester(data)
    big = pd.DataFrame(10.0, index=idx, columns=list(px.columns))
    res = bt.run({f"s{i}": big[[c]] for i, c in enumerate(px.columns)})
    assert (res["gross_exposure"] <= 1.0 + 1e-9).all(), "gross cap breached"


def test_gross_cap_holds_on_a_mixed_trading_calendar():
    """The cap must bind on the book HELD, not just on the decision panel.

    Counting the execution lag in each asset's own calendar (which is required
    for causality, see test_lag_counted_in_each_assets_own_calendar) means the
    book held on a date is assembled from decisions taken on several different
    dates. A cap applied only to the decision panel therefore does not cap the
    book: over a weekend the equity leg is frozen at Friday's size while the
    7-day asset keeps re-sizing on top of it. On the real universe this reached
    155% gross on 7% of days before it was fixed.
    """
    import config
    from backtest import Backtester
    cal = pd.date_range("2020-01-01", periods=200, freq="D")     # 7-day asset
    bdays = pd.bdate_range("2020-01-01", periods=200)            # 5-day asset
    rng = np.random.default_rng(7)

    def frame(index):
        p = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.02, len(index))), index=index)
        return pd.DataFrame({"close": p, "open": p, "high": p, "low": p, "volume": 1.0})

    data = {"BTCUSDT": frame(cal), "AAPL": frame(bdays), "SPY": frame(bdays)}
    bt = Backtester(data, start="2020-02-01")
    cols = ["BTCUSDT", "AAPL", "SPY"]

    # each sleeve alone wants full gross; the allocator must never let the
    # union of them exceed the cap on any date, weekend or not
    full = pd.DataFrame(1.0, index=cal, columns=cols)
    res = bt.run({"crypto": full[["BTCUSDT"]], "equity": full[["AAPL", "SPY"]]})

    gross = res["held"].abs().sum(axis=1)
    assert (gross <= config.GROSS_CAP + 1e-9).all(), (
        f"held gross reached {gross.max():.4f} against a cap of {config.GROSS_CAP}")
    assert (res["gross_exposure"] <= config.GROSS_CAP + 1e-9).all()
    # and the weekends specifically, since that is where it broke
    wknd = gross[gross.index.dayofweek >= 5]
    assert len(wknd) > 0 and (wknd <= config.GROSS_CAP + 1e-9).all()
