"""Offline self-checks for iv_core. Run: python check_iv.py"""
import numpy as np
import pandas as pd

import iv_core as core

# A synthetic chain with a known smile, forward and rate.
SPOT, RATE, FWD_CARRY = 100.0, 0.04, 0.01      # forward sits 1% above spot*exp(rT): a "dividend/borrow" effect
ASOF = pd.Timestamp("2026-11-11 17:00", tz="UTC")
true_vol = lambda k: 0.5 + 0.8 * k ** 2 - 0.3 * k


def synthetic_chain():
    rows = []
    for expiry in ("2026-12-18", "2027-03-19"):
        close = (pd.Timestamp(expiry).tz_localize(core.NY) + pd.Timedelta(hours=16)).tz_convert("UTC")
        years = (close - ASOF).total_seconds() / core.YEAR_SECONDS
        fwd = SPOT * np.exp(RATE * years) * (1 + FWD_CARRY)
        for strike in np.arange(60.0, 150.0, 2.5):
            for kind in ("call", "put"):
                px = float(core.black76(kind == "call", fwd, strike, years, np.exp(-RATE * years),
                                        true_vol(np.log(strike / fwd))))
                rows.append(dict(contractSymbol="X", expiry=expiry, kind=kind, strike=strike, bid=px * 0.99,
                                 ask=px * 1.01, lastPrice=px, lastTradeDate=ASOF, volume=1, openInterest=1,
                                 yahoo_iv=np.nan))
    return pd.DataFrame(rows)


def main():
    mkt = core.Market("TEST", ASOF, SPOT, RATE)
    table = core.compute_iv(synthetic_chain(), mkt)
    checks = []

    vols = np.array([0.15, 0.45, 0.9, 2.0])
    for call in (True, False):
        px = core.black76(call, 101.0, np.array([70.0, 100.0, 120.0, 160.0]), 0.3, 0.99, vols)
        got = core.implied_vol(px, call, 101.0, np.array([70.0, 100.0, 120.0, 160.0]), 0.3, 0.99)
        checks.append((f"solver round trip ({'calls' if call else 'puts'})", np.allclose(got, vols, atol=1e-6)))

    checks.append(("price below intrinsic gives NaN", np.isnan(core.implied_vol(4.0, True, 110.0, 100.0, 0.5, 1.0))))

    years = table.groupby("expiry")["years"].first()
    want = SPOT * np.exp(RATE * years) * (1 + FWD_CARRY)
    checks.append(("forward recovered from put-call parity",
                   np.allclose(table.groupby("expiry")["forward"].first(), want, rtol=1e-6)))

    otm = table[table["otm"]]
    checks.append(("smile recovered on OTM options",
                   np.allclose(otm["iv"], true_vol(otm["log_moneyness"]), atol=1e-4)))
    checks.append(("OTM side is puts below the forward, calls above",
                   bool(((otm["kind"] == "put") == (otm["strike"] < otm["forward"])).all())))

    term = core.term_structure(table, mkt)
    checks.append(("ATM vol close to the true 50%", np.allclose(term["atm_iv"], 0.5, atol=2e-3)))
    checks.append(("straddle break-evens are strike ± cost",
                   np.allclose(term["be_high"] - term["be_low"], 2 * term["straddle"])))

    cm = core.constant_maturity(term, days=(60, 400))
    checks.append(("constant maturity interpolates inside, refuses outside",
                   abs(cm[60] - 0.5) < 2e-3 and cm[400] is None))

    wide = synthetic_chain()
    wide["bid"] *= 0.3
    checks.append(("wide spreads are dropped", core.compute_iv(wide, mkt)["iv"].isna().all()))

    # rebuild_snapshot: the same chain arriving as Databento OPRA quotes must give the same vols.
    import rebuild_snapshot as rb
    chain = synthetic_chain()
    osi = ("TEST".ljust(6) + chain["expiry"].str.replace("-", "").str[2:]
           + chain["kind"].str[0].str.upper() + (chain["strike"] * 1000).round().astype(int).astype(str).str.zfill(8))
    quotes = pd.DataFrame({"symbol": osi.to_numpy(), "bid_px_00": chain["bid"].to_numpy(),
                           "ask_px_00": chain["ask"].to_numpy()},
                          index=pd.DatetimeIndex([ASOF] * len(chain), name="ts_recv"))
    quotes = pd.concat([quotes.assign(bid_px_00=0.0), quotes])          # an older, worse quote per option first
    checks.append(("OSI symbol parsed", rb.parse_osi("NVDA  261113C00165000") == ("NVDA", "2026-11-13", "call", 165.0)))
    rebuilt = core.compute_iv(rb.chain_from_quotes(quotes, "TEST"), mkt)
    joined = table.merge(rebuilt, on=["expiry", "kind", "strike"], suffixes=("", "_rb"))
    checks.append(("rebuilt chain matches Yahoo-style chain (last quote per option wins)",
                   len(joined) == len(table) and np.allclose(joined["iv"], joined["iv_rb"], equal_nan=True)))

    st = core.straddles(table, mkt, ["2026-12-18"], strikes=[100.0, 101.2])
    at100 = table[(table["expiry"] == "2026-12-18") & (table["strike"] == 100.0)].set_index("kind")
    checks.append(("straddle = call mid + put mid, snapped to the nearest listed strike",
                   list(st["strike"]) == [100.0, 100.0]
                   and np.isclose(st["straddle"].iloc[0], at100.loc["call", "price"] + at100.loc["put", "price"])
                   and bool((st["sell"] <= st["straddle"]).all() and (st["straddle"] <= st["buy"]).all())
                   and np.isclose(st["be_high"].iloc[0] - st["be_low"].iloc[0], 2 * st["straddle"].iloc[0])))
    checks.append(("moneyness picks strikes relative to spot",
                   list(core.straddles(table, mkt, ["2026-12-18"], moneyness=[0.9, 1.1])["strike"]) == [90.0, 110.0]))

    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    raise SystemExit(0 if all(ok for _, ok in checks) else 1)


if __name__ == "__main__":
    main()
