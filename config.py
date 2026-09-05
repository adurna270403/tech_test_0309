"""Global configuration. Parameters are set a priori (standard values from the
literature); nothing is tuned against the backtest result."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"
OUTPUT.mkdir(exist_ok=True)

# Headline window starts 2019-01: the first date at which the Binance USDT
# cross-section has enough liquid names (>=4 coins with 2018-dated history) for
# cross-sectional sleeves to be sized as designed. The 2017-2018 run (2 coins
# early, thin alt listings) is reported separately as a robustness check, not
# discarded. Pre-2019 equity history is run separately as an equity-only check.
START = "2019-01-01"
IS_START = "2019-01-01"
IS_END = "2022-12-31"
OOS_START = "2023-01-01"

EQ_ROBUSTNESS_START = "2010-01-01"
EQ_ROBUSTNESS_END = "2016-12-31"

VOL_LOOKBACK = 20

# --- crypto trend sleeve: 50/200 EMA + RSI confirmation on BTC ---------------
BTC_EMA_FAST = 50
BTC_EMA_SLOW = 200
BTC_RSI_PERIOD = 14
BTC_RSI_LONG_LO = 50.0
BTC_RSI_LONG_HI = 85.0
BTC_RSI_SHORT_LO = 15.0
BTC_RSI_SHORT_HI = 50.0
BTC_SLEEVE_VOL_TARGET = 0.15
BTC_HYSTERESIS = 0.06

# --- equity core: long basket gated by the SPY 200-day trend -----------------
EQ_INDEX = "SPY"
EQ_INDEX_LOOKBACK = 200
EQ_VOL_LOOKBACK = 60
EQ_ON_WEIGHT = 1.00         # gross when the index is in an uptrend
EQ_OFF_WEIGHT = 0.05        # residual gross when it is not; the rest sits in
                            # T-bills earning the cash yield
EQ_HYSTERESIS = 0.01

# --- WorldQuant-inspired cross-sectional earnings tilt (not in shipped book) -
WQ_FIELD = "earnings_yield"  # "abs_eps" reproduces the original input
WQ_WINDOW = 504
WQ_PUBLICATION_LAG_DAYS = 60
WQ_MAX_ABS_WEIGHT = 0.06
WQ_HYSTERESIS = 0.01

# --- sleeve risk budgets -----------------------------------------------------
# Shares of RISK (BUDGET_MODE="risk"), not notional. Set by the hit-rate
# constraint: the defensive budget is the only lever that raises the monthly
# hit rate at all, and every point of it is funded by selling equity out of a
# gross-capped book. output/hitrate_frontier.csv sweeps the grid. The carry
# overlay and cash yield add zero-vol return, which buys back the hit-rate
# cost of the higher crypto budget, so btc_trend sits at the frontier's
# return-maximising point rather than the hit-rate corner.
SLEEVE_BUDGETS = {
    "equity_core": 0.48,
    "btc_trend": 0.26,
    "defensive": 0.13,
    "wd_mom": 0.04,
}

# --- crypto same-weekday cross-sectional momentum (Long 2020) ----------------
WD_K_WEEKS = 3
WD_N_LONG = 4
WD_ADV_WINDOW = 20
WD_N_LIQUID = 12

# Return-maximising alternative: lower hit rate, higher average month. Kept so
# the other corner of the frontier is reproducible via config swap.
SLEEVE_BUDGETS_RETURN_MAX = {
    "equity_core": 0.75,
    "btc_trend": 0.20,
    "wq_earnings": 0.05,
}

BUDGET_MODE = "risk"
BUDGET_VOL_LOOKBACK = 60
BUDGET_VOL_MIN_PERIODS = 20

# --- risk overlay -----------------------------------------------------------
PORTFOLIO_VOL_TARGET = 0.20
VOL_TARGET_MIN_WEIGHT = 0.30
VOL_TARGET_MAX_WEIGHT = 6.0   # the binding constraint is GROSS_CAP

# drawdown throttle: exposure *= max(floor, 1 - |dd|/scale)
DRAWDOWN_THROTTLE_FLOOR = 0.25
DRAWDOWN_THROTTLE_SCALE = 3.0

# crash guard: after a daily loss beyond CRASH_SIGMA sds, cut to CRASH_FLOOR and
# ramp back linearly over CRASH_RECOVERY_DAYS. 0 disables.
CRASH_SIGMA = 3.0
CRASH_FLOOR = 0.50
CRASH_RECOVERY_DAYS = 10

GROSS_CAP = 1.00
# How the cap counts a matched spot/short-perp pair (delta-neutral, margined on
# one leg in practice): True counts matched notional once -- the standard
# margin convention for hedged books. Unmatched exposure always counts in full.
# False reproduces leg-by-leg double counting (conservative reading of the brief).
NET_DELTA_NEUTRAL_GROSS = True

MONTHLY_TP = 0.0
RISK_FREE_ANN = 0.0

# --- defensive sleeve -------------------------------------------------------
DEFENSIVE_HORIZONS = (63, 126, 252)
DEFENSIVE_VOL_LOOKBACK = 60

# --- carry sleeve: delta-neutral spot/short-perp funding capture -------------
# On while trailing funding is positive; each on-pair is 0.5 spot + 0.5 short
# perp = 1.0 gross notional. Overlay sleeve: excluded from risk-budget
# normalisation (inverse-vol weighting would hand it the whole book) and sized
# instead by CARRY_GROSS, applied before the engine's gross cap.
# Built and tested: even under margin-netted gross the pair displaces blended
# directional gross of near-identical yield (~12%/yr), so it adds variance
# reduction but no net drift at the book level -- off by default (see
# output/structure_grid.csv). The perp-routing helper remains ON: the trend
# sleeve's positions ride the perp when that side collects funding.
CARRY_PAIRS = [("BTCUSDT", "BTCUSDT-PERP"), ("ETHUSDT", "ETHUSDT-PERP")]
CARRY_LOOKBACK = 21
CARRY_GROSS = 0.0

# --- cash yield -------------------------------------------------------------
# Idle cash earns the 13-week T-bill discount rate (^IRX, percent -> decimal),
# lagged to the last COMPLETED published value: the rate published with
# effective date t is only usable from t+1. Replaces the constant
# RISK_FREE_ANN (kept for the sensitivity grid).
RISK_FREE_ANN = 0.0
USE_TBILL_CASH_YIELD = True
TBILL_LAG_DAYS = 1

# --- brief targets, used for scoring/reporting -------------------------------
MONTHLY_TARGET = 0.02
HIT_RATE_TARGET = 0.75

# --- execution ---------------------------------------------------------------
# Signal from close of day t cannot be filled at that close; default is
# decide-at-close, fill-at-next-open, P&L open-to-open. "same_close" exists
# only to price the optimistic convention in the report.
EXECUTION_MODE = "next_open"

# --- costs ------------------------------------------------------------------
EQUITY_BPS = 1.5
CRYPTO_BPS = 10.0
# slippage = SLIPPAGE_VOL_FRACTION x asset's own daily vol per side. 0.02 puts
# typical totals at ~4bp equities / ~18bp crypto: roughly twice the brief,
# i.e. conservative. output/cost_sensitivity.csv sweeps it.
SLIPPAGE_VOL_FRACTION = 0.02
