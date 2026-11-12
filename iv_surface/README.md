# iv_surface: implied-volatility surface pictures

This folder is one self-contained piece of the group project. It pulls a stock's options chain, works out the
implied volatility (IV) of every option, and saves PNG pictures of the IV surface at that moment. It is set up for
NVDA (the long straddle pitch) but takes any ticker.

Nothing else in the repo depends on it, and it needs no API key.

| 3D surface | Heatmap |
|---|---|
| ![surface](docs/example_surface.png) | ![heatmap](docs/example_heatmap.png) |

## The short version, for everyone

**What an implied volatility surface is.** An option's price tells you how big a move the market expects in the
stock before the option expires. Turn that price back into an expected move and you get the option's *implied
volatility* (IV), quoted as a yearly percentage. Do that for every strike and every expiry date and you get a
surface: strike along one side, time to expiry along the other, expected move as the height or colour. High IV
means expensive options and an expected big move; low IV means cheap options and calm expectations.

**What a straddle is.** Buying a call and a put at the same strike and expiry. It makes money if the stock moves
far enough in *either* direction before expiry, and loses what you paid if it does not. The price of the straddle
is therefore the market's estimate of the size of the move. Our pitch is that the market underprices the move
around NVDA's earnings, so we would buy the straddle.

**What is in this folder, ready to look at (no code needed).**

| What | Where |
|---|---|
| Surface and heatmap for Monday Nov 9, 10:46 ET (spot $179.13) | `output/NVDA_surface_2026-11-09_1046.png`, `output/NVDA_heatmap_2026-11-09_1046.png` |
| Same for Thursday Nov 12, 10:46 ET (spot $164.51) | `output/NVDA_surface_2026-11-12_1046.png`, `output/NVDA_heatmap_2026-11-12_1046.png` |
| Straddle prices, 7 expiries from Nov 20 2026 to Jan 2029, 9 strikes ($150 to $190) | `output/straddles_strikes.csv` |
| Straddle prices, same 7 expiries, strikes 5% below / at / 5% above that day's stock price | `output/straddles_moneyness.csv` |
| Straddle prices, the 5 expiries 1 to 3 months out, 13 strikes ($140 to $200) | `output/straddles_1to3m.csv` |

Every table has both days side by side, so the same contract can be compared before and after the stock's drop
from $179 to $164.5. Each row is one straddle on one day and has:

- `call`, `put`: the fair price of each leg (midpoint between what buyers bid and sellers ask).
- `straddle`: call + put, the fair price of the straddle. `buy` is what it really costs at the sellers' asking
  prices; `sell` is what you would get back at the buyers' bids. The gap between them is the trading cost.
- `move_pct`: the straddle as a percentage of the stock price. This is the move the market is pricing in. If you
  think the stock will move more than this by expiry, the straddle is cheap.
- `be_low`, `be_high`: where the stock has to be at expiry for the straddle to break even.
- `moneyness`: strike divided by stock price. 1.00 is at the money; 0.95 is a strike 5% below the stock.

**How to read the pictures.** The heatmap is the easy one. Each row is an expiry date, each column a strike, and
darker blue means a bigger expected move. The dotted vertical line is the stock price, and the orange dashed
line is the earnings date: rows below it include the earnings announcement and are clearly darker than the row
just above it. That jump is the earnings premium, and it is what the straddle trade is about. The 3D picture shows
the same numbers as a landscape, with the earnings ridge visible as the dark peak near the front.

**Why Nov 9 exists even though nobody ran the tool that day.** Yahoo only shows today's option prices. For
Monday we bought the actual exchange quotes for that minute from Databento (cost: under one cent) and rebuilt the
snapshot. The footnote on those two pictures says so. Any other past minute can be rebuilt the same way; see
"A moment nobody snapshotted" below.

## Setup (once)

You need Python 3.10 or newer.

```bash
cd iv_surface
```

```bash
pip install -r requirements.txt
```

## Use

Picture of the surface right now:

```bash
python vol_surface_png.py
```

It prints the two files it wrote, in `iv_surface/output/`:

- `NVDA_surface_<date>_<time>.png`: 3D surface, strike by days to expiry by IV.
- `NVDA_heatmap_<date>_<time>.png`: the same numbers as a flat grid. Use this one when you need to read values.

The time in the file name and in the picture title is New York time.

Another ticker:

```bash
python vol_surface_png.py AAPL
```

Picture of the surface at an earlier time:

```bash
python vol_surface_png.py --at "2026-11-12 10:50"
```

`--at` uses the latest saved snapshot at or before that New York time. Yahoo does not provide past options chains,
so the only times available are the ones when somebody ran the tool, plus any rebuilt from Databento. See which exist:

```bash
python vol_surface_png.py --list
```

Every live run saves a snapshot to `data/snapshots/<TICKER>/`. **Commit and push those files** so the whole group
can redraw the same moment. To build a history, run the tool once a day during market hours (9:30 to 16:00 New York).

### A moment nobody snapshotted (for example Monday Nov 9)

`rebuild_snapshot.py` builds the snapshot for any past minute from the OPRA consolidated option quotes on Databento
(dataset `OPRA.PILLAR`, schema `cbbo-1m`: every listed option's best bid and ask, sampled once a minute). Spot, the
T-bill rate and the earnings date still come from Yahoo. It needs the `databento` package and `DATABENTO_API_KEY`
in the environment (the same key as the futures pipeline). Nothing else in this folder needs either.

```bash
python -m pip install databento
export DATABENTO_API_KEY=db-...
python rebuild_snapshot.py --at "2026-11-09 10:46"
python vol_surface_png.py --at "2026-11-09 10:46"
```

The first command prints the Databento cost before downloading anything and stops if it is above `--max-cost`
(default $1; a five-minute window of one ticker's quotes is far below that). Add `--yes` to skip that stop. The saved
snapshot looks like any other, so `--list` shows it and the pictures are drawn and named the usual way
(`NVDA_surface_2026-11-09_1046.png`), with the footnote saying the quotes came from OPRA rather than Yahoo.
Rebuilt snapshots are worth committing too.

Use `python -m pip`, not bare `pip`, so the package lands in the same Python that runs the scripts. Yahoo keeps
1-minute spot bars for about 30 days; for older moments pass the stock price with `--spot`.

### Straddle prices from saved snapshots

```bash
python straddles.py --at "2026-11-09 10:46" --at "2026-11-12 10:46" --expiries 2026-11-20 2026-12-18 --strikes 165 180
python straddles.py --at "2026-11-12 10:46" --expiries 2026-11-20 --moneyness 0.95 1 1.05
```

For each snapshot, expiry and strike: the call and put mids, the straddle at the mid, what it costs to buy at the
asks and what you get selling at the bids, the implied move as a percent of spot, and the break-evens. Strikes are
dollars, or fractions of that day's spot with `--moneyness`, snapped to the nearest listed strike. With two or more
`--at` it also prints the change in each straddle between snapshots. `--csv path` writes the full table.

### Options

| Option | Default | What it does |
|---|---|---|
| `--min-moneyness`, `--max-moneyness` | 0.7, 1.4 | Strike range shown, as a fraction of the stock price |
| `--max-days` | none | Leave out expiries further away than this, e.g. `--max-days 90` to zoom in on the near term |
| `--elev`, `--azim` | 24, -128 | Camera height and rotation of the 3D picture, in degrees |
| `--no-save` | off | Do not save a snapshot of this pull |

## How to read the pictures

- **Strike axis:** the smile. IV is higher for strikes far from the current price than at the money.
- **Expiry axis:** the term structure. It is on a log scale because the expiries around earnings are days apart
  and the longest ones are years away.
- **Earnings:** the heatmap draws a dashed line at the next earnings date. Expiries just after it carry much higher
  IV than the ones just before, because the market prices in the earnings move. That gap is the core of the
  straddle pitch: a long straddle pays if the actual move is bigger than the one this IV implies.
- **Colour:** light is low IV, dark is high. The colour range is clipped at the 2nd and 98th percentile so a few
  extreme quotes do not wash out the rest; the arrows on the colour bar mark values beyond it.
- **Grey cells (heatmap) and gaps (3D):** no usable quote at that strike for that expiry.

## How the IV is computed

1. **Chain:** every listed expiry from Yahoo Finance through `yfinance`. Quotes are about 15 minutes delayed.
   A rebuilt snapshot uses the OPRA consolidated quote at that minute instead; the picture's footnote names the source.
2. **Price per option:** the midpoint of bid and ask. Options with no bid, or a bid-ask spread wider than 50% of the
   mid, are dropped. If the market is closed and Yahoo has blanked the quotes, the last trade of that session is
   used instead and the picture's footnote says so (those pictures are noisier).
3. **Forward price per expiry:** from put-call parity (`call - put = discount * (forward - strike)`) at the five
   strikes nearest the stock price. This picks up dividends and borrow cost without assuming them.
4. **IV:** the volatility that makes the Black-76 formula match the option's price, found by bisection.
5. **Which options:** out-of-the-money only, puts below the forward and calls above. They are the liquid ones.
6. **Grid for the picture:** pandas pivots the result into a table of expiries by strikes and interpolates each
   expiry linearly along strike, only between strikes that are actually quoted. Nothing is smoothed or extrapolated.

Time to expiry is calendar days / 365, measured to the 4pm New York close on the expiry date. The risk-free rate is
the 13-week Treasury bill (`^IRX`).

## Limits

- NVDA options are American-style and the formula is European. The error is small for out-of-the-money options on
  a stock with almost no dividend, but it is not zero.
- Very short expiries (1 to 2 days) have jumpy IV in the wings. Do not read much into them.
- The earnings date comes from Yahoo and can be an estimate. Check it against the company's investor relations page.
- Yahoo's own `impliedVolatility` column will not match exactly, especially for short expiries.

## Files

| File | Purpose |
|---|---|
| `vol_surface_png.py` | The command you run. Reshapes the data with pandas and draws the PNGs with matplotlib. |
| `iv_core.py` | Data pull, quote filtering, forward, implied vol, snapshots. No plotting. |
| `rebuild_snapshot.py` | Snapshot for a past minute from Databento OPRA quotes, for moments nobody ran the tool at. |
| `straddles.py` | Straddle prices (mid, buy, sell, implied move, break-evens) at chosen expiries and strikes, across snapshots. |
| `check_iv.py` | Offline tests on a made-up chain with a known answer: `python check_iv.py` |
| `data/snapshots/` | Saved chains with computed IV, one `.csv.gz` and one `.json` per run. Committed. |
| `output/` | The pictures and straddle tables listed at the top. Committed, so they can be viewed on GitHub without running anything. |
| `docs/` | The example pictures above. |

To use the numbers in your own code:

```python
import iv_core as core
raw, mkt = core.fetch_chain("NVDA")
table = core.compute_iv(raw, mkt)          # one row per option: strike, expiry, price, forward, iv, delta...
term = core.term_structure(table, mkt)     # per expiry: at-the-money IV, straddle cost, break-evens
```
