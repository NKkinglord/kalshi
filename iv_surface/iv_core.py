"""Pull an options chain and derive implied volatility from it.

Nothing here plots. iv_visualizer.py turns the output into a page.

Method, in the order the code runs:
  1. fetch_chain      every listed expiry from yfinance, plus spot and a risk-free rate
  2. compute_iv       pick a price per contract, infer each expiry's forward from
                      put-call parity, invert Black-76 for the vol
  3. term_structure   per-expiry ATM vol and ATM straddle cost
  4. save_snapshot    write the table and a summary to data/snapshots/<TICKER>/
"""
from __future__ import annotations

import json
import pathlib
import time
from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd
from scipy.special import ndtr

ROOT = pathlib.Path(__file__).resolve().parent
SNAPSHOT_DIR = ROOT / "data" / "snapshots"
NY = "America/New_York"
YEAR_SECONDS = 365.0 * 24 * 3600

# Quote filters. All are arguments of compute_iv, these are the defaults.
MAX_REL_SPREAD = 0.5      # drop quotes whose bid-ask spread exceeds 50% of the mid
MIN_DAYS = 0.5            # drop expiries closer than half a day (vol is not meaningful there)
PARITY_STRIKES = 5        # strikes nearest spot used to infer the forward
MAX_FORWARD_GAP = 0.05    # distrust a parity forward more than 5% away from spot*exp(rT)
CONSTANT_MATURITIES = (30, 60, 90)


@dataclass
class Market:
    ticker: str
    asof: pd.Timestamp        # UTC, the time the spot price refers to
    spot: float
    rate: float               # continuously compounded, annual
    earnings: str | None = None   # next earnings date, ISO, if Yahoo has one
    source: str = "Yahoo Finance chain"   # where the option quotes came from, printed under the pictures


# ----------------------------------------------------------------------------
# Black-76 (options on a forward) and its inverse
# ----------------------------------------------------------------------------

def black76(is_call, forward, strike, years, discount, vol):
    """European option price on a forward. Works on arrays."""
    sd = vol * np.sqrt(years)
    d1 = (np.log(forward / strike) + 0.5 * sd ** 2) / sd
    d2 = d1 - sd
    call = discount * (forward * ndtr(d1) - strike * ndtr(d2))
    put = discount * (strike * ndtr(-d2) - forward * ndtr(-d1))
    return np.where(is_call, call, put)


def implied_vol(price, is_call, forward, strike, years, discount, lo=1e-4, hi=8.0, iters=60):
    """Vectorised bisection. Returns NaN where the price admits no vol in [lo, hi].

    Bisection rather than Newton because the price is monotone in vol, so it
    cannot diverge on deep out-of-the-money options where vega is near zero.
    """
    price, is_call, forward, strike, years, discount = np.broadcast_arrays(
        *(np.asarray(a, dtype=float) for a in (price, is_call, forward, strike, years, discount)))
    is_call = is_call.astype(bool)
    ok = np.isfinite(price) & np.isfinite(forward) & (years > 0) & (price > 0)
    safe = lambda a, fill: np.where(ok, a, fill)
    f, k, t, d = safe(forward, 100.0), safe(strike, 100.0), safe(years, 1.0), safe(discount, 1.0)
    p = safe(price, 1.0)
    with np.errstate(all="ignore"):
        ok &= (p > black76(is_call, f, k, t, d, lo)) & (p < black76(is_call, f, k, t, d, hi))
        low = np.full(p.shape, lo)
        high = np.full(p.shape, hi)
        for _ in range(iters):
            mid = 0.5 * (low + high)
            above = black76(is_call, f, k, t, d, mid) > p
            high = np.where(above, mid, high)
            low = np.where(above, low, mid)
    return np.where(ok, 0.5 * (low + high), np.nan)


def forward_delta(is_call, forward, strike, years, vol):
    sd = vol * np.sqrt(years)
    d1 = (np.log(forward / strike) + 0.5 * sd ** 2) / sd
    return np.where(is_call, ndtr(d1), ndtr(d1) - 1.0)


# ----------------------------------------------------------------------------
# Fetch
# ----------------------------------------------------------------------------

def fetch_chain(ticker: str, pause: float = 0.15) -> tuple[pd.DataFrame, Market]:
    """Every listed expiry for `ticker`, calls and puts stacked, plus market context."""
    import yfinance as yf

    t = yf.Ticker(ticker)

    # Spot and its timestamp come from the same 1-minute bar. Outside market
    # hours this is the last regular-session bar, which is also when the option
    # quotes stopped updating, so time-to-expiry is measured from the right moment.
    bars = t.history(period="5d", interval="1m", prepost=False)["Close"].dropna()
    if len(bars):
        spot = float(bars.iloc[-1])
        asof = (bars.index[-1] + pd.Timedelta(minutes=1)).tz_convert("UTC")
    else:
        daily = t.history(period="5d")["Close"].dropna()
        spot = float(daily.iloc[-1])
        asof = (daily.index[-1].tz_convert(NY).normalize() + pd.Timedelta(hours=16)).tz_convert("UTC")

    try:  # 13-week T-bill yield, quoted in percent
        rate = float(yf.Ticker("^IRX").history(period="5d")["Close"].dropna().iloc[-1]) / 100.0
    except Exception:
        rate = 0.04

    earnings = None
    try:
        dates = (t.calendar or {}).get("Earnings Date") or []
        future = sorted(d for d in dates if pd.Timestamp(d) >= asof.tz_convert(NY).tz_localize(None).normalize())
        earnings = str(pd.Timestamp(future[0]).date()) if future else None
    except Exception:
        pass

    frames = []
    for expiry in t.options:
        chain = t.option_chain(expiry)
        for kind, df in (("call", chain.calls), ("put", chain.puts)):
            df = df.copy()
            df["kind"] = kind
            df["expiry"] = expiry
            frames.append(df)
        time.sleep(pause)
    if not frames:
        raise RuntimeError(f"Yahoo returned no option expiries for {ticker}")

    raw = pd.concat(frames, ignore_index=True)
    keep = ["contractSymbol", "expiry", "kind", "strike", "bid", "ask", "lastPrice",
            "lastTradeDate", "volume", "openInterest", "impliedVolatility"]
    raw = raw[keep].rename(columns={"impliedVolatility": "yahoo_iv"})
    return raw, Market(ticker.upper(), asof, spot, rate, earnings)


# ----------------------------------------------------------------------------
# Chain -> implied vols
# ----------------------------------------------------------------------------

def compute_iv(raw: pd.DataFrame, mkt: Market, max_rel_spread: float = MAX_REL_SPREAD,
               min_days: float = MIN_DAYS) -> pd.DataFrame:
    """Add price, forward, iv and friends to the raw chain. One row per contract."""
    df = raw.copy()
    for col in ("bid", "ask", "lastPrice", "strike"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df[["bid", "ask"]] = df[["bid", "ask"]].fillna(0.0)

    # Expiry is the 4pm New York close on the expiry date.
    expiry_ts = (pd.to_datetime(df["expiry"]).dt.tz_localize(NY) + pd.Timedelta(hours=16)).dt.tz_convert("UTC")
    df["years"] = (expiry_ts - mkt.asof).dt.total_seconds() / YEAR_SECONDS
    df["dte"] = df["years"] * 365.0
    df = df[df["dte"] >= min_days].copy()

    # Price per contract: the bid-ask mid when there is a usable two-sided quote.
    # Yahoo blanks bid and ask to zero outside market hours; only then fall back
    # to the last trade, and only if it printed in the session `asof` belongs to.
    mid = 0.5 * (df["bid"] + df["ask"])
    quoted = (df["bid"] > 0) & (df["ask"] >= df["bid"]) & ((df["ask"] - df["bid"]) <= max_rel_spread * mid)
    last_ts = pd.to_datetime(df["lastTradeDate"], utc=True, errors="coerce")
    same_session = last_ts.dt.tz_convert(NY).dt.date == mkt.asof.tz_convert(NY).date()
    no_quote = (df["bid"] == 0) & (df["ask"] == 0)
    traded = no_quote & same_session & (df["lastPrice"] > 0)
    df["price"] = np.where(quoted, mid, np.where(traded, df["lastPrice"], np.nan))
    df["price_source"] = np.where(quoted, "mid", np.where(traded, "last", "none"))

    df["discount"] = np.exp(-mkt.rate * df["years"])

    # Forward per expiry from put-call parity, C - P = D * (F - K), at the strikes
    # nearest spot. This absorbs dividends and borrow cost, and it means the vols
    # do not depend on the (possibly out-of-sync) spot print at all.
    forwards, sources = {}, {}
    for expiry, grp in df.groupby("expiry"):
        years = grp["years"].iloc[0]
        carry = mkt.spot * np.exp(mkt.rate * years)
        wide = grp.pivot_table(index="strike", columns="kind", values="price").dropna()
        fwd, src = carry, "carry"
        if {"call", "put"} <= set(wide.columns) and len(wide):
            near = wide.loc[(wide.index.to_series() - mkt.spot).abs().sort_values().index[:PARITY_STRIKES]]
            implied = float(np.median(near.index + np.exp(mkt.rate * years) * (near["call"] - near["put"])))
            if abs(implied / carry - 1.0) <= MAX_FORWARD_GAP:
                fwd, src = implied, "parity"
        forwards[expiry], sources[expiry] = fwd, src
    df["forward"] = df["expiry"].map(forwards)
    df["forward_source"] = df["expiry"].map(sources)

    is_call = (df["kind"] == "call").to_numpy()
    df["iv"] = implied_vol(df["price"], is_call, df["forward"], df["strike"], df["years"], df["discount"])
    with np.errstate(all="ignore"):
        df["delta"] = forward_delta(is_call, df["forward"], df["strike"], df["years"], df["iv"])
    df["log_moneyness"] = np.log(df["strike"] / df["forward"])
    df["moneyness"] = df["strike"] / mkt.spot

    # The surface uses out-of-the-money options only: puts below the forward,
    # calls at and above it. They are the liquid side, and the early-exercise
    # premium of these American options is smallest there.
    df["otm"] = np.where(is_call, df["strike"] >= df["forward"], df["strike"] < df["forward"])
    return df.sort_values(["years", "strike", "kind"]).reset_index(drop=True)


def smile(table: pd.DataFrame, expiry: str) -> pd.DataFrame:
    """Out-of-the-money vols for one expiry, sorted by strike."""
    s = table[(table["expiry"] == expiry) & table["otm"] & table["iv"].notna()]
    return s.sort_values("strike")


def term_structure(table: pd.DataFrame, mkt: Market) -> pd.DataFrame:
    """Per expiry: ATM vol (interpolated at the forward) and the ATM straddle."""
    rows = []
    for expiry, grp in table.groupby("expiry", sort=False):
        s = smile(table, expiry)
        fwd, years = grp["forward"].iloc[0], grp["years"].iloc[0]
        k = s["log_moneyness"].to_numpy()
        atm_iv = float(np.interp(0.0, k, s["iv"])) if len(s) >= 2 and k.min() <= 0 <= k.max() else np.nan

        row = dict(expiry=expiry, dte=years * 365.0, years=years, forward=fwd, atm_iv=atm_iv,
                   n_quotes=int(len(s)), forward_source=grp["forward_source"].iloc[0])
        both = grp.pivot_table(index="strike", columns="kind", values="price").dropna()
        if {"call", "put"} <= set(both.columns) and len(both):
            strike = float((both.index.to_series() - fwd).abs().idxmin())
            call, put = float(both.loc[strike, "call"]), float(both.loc[strike, "put"])
            cost = call + put
            row.update(atm_strike=strike, call=call, put=put, straddle=cost,
                       move_pct=cost / mkt.spot, be_low=strike - cost, be_high=strike + cost)
        rows.append(row)
    cols = ["expiry", "dte", "years", "forward", "atm_iv", "atm_strike", "call", "put",
            "straddle", "move_pct", "be_low", "be_high", "n_quotes", "forward_source"]
    return pd.DataFrame(rows).reindex(columns=cols).sort_values("years").reset_index(drop=True)


def straddles(table: pd.DataFrame, mkt: Market, expiries, strikes=None, moneyness=None) -> pd.DataFrame:
    """Straddle prices at chosen expiries and strikes. One row per (expiry, strike).

    Strikes are dollars (`strikes`) or fractions of spot (`moneyness`); each is
    snapped to the nearest listed strike of that expiry (`target` keeps the value
    asked for, so snapshots with different spots line up). Prices: `straddle` is the
    sum of the call and put mids, `buy` the sum of asks, `sell` the sum of bids.
    NaN where either leg has no usable quote.
    """
    if (strikes is None) == (moneyness is None):
        raise ValueError("give exactly one of strikes or moneyness")
    targets = list(strikes) if strikes is not None else list(moneyness)
    wanted = targets if strikes is not None else [m * mkt.spot for m in moneyness]
    rows = []
    for expiry in expiries:
        grp = table[table["expiry"] == expiry]
        if grp.empty:
            raise ValueError(f"{mkt.ticker} has no {expiry} expiry in the snapshot at {mkt.asof}")
        wide = grp.pivot_table(index="strike", columns="kind", values=["bid", "ask", "price"])
        for target, k in zip(targets, wanted):
            s = float(wide.index[np.abs(wide.index.to_numpy(dtype=float) - k).argmin()])
            leg = lambda col, kind: float(wide.loc[s, (col, kind)]) if (col, kind) in wide.columns else np.nan
            call, put = leg("price", "call"), leg("price", "put")
            cost = call + put
            rows.append(dict(asof=mkt.asof, spot=mkt.spot, expiry=expiry, target=target,
                             dte=float(grp["dte"].iloc[0]), strike=s, moneyness=s / mkt.spot,
                             call=call, put=put, straddle=cost,
                             buy=leg("ask", "call") + leg("ask", "put"),
                             sell=leg("bid", "call") + leg("bid", "put"),
                             move_pct=cost / mkt.spot, be_low=s - cost, be_high=s + cost))
    return pd.DataFrame(rows)


def constant_maturity(term: pd.DataFrame, days=CONSTANT_MATURITIES) -> dict[int, float | None]:
    """ATM vol at fixed horizons, interpolating total variance (iv^2 * T) linearly in T.

    Listed expiries roll closer every day, so a fixed horizon is what makes one
    day's ATM vol comparable with the next. No extrapolation: None outside the range.
    """
    t = term.dropna(subset=["atm_iv"])
    out = {}
    for d in days:
        target = d / 365.0
        if len(t) >= 2 and t["years"].min() <= target <= t["years"].max():
            w = np.interp(target, t["years"], t["atm_iv"] ** 2 * t["years"])
            out[d] = float(np.sqrt(w / target))
        else:
            out[d] = None
    return out


# ----------------------------------------------------------------------------
# Snapshots
# ----------------------------------------------------------------------------

def _stamp(asof: pd.Timestamp) -> str:
    return asof.strftime("%Y%m%dT%H%M%SZ")


def save_snapshot(table: pd.DataFrame, term: pd.DataFrame, mkt: Market) -> pathlib.Path:
    """Write <stamp>.csv.gz (full table) and <stamp>.json (summary). Same asof overwrites."""
    folder = SNAPSHOT_DIR / mkt.ticker
    folder.mkdir(parents=True, exist_ok=True)
    base = folder / _stamp(mkt.asof)
    table.to_csv(base.with_suffix(".csv.gz"), index=False)
    priced = table["iv"].notna()
    meta = asdict(mkt) | dict(
        asof=mkt.asof.isoformat(),
        n_contracts=int(len(table)),
        n_priced=int(priced.sum()),
        share_last_trade=float((table.loc[priced, "price_source"] == "last").mean()) if priced.any() else None,
        constant_maturity={str(k): v for k, v in constant_maturity(term).items()},
        term=json.loads(term.to_json(orient="records")),
    )
    base.with_suffix(".json").write_text(json.dumps(meta, indent=1))
    return base.with_suffix(".csv.gz")


def load_snapshot(path: str | pathlib.Path) -> tuple[pd.DataFrame, Market]:
    path = pathlib.Path(path)
    meta = json.loads(path.with_name(path.name.replace(".csv.gz", ".json")).read_text())
    table = pd.read_csv(path, dtype={"expiry": str})
    mkt = Market(meta["ticker"], pd.Timestamp(meta["asof"]), meta["spot"], meta["rate"], meta.get("earnings"),
                 meta.get("source", "Yahoo Finance chain"))
    return table, mkt


def list_snapshots(ticker: str) -> list[dict]:
    """Summaries of every saved snapshot for a ticker, oldest first."""
    folder = SNAPSHOT_DIR / ticker.upper()
    metas = [json.loads(p.read_text()) | {"path": str(p).replace(".json", ".csv.gz")}
             for p in sorted(folder.glob("*.json"))]
    return sorted(metas, key=lambda m: m["asof"])
