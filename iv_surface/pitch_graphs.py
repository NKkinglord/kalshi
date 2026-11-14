"""Two pitch graphs for one straddle, drawn from a saved snapshot.

    python pitch_graphs.py --at "2026-11-09 10:46" --expiry 2027-01-15
    python pitch_graphs.py --at "2026-11-09 10:46" --expiry 2027-01-15 --strike 180 --years 2

Writes to output/:
    <TICKER>_straddle_payoff_<expiry>_<date>.png       profit and loss against the stock price
    <TICKER>_implied_vs_realised_<expiry>_<date>.png   the vol we pay against the vol the stock has delivered

The straddle's price and implied vol come from the snapshot (see vol_surface_png.py --list).
The stock's price history comes from Yahoo Finance and needs an internet connection.
"""
from __future__ import annotations

import argparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import iv_core as core
from vol_surface_png import OUTPUT_DIR, snapshot_at

INK, INK_2, GRID = "#0b0b0b", "#52514e", "#d9d8d2"
BLUE, ORANGE, RED, GREY = "#2a78d6", "#eb6834", "#d03b3b", "#8a8983"
TRADING_DAYS = 252


def trading_days_between(start, end) -> int:
    """Trading days after `start` up to and including `end`, skipping weekends and US market holidays."""
    from pandas.tseries.holiday import (AbstractHolidayCalendar, GoodFriday, Holiday, USLaborDay,
                                        USMartinLutherKingJr, USMemorialDay, USPresidentsDay, USThanksgivingDay,
                                        nearest_workday)

    class Market(AbstractHolidayCalendar):
        rules = [Holiday("New Year", month=1, day=1, observance=nearest_workday), USMartinLutherKingJr,
                 USPresidentsDay, GoodFriday, USMemorialDay,
                 Holiday("Juneteenth", month=6, day=19, observance=nearest_workday),
                 Holiday("Independence Day", month=7, day=4, observance=nearest_workday), USLaborDay,
                 USThanksgivingDay, Holiday("Christmas", month=12, day=25, observance=nearest_workday)]

    start, end = pd.Timestamp(start), pd.Timestamp(end)
    closed = Market().holidays(start, end).values.astype("datetime64[D]")
    return int(np.busday_count(start.date(), end.date(), holidays=closed))


def style(ax):
    ax.grid(color=GRID, lw=0.8)
    ax.set_axisbelow(True)
    ax.tick_params(colors=INK_2, length=0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)


def pick_straddle(table: pd.DataFrame, mkt: core.Market, expiry: str, strike: float | None) -> dict:
    """Call and put mid at one strike of one expiry, plus that expiry's ATM vol and forward."""
    term = core.term_structure(table, mkt).set_index("expiry")
    if expiry not in term.index:
        raise SystemExit(f"{expiry} is not an expiry in this snapshot. Available: {', '.join(term.index)}")
    row = term.loc[expiry]
    legs = table[table["expiry"] == expiry].pivot_table(index="strike", columns="kind", values="price").dropna()
    target = row["atm_strike"] if strike is None else strike
    k = float((legs.index.to_series() - target).abs().idxmin())
    call, put = float(legs.loc[k, "call"]), float(legs.loc[k, "put"])
    return dict(expiry=expiry, strike=k, call=call, put=put, cost=call + put, iv=float(row["atm_iv"]),
                forward=float(row["forward"]), years=float(row["years"]), dte=float(row["dte"]))


def draw_payoff(s: dict, mkt: core.Market, path):
    """Profit per share at expiry, and what the position would be worth if the move came today."""
    k, cost = s["strike"], s["cost"]
    lo, hi = k - cost, k + cost
    x = np.linspace(mkt.spot * 0.6, mkt.spot * 1.4, 400)
    at_expiry = np.abs(x - k) - cost

    # Same move happening now: reprice both legs with the time left and the vol unchanged.
    fwd = s["forward"] * x / mkt.spot
    disc = np.exp(-mkt.rate * s["years"])
    today = (core.black76(True, fwd, k, s["years"], disc, s["iv"])
             + core.black76(False, fwd, k, s["years"], disc, s["iv"]) - cost)

    fig, ax = plt.subplots(figsize=(11, 6.6))
    style(ax)
    ax.axhline(0, color=INK_2, lw=1)
    ax.fill_between(x, at_expiry, 0, where=at_expiry >= 0, color=BLUE, alpha=0.14, lw=0)
    ax.fill_between(x, at_expiry, 0, where=at_expiry <= 0, color=RED, alpha=0.12, lw=0)
    ax.plot(x, at_expiry, color=BLUE, lw=2.2, label=f"Held to expiry ({s['expiry']})")
    ax.plot(x, today, color=INK_2, lw=1.8, ls="--", label=f"If the move happened today (IV unchanged at {s['iv']:.0%})")

    for be, ha, dx in ((lo, "right", -10), (hi, "left", 10)):
        ax.plot(be, 0, "o", color=BLUE, ms=8, mec="white", mew=2, zorder=5)
        ax.annotate(f"break-even \\${be:,.0f}\n({be / mkt.spot - 1:+.0%} from today)", (be, 0), xytext=(dx, -8),
                    textcoords="offset points", ha=ha, va="top", fontsize=10, color=INK)
    ax.plot(k, -cost, "o", color=RED, ms=8, mec="white", mew=2, zorder=5)
    ax.annotate(f"most we can lose: \\${cost:,.2f}\n(stock finishes at \\${k:,.0f})", (k, -cost), xytext=(0, -12),
                textcoords="offset points", ha="center", va="top", fontsize=10, color=INK)
    ax.axvline(mkt.spot, color=GREY, lw=1, ls=":")
    ax.annotate(f"stock today \\${mkt.spot:,.2f}", (mkt.spot, 1), xycoords=("data", "axes fraction"), xytext=(0, 4),
                textcoords="offset points", ha="center", fontsize=9, color=INK_2)
    ax.text(x[6], at_expiry[6] / 2, "profit", color=INK_2, fontsize=10, va="center")
    ax.text(x[-7], at_expiry[-7] / 2, "profit", color=INK_2, fontsize=10, va="center", ha="right")

    ax.set_ylim(-cost * 1.45, max(at_expiry.max(), today.max()) * 1.08)
    ax.set_xlabel(f"{mkt.ticker} stock price", color=INK_2)
    ax.set_ylabel("Profit or loss per share", color=INK_2)
    ax.xaxis.set_major_formatter(lambda v, _: f"\\${v:,.0f}")
    ax.yaxis.set_major_formatter(lambda v, _: f"-\\${-v:,.0f}" if v < 0 else f"\\${v:,.0f}")
    ax.legend(loc="upper center", frameon=False, fontsize=10, labelcolor=INK_2)
    when = mkt.asof.tz_convert(core.NY).strftime("%a %b %d %Y, %H:%M ET")
    fig.suptitle(f"{mkt.ticker} \\${k:,.0f} straddle expiring {s['expiry']}: what we make or lose\n"
                 f"Cost \\${cost:,.2f} per share ({cost / mkt.spot:.1%} of the stock)  ·  priced {when}",
                 x=0.02, ha="left", fontsize=14, color=INK)
    fig.text(0.02, 0.01, f"Call \\${s['call']:.2f} + put \\${s['put']:.2f} at bid-ask mids. The dashed line assumes implied "
             "vol stays where it is; a rise in vol lifts it, a fall lowers it. No trading costs.", fontsize=9, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)


def realised_vol(closes: pd.Series, window: int) -> pd.Series:
    """Annualised standard deviation of daily log returns over a rolling window of trading days."""
    return np.log(closes).diff().rolling(window).std() * np.sqrt(TRADING_DAYS)


def draw_implied_vs_realised(s: dict, mkt: core.Market, closes: pd.Series, earnings: list, path, years: float):
    asof = mkt.asof.tz_convert(core.NY).tz_localize(None).normalize()
    life = trading_days_between(asof, s["expiry"])           # trading days the straddle has left
    rv_life, rv_month = realised_vol(closes, life), realised_vol(closes, 21)
    start = asof - pd.Timedelta(days=round(365 * years))
    rv_life, rv_month = rv_life[start:asof].dropna(), rv_month[start:asof].dropna()
    above = float((rv_life > s["iv"]).mean())

    fig, ax = plt.subplots(figsize=(11, 6.6))
    style(ax)
    for d in earnings:
        if start <= d <= asof:
            ax.axvline(d, color=GREY, lw=1, ls=":")
    ax.plot(rv_month.index, rv_month, color=GREY, lw=1.2, label="Realised vol, last 21 trading days (1 month)")
    ax.plot(rv_life.index, rv_life, color=BLUE, lw=2.2,
            label=f"Realised vol, last {life} trading days (same length as our straddle)")
    ax.axhline(s["iv"], color=ORANGE, lw=2, ls="--", label=f"Implied vol we pay: {s['iv']:.0%}")
    ax.annotate(f"{rv_life.iloc[-1]:.0%}", (rv_life.index[-1], rv_life.iloc[-1]), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=10, color=INK)
    ax.annotate(f"{s['iv']:.0%}", (rv_life.index[-1], s["iv"]), xytext=(6, 0), textcoords="offset points",
                va="center", fontsize=10, color=INK)
    if earnings:
        ax.plot([], [], color=GREY, lw=1, ls=":", label="Earnings reports")

    ax.set_ylim(0, max(rv_month.max(), s["iv"]) * 1.12)
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0%}")
    ax.set_ylabel("Annualised volatility", color=INK_2)
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    ax.margins(x=0.02)
    ax.grid(axis="x", visible=False)                       # keep vertical lines for earnings only
    ax.legend(loc="upper right", frameon=False, fontsize=10, labelcolor=INK_2)
    fig.suptitle(f"{mkt.ticker}: the vol we pay against the vol the stock has actually delivered\n"
                 f"{s['expiry']} straddle at {s['iv']:.0%} implied  ·  {life}-day realised vol beat that on "
                 f"{above:.0%} of days in {years:g} years", x=0.02, ha="left", fontsize=14, color=INK)
    fig.text(0.02, 0.01, "Realised vol = standard deviation of daily log returns x sqrt(252), from Yahoo Finance daily "
             f"closes through {asof:%b %d %Y}. The past does not guarantee the next {life} days.", fontsize=9, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    fig.savefig(path, dpi=200, facecolor="white")
    plt.close(fig)
    return life, float(rv_life.iloc[-1]), float(rv_month.iloc[-1]), above


def fetch_history(ticker: str, asof: pd.Timestamp, years: float):
    import yfinance as yf
    t = yf.Ticker(ticker)
    end = asof.tz_convert(core.NY).tz_localize(None).normalize()
    hist = t.history(start=end - pd.Timedelta(days=round(365 * (years + 1))), end=end + pd.Timedelta(days=1))
    closes = hist["Close"].dropna()                       # adjusted for splits and dividends
    closes.index = closes.index.tz_localize(None).normalize()
    try:
        dates = t.get_earnings_dates(limit=24).index
        earnings = sorted({d.tz_localize(None).normalize() for d in dates})
    except Exception:
        earnings = []
    return closes, earnings


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", default="NVDA")
    ap.add_argument("--at", required=True, help='New York time of a saved snapshot, e.g. "2026-11-09 10:46"')
    ap.add_argument("--expiry", required=True, help="straddle expiry, e.g. 2027-01-15")
    ap.add_argument("--strike", type=float, help="strike in dollars (default: the one nearest the forward)")
    ap.add_argument("--years", type=float, default=2, help="years of price history on the realised-vol graph")
    args = ap.parse_args()

    table, mkt = core.load_snapshot(snapshot_at(args.ticker, args.at))
    s = pick_straddle(table, mkt, args.expiry, args.strike)
    OUTPUT_DIR.mkdir(exist_ok=True)
    tag = f"{args.expiry}_{mkt.asof.tz_convert(core.NY):%Y-%m-%d}"
    payoff = OUTPUT_DIR / f"{mkt.ticker}_straddle_payoff_{tag}.png"
    vols = OUTPUT_DIR / f"{mkt.ticker}_implied_vs_realised_{tag}.png"

    draw_payoff(s, mkt, payoff)
    print(f"{mkt.ticker} ${s['strike']:.0f} straddle {s['expiry']}: cost {s['cost']:.2f}, IV {s['iv']:.1%}, "
          f"break-evens {s['strike'] - s['cost']:.2f} / {s['strike'] + s['cost']:.2f}")
    print(payoff)

    closes, earnings = fetch_history(mkt.ticker, mkt.asof, args.years)
    life, rv_now, rv_month, above = draw_implied_vs_realised(s, mkt, closes, earnings, vols, args.years)
    print(f"Realised vol: {life}-day {rv_now:.1%}, 21-day {rv_month:.1%}; {life}-day was above implied on "
          f"{above:.0%} of days; {len(earnings)} earnings dates found")
    print(vols)


if __name__ == "__main__":
    main()
