"""Rebuild a snapshot for a past minute that nobody ran the tool at.

    python rebuild_snapshot.py --at "2026-11-09 10:46"            # NVDA at that New York time
    python rebuild_snapshot.py AAPL --at "2026-11-09 10:46"
    python rebuild_snapshot.py --at "2026-11-09 10:46" --yes      # do not stop to confirm the cost

Yahoo keeps no past option chains, so the quotes come from Databento instead:
the consolidated best bid and ask of every listed option on the ticker at that
minute (dataset OPRA.PILLAR, schema cbbo-1m). Spot, the 13-week T-bill rate and
the next earnings date still come from Yahoo. The result is saved to
data/snapshots/<TICKER>/ in exactly the form a live run would save, so the
pictures are drawn the usual way:

    python vol_surface_png.py --at "2026-11-09 10:46"

Needs `pip install databento` and DATABENTO_API_KEY in the environment. Only the
data download spends credits. The cost is printed first and the download is
refused above --max-cost unless you pass --yes.

Limits: Yahoo keeps 1-minute spot bars for about 30 days, so older moments need
--spot. The time must fall inside a trading session (9:30 to 16:00 New York).
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

import iv_core as core

DATASET = "OPRA.PILLAR"
SCHEMA = "cbbo-1m"          # consolidated best bid/ask, one record per option per minute
WINDOW_MINUTES = 5          # look back this far for each option's latest quote


# ----------------------------------------------------------------------------
# OPRA symbols -> the chain layout iv_core.compute_iv expects
# ----------------------------------------------------------------------------

def parse_osi(symbol: str) -> tuple[str, str, str, float]:
    """Split an OSI option symbol: 'NVDA  261113C00165000' -> ('NVDA', '2026-11-13', 'call', 165.0)."""
    s = symbol.ljust(21)
    root, ymd, cp, strike = s[:6].strip(), s[6:12], s[12].upper(), s[13:21]
    if cp not in ("C", "P") or not (ymd + strike).isdigit():
        raise ValueError(f"not an OSI option symbol: {symbol!r}")
    expiry = f"20{ymd[:2]}-{ymd[2:4]}-{ymd[4:6]}"
    return root, expiry, "call" if cp == "C" else "put", int(strike) / 1000.0


def chain_from_quotes(quotes: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """One row per option, laid out like iv_core.fetch_chain's output.

    `quotes` is a Databento DataFrame (cbbo-1m or cmbp-1): a `symbol` column with
    the OSI symbol and `bid_px_00` / `ask_px_00` prices. Several records per
    option are fine: the last one wins.
    """
    q = quotes.sort_index(kind="stable").groupby("symbol", sort=False).tail(1)
    parsed = pd.DataFrame([parse_osi(s) for s in q["symbol"]], columns=["root", "expiry", "kind", "strike"])
    raw = pd.DataFrame({
        "contractSymbol": q["symbol"].str.replace(" ", "", regex=False).to_numpy(),
        "expiry": parsed["expiry"].to_numpy(),
        "kind": parsed["kind"].to_numpy(),
        "strike": parsed["strike"].to_numpy(),
        "bid": pd.to_numeric(q["bid_px_00"], errors="coerce").to_numpy(),
        "ask": pd.to_numeric(q["ask_px_00"], errors="coerce").to_numpy(),
        "lastPrice": np.nan,          # not pulled: the quote is live, so no last-trade fallback is needed
        "lastTradeDate": pd.NaT,
        "volume": np.nan,
        "openInterest": np.nan,
        "yahoo_iv": np.nan,
    })
    raw = raw[parsed["root"].to_numpy() == ticker.upper()]
    return raw.sort_values(["expiry", "strike", "kind"]).reset_index(drop=True)


# ----------------------------------------------------------------------------
# Market context at a past minute, from Yahoo
# ----------------------------------------------------------------------------

def to_utc(when: str) -> pd.Timestamp:
    t = pd.Timestamp(when)
    return (t.tz_localize(core.NY) if t.tzinfo is None else t).tz_convert("UTC")


def market_at(ticker: str, asof: pd.Timestamp, spot: float | None = None) -> core.Market:
    """Spot (close of the 1-minute bar ending at `asof`), T-bill rate and earnings date."""
    import yfinance as yf

    t = yf.Ticker(ticker)
    day = asof.tz_convert(core.NY).normalize()
    if spot is None:
        bars = t.history(start=day.tz_localize(None), end=(day + pd.Timedelta(days=1)).tz_localize(None),
                         interval="1m", prepost=False)["Close"].dropna()
        bars = bars[bars.index + pd.Timedelta(minutes=1) <= asof]        # bars whose close is known at asof
        if bars.empty:
            raise SystemExit(f"Yahoo has no {ticker.upper()} 1-minute bar closing at or before "
                             f"{asof.tz_convert(core.NY):%Y-%m-%d %H:%M} ET. Yahoo keeps about 30 days of "
                             "1-minute bars; pick a time inside a trading session or pass --spot.")
        spot = float(bars.iloc[-1])

    rate = 0.04
    try:
        irx = yf.Ticker("^IRX").history(start=(day - pd.Timedelta(days=10)).tz_localize(None),
                                        end=(day + pd.Timedelta(days=1)).tz_localize(None))["Close"].dropna()
        irx = irx[irx.index.tz_convert(core.NY).normalize() <= day]
        if len(irx):
            rate = float(irx.iloc[-1]) / 100.0
    except Exception:
        pass

    earnings = None
    try:  # Yahoo only lists future earnings dates, so this is right only when that date is still ahead of today
        dates = (t.calendar or {}).get("Earnings Date") or []
        future = sorted(d for d in dates if pd.Timestamp(d) >= day.tz_localize(None))
        earnings = str(pd.Timestamp(future[0]).date()) if future else None
    except Exception:
        pass

    return core.Market(ticker.upper(), asof, spot, rate, earnings,
                       source=f"OPRA consolidated quotes via Databento ({SCHEMA})")


# ----------------------------------------------------------------------------
# Databento pull
# ----------------------------------------------------------------------------

def pull_quotes(ticker: str, asof: pd.Timestamp, max_cost: float, yes: bool) -> pd.DataFrame:
    try:
        import databento as db
    except ImportError:
        raise SystemExit("The databento package is not installed in this Python: python -m pip install databento")
    if not os.environ.get("DATABENTO_API_KEY"):
        raise SystemExit("DATABENTO_API_KEY is not set. Export it in this shell (or add it to ~/.zshrc) and retry.")

    client = db.Historical()
    request = dict(dataset=DATASET, symbols=[f"{ticker.upper()}.OPT"], stype_in="parent", schema=SCHEMA,
                   start=(asof - pd.Timedelta(minutes=WINDOW_MINUTES)).isoformat(), end=asof.isoformat())
    cost = float(client.metadata.get_cost(**request))
    print(f"Databento: {DATASET} {SCHEMA} {ticker.upper()}.OPT, {WINDOW_MINUTES} minutes ending "
          f"{asof.tz_convert(core.NY):%Y-%m-%d %H:%M} ET. Cost ${cost:.4f}.")
    if cost > max_cost and not yes:
        raise SystemExit(f"Cost ${cost:.4f} is above --max-cost {max_cost}. Rerun with --yes to pay it, "
                         "or raise --max-cost.")
    quotes = client.timeseries.get_range(**request).to_df()
    if quotes.empty:
        raise SystemExit("Databento returned no quotes for that window. Is the time inside a trading session?")
    return quotes


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", default="NVDA")
    ap.add_argument("--at", required=True, help='New York time of the snapshot, e.g. "2026-11-09 10:46"')
    ap.add_argument("--spot", type=float, default=None, help="stock price at that time, if Yahoo no longer has it")
    ap.add_argument("--max-cost", type=float, default=1.0, help="refuse a Databento pull dearer than this (USD)")
    ap.add_argument("--yes", action="store_true", help="download whatever the cost is")
    args = ap.parse_args()

    asof = to_utc(args.at)
    mkt = market_at(args.ticker, asof, args.spot)
    print(f"{mkt.ticker} at {asof.tz_convert(core.NY):%a %Y-%m-%d %H:%M} ET: spot {mkt.spot:.2f}, "
          f"rate {mkt.rate:.4f}, next earnings {mkt.earnings}")

    quotes = pull_quotes(args.ticker, asof, args.max_cost, args.yes)
    raw = chain_from_quotes(quotes, args.ticker)
    table = core.compute_iv(raw, mkt)
    term = core.term_structure(table, mkt)
    path = core.save_snapshot(table, term, mkt)
    print(f"{len(raw)} contracts, {int(table['iv'].notna().sum())} priced. Saved {path}")
    print(f'Draw it:  python vol_surface_png.py {mkt.ticker} --at "{asof.tz_convert(core.NY):%Y-%m-%d %H:%M}"')


if __name__ == "__main__":
    main()
