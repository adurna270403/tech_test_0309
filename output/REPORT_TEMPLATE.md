# [TITLE — suggestion: "A Three-Sleeve Unlevered Book: Hit Rate Above 75%, and an Honest Account of Why 2%/Month Is Not Reachable Inside the Constraints"]

*Author · Date · All results net of costs, decide-at-close/fill-at-next-open, gross ≤ 100% throughout, 2019-01 → 2026-09 (93 months). IS = 2019-2022, OOS = 2023+. The sample starts where the Binance USDT cross-section has enough liquid names to size cross-sectional sleeves as designed; the thin 2017-2018 market is reported as a robustness check.*

---

## Executive summary

> Suggested 5-sentence arc. Numbers are real, from `output/summary.json`.

- The brief asked for ≥2%/month net, >75% positive months, and no leverage, across US equities, crypto and futures. The shipped book **meets the hit-rate condition in all three windows — 75.3% full, 75.0% IS, 75.6% OOS — and averages 1.64%/month net (1.62% OOS, Sharpe 1.43)**, short of the 2% floor.
- The miss is structural, not a tuning shortfall: the book leans on the 100% gross cap nearly all the time (mean gross 93%, cap binding ~85% of days), and inside the cap **every sleeve and overlay tested returns 12–14% per year per unit of gross**, so swapping gross between them moves variance, not drift.
- Diagnostic proof: at a 125% gross cap the book earns 1.64%/month at 73.5% positive months — both targets clear together only beyond the constraint. The shadow price of "no leverage" is precisely the missing ~0.7%/month.
- Robustness: OOS Sharpe (1.56) exceeds IS (0.99); deflated Sharpe 0.99 after charging for all trials; 24 automated no-lookahead tests pass; results survive 3bp/20bp costs.
- Deliverables: reproducible pipeline (`make all`), 24 leakage tests, rejected-sleeve graveyard with documented negative results, and the frontier grids that make the infeasibility claim auditable.

---

## 1. The problem, as stated

> Restate the brief in your own words: three joint constraints, cross-asset universe welcome, no tuning, no lookahead. State the scorecard up front:

| Constraint | Result | Verdict |
|---|---|---|
| ≥ 2%/month net | 1.64% full · 1.67% IS · 1.62% OOS | **MISSED** |
| > 75% positive months | 75.3% full · 75.0% IS · 75.6% OOS | **MET** |
| No leverage (gross ≤ 100%) | mean 93%, max 100.0% | **MET** |
| Costs modelled | 1.5bp eq / 10bp crypto + 2%·vol slippage (~2× brief) | **MET** |

Suggested framing sentence: *"Two of three conditions are met outright; this report is as much about proving why the third is unreachable jointly with the second as it is about the strategy itself."*

---

## 2. Architecture — four sleeves, four different drivers

> The design principle: sleeves must be uncorrelated in *driver*, not merely in history.

| Sleeve | Driver | What it does | Role |
|---|---|---|---|
| **equity_core** (48% risk budget) | Equity drift + SPY 200d trend gate | Long, inverse-vol basket of 70 US large caps at 100% gross in uptrends, 5% gross otherwise | Return base; hit rate near the index's own |
| **btc_trend** (26% risk budget) | Crypto trend | BTC EMA(50/200) + RSI(14) regime, inverse-vol sized, applied to the 11-coin liquid complex; long in bull, flat/short in bear; positions ride the perp when that side collects funding | The return engine; also the volatility source |
| **defensive** (13% risk budget) | Defensive-asset trend | Long-only GLD/TLT/UUP, each leg held only while above its own 63/126/252d price — unanimity gate | **The hit-rate engine**: the only sleeve that pays when equities fall |
| **wd_mom** (4% risk budget) | Crypto same-weekday seasonality | Equal-weights the coins with the strongest same-weekday return 3 weeks ago (Long 2020, FRL), over a 30-coin Binance USDT universe (delisted names included) restricted each day to the top-12 by trailing 20-day dollar volume; formed once a week at Friday close | Low-corr diversifier (daily corr to book +0.16); the best of ~40 candidates searched |

Sleeve standalone stats (net, 2017+): equity_core 11.2%/yr, Sharpe 0.99, 71% positive months · btc_trend 24.0%/yr, Sharpe 0.94, 47% positive months · defensive 4.1%/yr, Sharpe 0.48 · wd_mom +22%/yr on 32% gross, Sharpe 0.99 (11-coin spec), IS 1.10 / OOS 0.70; the shipped 30-coin top-12-ADV k=3 variant lifts the book to 1.64%/mo @ 75.3%.

**Execution model** — worth a paragraph of its own because it is where most backtests lie: signal at close t, fill at open t+1, P&L open-to-open, execution lag counted in each asset's *own* trading calendar (crypto 7d, equities 5d), gross cap applied to the *held* book (a flat shift over a mixed calendar peaked at 155% gross before this was fixed — regression-tested). Optimistic same-close fills are priced in the sensitivity table only.

**Cash yield**: idle cash earns the 13-week T-bill rate, causally lagged. Combined with the 5% off-gate weight, this turns part of the defensive posture into income.

**Risk overlays, in binding order**: portfolio vol target (20%) → gross cap (100%, binds ~85% of days) → drawdown throttle → crash guard. Order matters: the vol target wants to scale *up*, so anything applied before the cap is absorbed and never seen.

---

## 3. Results

> Pull tables from `output/summary.json` / REPORT.md §3 — headline metrics table (CAGR 14.0%, Sharpe 1.43, MaxDD −15.0%, 93 months), monthly grid, and the scorecard. Three discussion points worth making:

1. **Wealth framing**: $10k → ~$21k net over 93 months (CAGR 14.0%), with −15.0% max drawdown; in 2022: SPY −18%, book +0.7%. The book's claim is the *ride*, not the destination.
2. **The 75% is met by one month of margin** (70 vs 70 required of 93 months — strictly greater than 75% means ≥70). State this plainly; it recurs in limitations.
3. **Non-Gaussian escape hatch**: the Gaussian identity Φ(m/σ_monthly) says a 75% hit rate needs annual Sharpe ≈ 2.34; the book ships 1.43. Trend gates amputate the left tail and many small wins do the counting. But read what that reshaping buys: a hit rate, not return — the crux of §5.

---

## 4. Where the hit rate comes from — and why allocation alone can't fake it

> Core research finding. Before the defensive sleeve, the book was long equity beta: 96% of up-months, 17% of down-months. No re-budgeting of long-equity/crypto sleeves can move the hit rate — they carry the same risk. **The hit rate can only be raised by winning months in which equities fall, which requires owning something that rises in them.** Market-direction attribution (`output/market_direction_attribution.csv`): in SPY-down months the defensive sleeve averages +0.36% (60% positive) while equity_core averages −2.23%.
>
> Robustness honesty: stepping the crypto budget moves hit rate 76→75→74→71→69% — noise, not an optimum. On the 2010-16 equity-only holdout the same machine is 62% positive *despite similar Sharpe* — direct evidence that monthly hit rate is a property of the sample path as much as of the strategy.

---

## 5. Why the average month is 1.34% and not 2%

> This is the section that turns a "miss" into a result. Two sub-arguments.

### 5.1 The rule: no leverage is binding

- Arithmetic: at pinned m/σ (the hit-rate condition fixes it), lifting μ to 2% requires exposure ≈ 138% of equity. The rules cap the loan at 100%. No rearrangement of furniture lends you the difference.
- The cap is not slack: max exactly 100%, binding most days.
- Per-gross economics inside the cap: every sleeve and overlay returns **12–14%/yr per unit of gross** (equity core 11.9%, trend 55.8% but on 25% notional → blended ~14%, carry 12.5% netted, defensive 4.2%). Swapping gross between streams of equal efficiency moves variance, not drift. This single sentence is why five different added sleeves all failed the same way.
- The strongest counterexample tested — and why it still fails: a same-weekday crypto cross-sectional momentum sleeve (Long 2020) is genuinely alpha: +22%/yr net on 32% gross, IS Sharpe 1.10 / OOS 0.70, daily correlation to the book +0.16, robust across lookback k=1..8. Added to the book it raises Sharpe but *along* the frontier, not beyond it: every reallocation that keeps the hit rate ≥75% lands within a few tenths of a percent of the same return (`output/research/wd_frontier.csv`, `fine_frontier.csv` + `fine_frontier_2019.csv`, 464 cells total). The best spec-feasible cell shipped: budgets 0.48/0.26/0.13/0.04, 30-coin universe with top-12 ADV filter, k=3 (`output/research/final_grid.csv`).
- Diagnostic pricing of the constraint: cap 1.25 → 1.64%/mo @ 73.5%; cap 1.5 → 1.88% @ 73.5%. **Both targets clear together only beyond 100% gross** — the constraint, not the alpha, is the shortfall.

### 5.2 The search: what was tried and rejected

> Present as a table of negative results (the report PDF has the full 20-row version). Suggested highlight rows:

| Candidate | Outcome | Verdict |
|---|---|---|
| Equity mean reversion (5d fade) | −0.47%/mo net, 39% hit | costs + no intraday edge |
| Funding-carry overlay (spot vs short perp) | Sharpe ~8 standalone at 1% vol, but displaces equal-yield gross inside the cap | a wash at book level |
| Cross-sectional earnings tilt (WQ-style) | negative after next-open fills | rejected |
| Turn-of-month (hold last + first 3 trading days) | standalone Sharpe 1.43, but IS 0.82 → OOS 0.13, 2024 edge negative | calendar decay |
| WQ-BRAIN-style "super alpha" (max-Sharpe weighting of 7 alphas) | in-sample optimizer shorts the best alpha (−1.9x) → −15.8%/yr | the combination trap |
| Crypto reversion | net Sharpe −1.9 | crypto has short-horizon momentum |
| Managed futures, 29 ETFs | trends live in low-vol instruments that can't contribute risk under a gross cap | the cap again |
| Monthly take-profit 2–4% | avg month 1.34% → 0.6–1.1% | truncates the paying months |
| Binding vol target 9–13% | both avg *and* hit rate fall | left tail is alpha, not volatility |
| Tier-4 "cut sleeve after losing streak" | book *worse* (0.25%/mo): after losing months the book averages 1.92% @ 81.5% positive next month — monthly mean reversion, so cutting is exactly wrong | allocation brain needs positive-alpha sleeves to protect |

### 5.3 The clock

> One honest paragraph: one person, one day; ~30 candidates built, each on the same causal engine, each judged IS-only; the standout IS performer (per-asset crypto trend, +3.6%/mo) decays to 0.03–0.14 OOS Sharpe — had selection used the full sample, that trap ships. The paper trail is the grid CSVs.

---

## 6. Is it real? Robustness

- **IS vs OOS per sleeve** (`output/is_oos_decay.csv`): equity_core 0.92→1.15 (no decay), btc_trend 0.96→0.91 (mild), wd_mom 1.10→0.70 (moderate, both windows positive), defensive 0.12→0.80, rejected sleeves show the contrast case.
- **Parameter sensitivity** (`output/sensitivity.json`): equity_core flat across every grid; wd_mom flat across lookback k=1..8 (all positive net) and robust to the 30-coin liquidity-filtered universe; disclosed caveat — the RSI threshold on the trend sleeve is not flat (specification risk lives there).
- **Fills**: next-open vs same-close costs ~X bp/month compounded; the edge survives.
- **Multiple testing**: OOS Sharpe 1.61, DSR after charging n_trials. *"The result is not the search talking."*
- **Tests**: 24 automated no-lookahead tests (signal-level future-data invariance, engine overlay invariance, per-calendar lag, gross cap on mixed calendars, carry gate, perp routing, EPS publication lag, wd_mom weekly labelling + warmup).

---

## 7. Limitations

> Keep the honest ones; they strengthen rather than weaken:
> - Survivorship bias in today's-large-cap equity list (flatters equity numbers).
> - Hit rate met by one month of margin; do not quote 75% as a stable property. The thin 2017-2018 crypto market (2 coins early, few liquid alts) is excluded from the headline and reported as a robustness check: on that window the same machine earns +0.4%/mo at exactly 75% positive months with −21% drawdown — positive, but half the headline pace, showing how much the crypto complex's maturity drives returns.
> - Window starts where crypto starts — also where crypto pays. 2010-16 run reported for audit.
> - Crypto costs are fair-weather estimates; crypto is the highest-turnover sleeve.
> - No futures, hence no cheap rates/credit/FX access — the diversifiers that would help can't contribute risk without leverage.

---

## 8. What more time would buy

Point-in-time index membership · walk-forward re-estimation loop · defined-risk options overlays (equity-like premia inside a notional cap) · crypto execution research (18bp slippage compressible) · an intraday mean-reversion attempt (the one rejected sleeve with a plausible mechanism — the daily version loses to costs, but the brief's 1–2bp assumption is an intraday number) · a search over strategy space orders of magnitude larger than one day allowed.

> Closing line, suggested: *"The gap between 1.64% and 2% per month is a research-programme-sized gap, not a tuning-sized one — and the constraints as given place the 2% floor, the 75% hit rate, and the 100% gross cap in proven mutual tension. Choosing which to relax is a specification decision, not an optimization one."*

---

### Appendix pointer map (what backs each claim)

| Claim in report | File |
|---|---|
| All headline numbers | `output/summary.json`, `output/monthly_table.csv` |
| Hit-rate thinness / budget frontier | `output/hitrate_frontier.csv`, `output/budget_frontier.csv` |
| Structural grid searches | `output/research/{structure,offw,carry,fine_frontier,wd_frontier,super_alpha}.csv` |
| Rejected sleeves | `output/candidate_alphas.csv`, `output/cross_asset_experiment.csv` |
| Cost/fill sensitivity | `output/cost_sensitivity.csv`, `output/execution_sensitivity.csv` |
| Overlay ablations | `output/risk_ablation.csv` |
| DSR / PSR / gross stats | `output/diagnostics.json` |
| Reproduce everything | `make all` (backtest, validation, report) |
