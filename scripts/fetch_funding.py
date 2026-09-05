"""PRIVATE one-shot data fetcher — NOT part of the submission package.

Extends data/snapshot/ with the two series the carry sleeve and the cash-yield
accounting need:

  BTCUSDT-PERP.parquet     synthetic perp price panel: spot prices scaled by a
                           cumulative funding factor, so perp return = spot
                           return - daily funding. Standard backtester P&L on
                           this series IS the funding-adjusted P&L of a perp
                           position.
  ETHUSDT-PERP.parquet     same construction on ETH.
  funding_daily.parquet    daily summed funding rate per symbol (signal input)
  tbill_irx.parquet        13-week T-bill discount rate (^IRX), percent

Usage (local only):
    python scripts/fetch_funding.py
"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
SNAP = ROOT / "data" / "snapshot"

FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
SPOT_URL = "https://api.binance.com/api/v3/klines"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}"

PERP_SYMBOLS = ["BTCUSDT", "ETHUSDT"]
START_MS = int(pd.Timestamp("2019-09-01", tz="UTC").timestamp() * 1000)


def fetch_funding(symbol: str) -> pd.Series:
    rows, start = [], START_MS
    while True:
        r = requests.get(FUNDING_URL, params={
            "symbol": symbol, "startTime": start, "limit": 1000}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        if len(batch) < 1000:
            break
        start = batch[-1]["fundingTime"] + 1
        time.sleep(0.3)
    s = pd.Series(
        {pd.to_datetime(x["fundingTime"], unit="ms", utc=True): float(x["fundingRate"])
         for x in rows})
    s.index = s.index.tz_convert(None).normalize()
    daily = s.groupby(level=0).sum().sort_index()
    daily.index.name = "date"
    return daily.rename(symbol)


def fetch_spot_close(symbol: str) -> pd.Series:
    rows, start = [], int(pd.Timestamp("2019-09-01").timestamp() * 1000)
    end = int(pd.Timestamp.now().timestamp() * 1000)
    while start < end:
        r = requests.get(SPOT_URL, params={
            "symbol": symbol, "interval": "1d", "startTime": start,
            "endTime": end, "limit": 1000}, timeout=30)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        start = batch[-1][0] + 1
        time.sleep(0.3)
    idx = pd.to_datetime([x[0] for x in rows], unit="ms")
    return pd.Series([float(x[4]) for x in rows], index=idx, name=symbol)


def build_perp_panel(symbol: str, funding: pd.Series) -> pd.DataFrame:
    """Synthetic perp OHLCV: spot prices times the cumulative funding factor
    A_t = prod(1 - f), so that daily perp return = spot return - funding."""
    spot = pd.read_parquet(SNAP / f"{symbol}.parquet")
    f = funding.reindex(spot.index).fillna(0.0)
    a = (1.0 - f).cumprod()
    df = pd.DataFrame({
        "open": spot["open"] * a,
        "high": spot["high"] * a,
        "low": spot["low"] * a,
        "close": spot["close"] * a,
        "volume": spot["volume"],
    })
    df.index.name = "date"
    return df


def fetch_irx() -> pd.DataFrame:
    r = requests.get(YAHOO_CHART.format(sym="%5EIRX"),
                     params={"range": "max", "interval": "1d"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    df = pd.DataFrame({"close": close},
                      index=pd.to_datetime(ts, unit="s").normalize())
    df = df.dropna()
    df["open"] = df["high"] = df["low"] = df["close"]
    df["volume"] = 0.0
    df.index.name = "date"
    return df[["open", "high", "low", "close", "volume"]]


def main():
    funding_all = {}
    for sym in PERP_SYMBOLS:
        print(f"fetching {sym} funding ...")
        f = fetch_funding(sym)
        funding_all[sym] = f
        print(f"  {len(f)} days, {f.index[0].date()}..{f.index[-1].date()}, "
              f"mean daily {f.mean():.5f}")
        panel = build_perp_panel(sym, f)
        panel.to_parquet(SNAP / f"{sym}-PERP.parquet")
        print(f"  wrote {sym}-PERP.parquet")
        time.sleep(0.5)

    fund = pd.DataFrame(funding_all)
    fund.index.name = "date"
    fund.to_parquet(SNAP / "funding_daily.parquet")
    print(f"wrote funding_daily.parquet {fund.shape}")

    print("fetching ^IRX ...")
    irx = fetch_irx()
    irx.to_parquet(SNAP / "tbill_irx.parquet")
    print(f"wrote tbill_irx.parquet {len(irx)} rows, {irx.index[0].date()}.."
          f"{irx.index[-1].date()}")


if __name__ == "__main__":
    main()
