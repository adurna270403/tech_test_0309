"""Offline data loaders — read frozen data from data/snapshot/ only.

No network calls. Rebuild the snapshot locally with scripts/fetch_data.py
(kept out of the submission package).

Snapshot contents:
  <SYMBOL>.parquet          daily OHLCV, split/dividend adjusted (equities/ETF)
                            or raw spot (crypto)
  fundamentals_eps.parquet  quarterly TTM net EPS panel (fiscal period end)
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

SNAP = Path(__file__).resolve().parent / "snapshot"

# Index proxy used by the equity trend core.
INDEX_TICKERS = ["SPY"]

EQUITY_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "ADBE", "AMD", "INTC",
    "CSCO", "QCOM", "TXN", "ACN", "INTU", "LRCX", "TSM",
    "GOOGL", "META", "NFLX", "DIS", "CMCSA", "T", "VZ",
    "AMZN", "TSLA", "HD", "MCD", "NKE", "SBUX", "LOW",
    "PG", "KO", "PEP", "WMT", "COST",
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "ABT", "RMD",
    "BRK-B", "JPM", "BAC", "WFC", "GS", "MS", "V", "MA",
    "CAT", "HON", "UNP", "BA", "GE", "RTX", "LMT",
    "XOM", "CVX", "COP", "SLB", "NEE", "DUK", "SO", "LIN", "SHW", "PLD", "AMT",
]

CRYPTO_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "LTCUSDT", "XRPUSDT", "ADAUSDT",
    "LINKUSDT", "DOGEUSDT", "SOLUSDT", "AVAXUSDT", "DOTUSDT",
]

# Cross-asset exposures (rates, credit, commodities, FX, non-US equity, sectors)
# taken through liquid US-listed ETFs. The brief allows futures and counts them
# on notional; an ETF gives the same economic exposure on notional, with more
# reliable free data and no roll convention to choose.
XASSET_TICKERS = [
    "TLT", "IEF", "SHY", "TIP",                    # rates / duration
    "LQD", "HYG",                                  # credit
    "GLD", "SLV", "DBC", "USO", "DBA",             # commodities
    "UUP", "FXE", "FXY",                           # FX
    "EFA", "EEM", "EWJ", "VGK", "VNQ", "IWM", "QQQ",  # non-US / other equity
    "XLU", "XLP", "XLV", "XLK", "XLE", "XLF", "XLI", "XLY", "XLB",  # sectors
]

# The defensive sleeve trades this subset, so unlike the rest of XASSET_TICKERS
# these are loaded by default -- a shipped sleeve cannot depend on an opt-in flag.
DEFENSIVE_TICKERS = ["GLD", "TLT", "UUP"]

# Which cross-asset class each instrument belongs to. Used to spread risk across
# asset classes rather than across tickers, so the nine sector ETFs cannot crowd
# out rates and commodities just by being numerous.
XASSET_CLASS = {
    "TLT": "rates", "IEF": "rates", "SHY": "rates", "TIP": "rates",
    "LQD": "credit", "HYG": "credit",
    "GLD": "metals", "SLV": "metals",
    "DBC": "commod", "USO": "commod", "DBA": "commod",
    "UUP": "fx", "FXE": "fx", "FXY": "fx",
    "EFA": "eq_intl", "EEM": "eq_intl", "EWJ": "eq_intl", "VGK": "eq_intl",
    "VNQ": "reits",
    "IWM": "eq_us", "QQQ": "eq_us",
    "XLU": "sector", "XLP": "sector", "XLV": "sector", "XLK": "sector",
    "XLE": "sector", "XLF": "sector", "XLI": "sector", "XLY": "sector",
    "XLB": "sector",
}


def is_crypto(symbol: str) -> bool:
    return symbol.endswith("USDT")


def is_index(symbol: str) -> bool:
    return symbol in INDEX_TICKERS


def _read_symbol(symbol: str) -> pd.DataFrame:
    path = SNAP / f"{symbol}.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing snapshot {path}. Run scripts/fetch_data.py once locally "
            "to materialize data/snapshot/."
        )
    df = pd.read_parquet(path)
    if not isinstance(df.index, pd.DatetimeIndex):
        if "date" in df.columns:
            df = df.set_index("date")
        df.index = pd.to_datetime(df.index)
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    need = ["open", "high", "low", "close", "volume"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"{symbol}: missing columns {missing}")
    return df[need].astype(float)


def load_equities(tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    tickers = tickers or EQUITY_TICKERS
    return {t: _read_symbol(t) for t in tickers}


def load_indices(tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    tickers = tickers or INDEX_TICKERS
    return {t: _read_symbol(t) for t in tickers}


def load_crypto(symbols: list[str] | None = None) -> dict[str, pd.DataFrame]:
    symbols = symbols or CRYPTO_SYMBOLS
    return {s: _read_symbol(s) for s in symbols}


def load_xassets(tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    tickers = tickers or XASSET_TICKERS
    return {t: _read_symbol(t) for t in tickers}


def load_defensive(tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    tickers = tickers or DEFENSIVE_TICKERS
    return {t: _read_symbol(t) for t in tickers}


def load_all(include_xassets: bool = False) -> dict[str, pd.DataFrame]:
    """Tradable universe: index proxy + US equities + crypto spot + defensives.

    The REST of the cross-asset ETFs are off by default because no shipped
    sleeve trades them: the cross-asset trend book was built, tested and
    rejected (see run_validation.cross_asset_experiment and section 7 of the
    report). They stay in the snapshot so that result stays reproducible.
    """
    out = {}
    out.update(load_indices())
    out.update(load_equities())
    out.update(load_defensive())
    if include_xassets:
        out.update(load_xassets())
    out.update(load_crypto())
    return out


def load_eps() -> pd.DataFrame:
    """Quarterly TTM net EPS, indexed by FISCAL PERIOD END (not report date).

    Callers must apply a publication lag before using these values in a signal;
    see signals/wq_earnings.py.
    """
    path = SNAP / "fundamentals_eps.parquet"
    if not path.exists():
        raise FileNotFoundError(f"Missing {path}; run scripts/fetch_data.py")
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index)
    return df.sort_index().astype(float)


def close_frame(data: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Wide close-price panel (outer join on dates)."""
    return pd.DataFrame({k: v["close"] for k, v in data.items()})


def data_fingerprint(data: dict[str, pd.DataFrame]) -> str:
    """Stable hash of the cached dataset, for reproducibility in the report."""
    h = hashlib.sha256()
    for k in sorted(data):
        h.update(k.encode())
        h.update(str(data[k].index[0]).encode())
        h.update(str(data[k].index[-1]).encode())
        h.update(str(len(data[k])).encode())
        h.update(hashlib.sha256(data[k].round(8).to_csv().encode()).hexdigest().encode())
    return h.hexdigest()[:16]
