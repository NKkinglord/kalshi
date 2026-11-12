"""Save PNG pictures of an implied-volatility surface.

    python vol_surface_png.py                       # NVDA right now
    python vol_surface_png.py AAPL                  # another ticker
    python vol_surface_png.py --at "2026-11-11 12:00"   # the saved snapshot at or just before that New York time
    python vol_surface_png.py --list                # which snapshots are saved

Each run writes two pictures to output/:
    <TICKER>_surface_<time>.png   3D surface: strike x days to expiry x implied vol
    <TICKER>_heatmap_<time>.png   the same numbers as a flat grid, easier to read values from

iv_core.py does the data pull and the implied-vol maths. This file only reshapes
the result with pandas and draws it.
"""
from __future__ import annotations

import argparse
import pathlib

import matplotlib
matplotlib.use("Agg")           # draw straight to files, no window needed
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import iv_core as core

OUTPUT_DIR = core.ROOT / "output"
CMAP = "Blues"                  # one hue, light = low vol, dark = high vol
INK, INK_2, GRID = "#0b0b0b", "#52514e", "#d9d8d2"
DAY_TICKS = [1, 2, 5, 10, 30, 90, 180, 365, 730]


def iv_grid(table: pd.DataFrame, mkt: core.Market, lo: float, hi: float, n_strikes: int = 80,
            min_points: int = 5) -> pd.DataFrame:
    """Implied vol as a table: one row per expiry, one column per strike.

    Every expiry quotes a different set of strikes, so each smile is linearly
    interpolated onto one shared strike grid. Cells outside the strikes an expiry
    actually quotes stay empty (NaN): nothing is extrapolated or smoothed.
    """
    otm = table[table["otm"] & table["iv"].notna() & table["moneyness"].between(lo, hi)]
    counts = otm.groupby("expiry")["iv"].size()
    otm = otm[otm["expiry"].isin(counts[counts >= min_points].index)]

    quoted = otm.pivot_table(index="strike", columns="expiry", values="iv")      # strikes x expiries, with holes
    grid = np.round(np.linspace(lo * mkt.spot, hi * mkt.spot, n_strikes), 2)
    filled = (quoted.reindex(quoted.index.union(grid))
                    .interpolate(method="index", limit_area="inside")           # along strike, inside quotes only
                    .loc[grid])
    out = filled.T                                                               # expiries x strikes
    out.index = pd.MultiIndex.from_arrays(
        [out.index, out.index.map(otm.groupby("expiry")["dte"].first())], names=["expiry", "dte"])
    return out.sort_index(level="dte")


def color_limits(z) -> tuple[float, float]:
    """Colour range from the 2nd to 98th percentile, so a handful of extreme wing
    quotes on the nearest expiry do not wash out every other expiry."""
    lo, hi = np.percentile(z.compressed(), [2, 98])
    return float(lo), float(hi)


def caption(table: pd.DataFrame, mkt: core.Market) -> tuple[str, str]:
    when = mkt.asof.tz_convert(core.NY).strftime("%a %b %d %Y, %H:%M ET")
    priced = table[table["iv"].notna()]
    share_last = (priced["price_source"] == "last").mean()
    title = f"{mkt.ticker} implied volatility surface\n{when}  ·  spot ${mkt.spot:,.2f}"
    source = "bid-ask mids" if share_last < 0.2 else f"{share_last:.0%} last-trade prices (market closed, noisier)"
    foot = f"Out-of-the-money options, {mkt.source}, {len(priced):,} contracts, {source}."
    if mkt.earnings:
        foot += f"  Next earnings: {mkt.earnings}."
    return title, foot


def draw_surface(grid: pd.DataFrame, table, mkt, path: pathlib.Path, elev: float, azim: float):
    strikes = grid.columns.to_numpy(dtype=float)
    dte = grid.index.get_level_values("dte").to_numpy()
    x, y = np.meshgrid(strikes, np.log10(dte))      # log time: earnings-week expiries are days apart, LEAPS years
    z = np.ma.masked_invalid(grid.to_numpy() * 100)

    fig = plt.figure(figsize=(11, 8))
    ax = fig.add_subplot(projection="3d")
    vmin, vmax = color_limits(z)
    surf = ax.plot_surface(x, y, z, cmap=CMAP, vmin=vmin, vmax=vmax, rstride=1, cstride=1,
                           linewidth=0.15, edgecolor="#ffffff", antialiased=True)
    ticks = [d for d in DAY_TICKS if dte.min() * 0.8 <= d <= dte.max() * 1.2]
    ax.set_yticks(np.log10(ticks), [str(d) for d in ticks])
    ax.set_xlabel("Strike ($)", labelpad=8, color=INK_2)
    ax.set_ylabel("Days to expiry (log scale)", labelpad=8, color=INK_2)
    ax.set_zlabel("Implied vol (%)", labelpad=6, color=INK_2)
    ax.tick_params(colors=INK_2, labelsize=9)
    ax.view_init(elev=elev, azim=azim)
    ax.set_box_aspect((1.25, 1.25, 0.75), zoom=1.12)
    for axis in (ax.xaxis, ax.yaxis, ax.zaxis):
        axis.set_pane_color((1, 1, 1, 0))
        axis._axinfo["grid"]["color"] = GRID
    fig.colorbar(surf, ax=ax, shrink=0.5, pad=0.08, label="Implied vol (%)", extend="both")

    title, foot = caption(table, mkt)
    fig.suptitle(title, x=0.06, ha="left", fontsize=14, color=INK)
    fig.text(0.06, 0.03, foot, fontsize=9, color=INK_2)
    fig.savefig(path, dpi=200, facecolor="white", bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)


def draw_heatmap(grid: pd.DataFrame, table, mkt, path: pathlib.Path):
    strikes = grid.columns.to_numpy(dtype=float)
    expiries = grid.index.get_level_values("expiry")
    dte = grid.index.get_level_values("dte").to_numpy()
    z = np.ma.masked_invalid(grid.to_numpy() * 100)

    fig, ax = plt.subplots(figsize=(11, 0.34 * len(grid) + 2.6))
    vmin, vmax = color_limits(z)
    mesh = ax.pcolormesh(strikes, np.arange(len(grid)), z, cmap=CMAP, shading="nearest", vmin=vmin, vmax=vmax)
    ax.set_facecolor("#f0efec")                                   # empty cells = no usable quote
    ax.set_yticks(np.arange(len(grid)), [f"{e}  ({d:.0f}d)" for e, d in zip(expiries, dte)], fontsize=9)
    ax.invert_yaxis()                                             # nearest expiry on top
    ax.set_xlabel("Strike ($)", color=INK_2)
    ax.tick_params(colors=INK_2)
    for side in ax.spines.values():
        side.set_visible(False)

    ax.axvline(mkt.spot, color=INK, lw=1, ls=":")
    ax.annotate(f"spot ${mkt.spot:.2f}", (mkt.spot, 1), xycoords=("data", "axes fraction"), xytext=(0, 4),
                textcoords="offset points", ha="center", fontsize=9, color=INK_2)
    if mkt.earnings:
        before = int((expiries <= mkt.earnings).sum())           # rows expiring on or before the earnings date
        if 0 < before < len(grid):
            ax.axhline(before - 0.5, color="#eb6834", lw=1.5, ls="--")
            ax.annotate(f"earnings {mkt.earnings[5:]}", (1, before - 0.5), xycoords=("axes fraction", "data"),
                        xytext=(-4, 3), textcoords="offset points", ha="right", va="bottom", fontsize=9, color=INK)
    fig.colorbar(mesh, ax=ax, pad=0.02, label="Implied vol (%)", extend="both")

    title, foot = caption(table, mkt)
    fig.suptitle(title, x=0.02, ha="left", fontsize=14, color=INK)
    fig.text(0.02, 0.0, foot + "  Grey = no usable quote.", fontsize=9, color=INK_2)
    fig.tight_layout(rect=(0, 0.03, 1, 0.98))
    fig.savefig(path, dpi=200, facecolor="white", bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def snapshot_at(ticker: str, when: str) -> str:
    """Path of the latest saved snapshot at or before `when` (New York time)."""
    target = pd.Timestamp(when)
    target = (target.tz_localize(core.NY) if target.tzinfo is None else target).tz_convert("UTC")
    saved = [m for m in core.list_snapshots(ticker) if pd.Timestamp(m["asof"]) <= target]
    if not saved:
        raise SystemExit(f"No {ticker.upper()} snapshot saved at or before {when}. Run with --list to see what exists. "
                         "Yahoo has no historical chains, so only times someone ran this tool are available. "
                         "To rebuild another moment from OPRA quotes (needs Databento): "
                         f'python rebuild_snapshot.py {ticker.upper()} --at "{when}"')
    return saved[-1]["path"]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ticker", nargs="?", default="NVDA")
    ap.add_argument("--at", help='use the saved snapshot at or just before this New York time, e.g. "2026-11-11 12:00"')
    ap.add_argument("--list", action="store_true", help="list saved snapshots for the ticker and exit")
    ap.add_argument("--min-moneyness", type=float, default=0.7, help="lowest strike shown, as a fraction of spot")
    ap.add_argument("--max-moneyness", type=float, default=1.4, help="highest strike shown, as a fraction of spot")
    ap.add_argument("--max-days", type=float, default=None, help="leave out expiries further away than this")
    ap.add_argument("--elev", type=float, default=24, help="3D camera height in degrees")
    ap.add_argument("--azim", type=float, default=-128, help="3D camera rotation in degrees")
    ap.add_argument("--no-save", action="store_true", help="do not save a snapshot of a live pull")
    args = ap.parse_args()

    if args.list:
        for m in core.list_snapshots(args.ticker):
            t = pd.Timestamp(m["asof"]).tz_convert(core.NY)
            print(f"{t:%Y-%m-%d %H:%M} ET   spot {m['spot']:.2f}   {m['n_priced']} contracts priced")
        return

    if args.at:
        table, mkt = core.load_snapshot(snapshot_at(args.ticker, args.at))
    else:
        print(f"Pulling the {args.ticker.upper()} chain from Yahoo...")
        raw, mkt = core.fetch_chain(args.ticker)
        table = core.compute_iv(raw, mkt)
        if not args.no_save:
            core.save_snapshot(table, core.term_structure(table, mkt), mkt)
    if args.max_days:
        table = table[table["dte"] <= args.max_days]

    grid = iv_grid(table, mkt, args.min_moneyness, args.max_moneyness)
    if grid.empty:
        raise SystemExit("No expiry has enough usable quotes to draw. Try again during market hours.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = mkt.asof.tz_convert(core.NY).strftime("%Y-%m-%d_%H%M")
    surface = OUTPUT_DIR / f"{mkt.ticker}_surface_{stamp}.png"
    heatmap = OUTPUT_DIR / f"{mkt.ticker}_heatmap_{stamp}.png"
    draw_surface(grid, table, mkt, surface, args.elev, args.azim)
    draw_heatmap(grid, table, mkt, heatmap)
    print(f"{mkt.ticker} as of {mkt.asof.tz_convert(core.NY):%Y-%m-%d %H:%M} ET, spot {mkt.spot:.2f}, "
          f"{len(grid)} expiries drawn")
    print(surface)
    print(heatmap)


if __name__ == "__main__":
    main()
