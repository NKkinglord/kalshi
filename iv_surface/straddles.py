"""Straddle prices from saved snapshots, at the expiries and strikes you choose.

    python straddles.py --at "2026-11-09 10:46" --at "2026-11-12 10:46" --expiries 2026-11-20 2026-12-18 --strikes 165 180
    python straddles.py --at "2026-11-12 10:46" --expiries 2026-11-20 --moneyness 0.95 1 1.05
    python straddles.py ... --csv output/straddles.csv

Each --at picks the saved snapshot at or just before that New York time (see
`python vol_surface_png.py --list`). Strikes are dollars, or fractions of that
day's spot with --moneyness; each is snapped to the nearest strike listed for
that expiry. For every snapshot, expiry and strike it prints:

    call, put    bid-ask mid of each leg
    straddle     call + put at the mid (fair value)
    buy / sell   what you pay at the asks / receive at the bids
    move         straddle as a percent of spot: the move the market is pricing
    be_low/high  break-evens at expiry, strike -/+ straddle

With two or more snapshots it also prints the change in the straddle (mid)
between consecutive snapshots for every expiry/strike they share. A blank cell
means one leg had no usable quote (no bid, or a spread wider than 50% of mid).
"""
from __future__ import annotations

import argparse

import pandas as pd

import iv_core as core
from vol_surface_png import snapshot_at


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", default="NVDA")
    ap.add_argument("--at", action="append", required=True, help="New York time of a saved snapshot; repeatable")
    ap.add_argument("--expiries", nargs="+", required=True, help="expiry dates, e.g. 2026-11-20 2026-12-18")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--strikes", nargs="+", type=float, help="strikes in dollars")
    group.add_argument("--moneyness", nargs="+", type=float, help="strikes as a fraction of spot, e.g. 0.95 1 1.05")
    ap.add_argument("--csv", help="also write the full table here")
    args = ap.parse_args()

    frames = []
    for when in args.at:
        table, mkt = core.load_snapshot(snapshot_at(args.ticker, when))
        frames.append(core.straddles(table, mkt, args.expiries, strikes=args.strikes, moneyness=args.moneyness))
    out = pd.concat(frames, ignore_index=True)
    out["when"] = out["asof"].dt.tz_convert(core.NY).dt.strftime("%a %Y-%m-%d %H:%M ET")

    pd.set_option("display.width", 200)
    for when, grp in out.groupby("when", sort=False):
        print(f"\n{args.ticker.upper()} straddles, {when}, spot {grp['spot'].iloc[0]:.2f}")
        show = grp[["expiry", "dte", "strike", "moneyness", "call", "put", "straddle", "buy", "sell",
                    "move_pct", "be_low", "be_high"]].rename(columns={"move_pct": "move"})
        print(show.to_string(index=False, formatters={
            "dte": "{:.0f}d".format, "moneyness": "{:.3f}".format, "move": "{:.1%}".format,
            **{c: "{:.2f}".format for c in ("call", "put", "straddle", "buy", "sell", "be_low", "be_high")}}))

    if len(args.at) > 1:
        key = "strike" if args.strikes else "moneyness"
        wide = (out.pivot_table(index=["expiry", "target"], columns="when", values="straddle", sort=False)
                   .rename_axis(index=["expiry", key]))
        wide = wide[[w for w in out["when"].unique() if w in wide.columns]]
        change = wide.diff(axis=1).iloc[:, 1:]
        change.columns = [f"change to {c.split(' ')[1]}" for c in change.columns]
        print("\nStraddle (mid) by snapshot and the change between consecutive snapshots:")
        print(pd.concat([wide, change], axis=1).to_string(float_format="{:.2f}".format))

    if args.csv:
        out.drop(columns="when").to_csv(args.csv, index=False)
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
