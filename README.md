An unlevered, cost-aware, daily-rebalanced book of three uncorrelated sleeves:
a trend-gated **US equity core**, a **BTC EMA+RSI trend** sleeve, and a
**trend-gated defensive** sleeve (gold / duration / dollar).

Headline: >75% positive months met (76.1%, 117 months from 2017); the 2-4%
average-month target is not met (1.23%/month) — the report explains the miss
via the no-leverage constraint and the one-day research budget, with the
no-leverage arithmetic in `output/REPORT.pdf` section 5 (regenerate with
`make all`).

## Run everything

```bash
pip install -r requirements.txt
make all        # or: the four commands below
```

Without make:

```bash
python -m pytest tests/ -q     # no-lookahead & engine-correctness tests
python run_backtest.py         # reads frozen snapshot, runs backtest, writes output/
python run_validation.py       # sensitivity grids, DSR, correlation, sleeve frontier
python report.py               # writes output/REPORT.pdf (+ charts, markdown copy)
```

Data: frozen daily OHLCV plus a quarterly EPS panel under `data/snapshot/`
(no network access at any point). `data/loaders.py` only reads that snapshot.
To rebuild it locally (not part of the submission): `python scripts/fetch_data.py`.

## Layout

- `config.py` — all a-priori parameters (signals, sleeve risk budgets, risk overlays, costs, windows).
- `data/loaders.py` — offline snapshot loader (prices + fundamentals) and data fingerprint.
- `data/snapshot/` — frozen parquet panel used by every run.
- `signals/equity_core.py` — SPY-trend-gated long US equity basket (equity drift).
- `signals/btc_trend.py` — BTC EMA/RSI regime applied to the crypto complex (return engine).
- `signals/defensive.py` — trend-gated gold / duration / dollar (hit-rate engine: the only
  sleeve that is positive when equities fall; see section 5 of the report).
- `signals/wq_earnings.py` — WQ-inspired cross-sectional earnings tilt, with publication lag.
  Built, tested, rejected (negative under next-open fills); off by default.
- `signals/` — also retains earlier research sleeves (trend/reversion/xs), unused by default.
- `portfolio/allocator.py` — sleeve risk budgets, vol targeting, drawdown throttle, crash guard, gross cap.
- `costs.py` — commission + vol-scaled slippage per asset class.
- `backtest.py` — engine: signal at close t, fill at open t+1, single-pass cost accounting.
- `validation.py` + `run_validation.py` — deflated Sharpe, sensitivity, correlation, frontier.
- `tests/test_no_lookahead.py` — automated leakage checks, incl. the earnings publication lag.
- `output/` — REPORT.pdf (the deliverable), charts, daily results, monthly series,
  diagnostics, frontier.
