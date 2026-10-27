"""Quick persistence test on Hyperliquid pass-1 totals (every wallet): do finder picks keep an edge that
beats our copying costs, and beat random picks? Minutes instead of hours.

Edge is measured per dollar traded, on Binance-priced coins only:
  gross edge (bps) = (net PnL + the leader's own fees) / notional traded x 10,000
  copy edge (bps)  = gross edge - our cost per dollar traded (taker 4.5 bps + slippage 3 bps = 7.5 bps)
This ignores copy delay and sizing, so it's an optimistic screen: if copying fails here, it fails for real.
"""
from __future__ import annotations

import math
import time

import numpy as np
import pandas as pd

from .biglab import load_p1

DAY = 86_400_000
COST_BPS = 7.5


def window_stats(p1: pd.DataFrame, lo: int, hi: int) -> pd.DataFrame:
    w = p1[(p1["t"] >= lo) & (p1["t"] < hi)]
    g = w.groupby("user", observed=True)
    s = g.agg(net=("net_sup", "sum"), notional=("notional_sup", "sum"), notional_all=("notional", "sum"), fees=("fees", "sum"),
              orders=("orders", "sum"), closes=("close_orders", "sum"), liq=("liq", "sum"), spot=("spot", "sum"),
              fills=("fills", "sum"), days=("t", "nunique"))
    s["gross"] = s["net"] + s["fees"] * s["notional"] / s["notional_all"].clip(lower=1e-9)
    s["gross_bps"] = s["gross"] / s["notional"].clip(lower=1e-9) * 1e4
    return s


def daily_tstat(p1: pd.DataFrame, users: pd.Index, lo: int, hi: int) -> pd.DataFrame:
    w = p1[(p1["t"] >= lo) & (p1["t"] < hi) & p1["user"].isin(users)]
    ndays = int((hi - lo) // DAY)
    piv = w.pivot_table(index="user", columns="t", values="net_sup", aggfunc="sum", fill_value=0.0, observed=True)
    piv = piv.reindex(index=users, fill_value=0.0)
    mean = piv.sum(axis=1) / ndays
    var = ((piv.pow(2).sum(axis=1) - ndays * mean ** 2) / max(ndays - 1, 1)).clip(lower=0)  # zeros for idle days included
    t = mean / np.sqrt(var / ndays)
    month = w.assign(m=(hi - w["t"]) // (30 * DAY)).groupby(["user", "m"], observed=True)["net_sup"].sum().unstack(fill_value=0)
    return pd.DataFrame({"tstat": t.replace([np.inf, -np.inf], np.nan).fillna(0),
                         "months_up": (month > 0).sum(axis=1).reindex(users, fill_value=0)})


def btc_return(t0: int, t1: int) -> float:
    """BTC % change over the window (to spot finders that only 'win' when the market rises)."""
    try:
        from . import store
        t, o = store.load("BTC/USDT:USDT")
        i0, i1 = np.searchsorted(t, t0), min(np.searchsorted(t, t1), len(t) - 1)
        return float((o[i1] / o[i0] - 1) * 100) if t[0] <= t0 else math.nan
    except (OSError, ValueError):
        return math.nan


def bh(p: pd.Series, q: float = 0.10) -> pd.Series:
    order = p.sort_values()
    m = len(order)
    passed = order.to_numpy() <= q * np.arange(1, m + 1) / m
    cut = np.nonzero(passed)[0].max() + 1 if passed.any() else 0
    return pd.Series(np.arange(m) < cut, index=order.index).reindex(p.index)


def run(lookback: int = 90, hold: int = 30, k: int = 50, placebo: int = 1000, seed: int = 0) -> dict:
    t_start = time.time()
    p1 = load_p1()
    first, last = int(p1["t"].min()), int(p1["t"].max()) + DAY
    starts = list(range(first + lookback * DAY, last - hold * DAY + 1, hold * DAY))
    rng = np.random.default_rng(seed)
    print(f"{p1['day'].nunique()} days, {p1['user'].nunique():,} wallets, {len(starts)} test months "
          f"({pd.Timestamp(starts[0], unit='ms'):%Y-%m-%d} .. {pd.Timestamp(starts[-1] + hold * DAY, unit='ms'):%Y-%m-%d})", flush=True)
    rows, picks_log = [], []
    for T in starts:
        before = window_stats(p1, T - lookback * DAY, T)
        elig = before[(before["orders"] / lookback <= 30) & (before["days"] >= 20) & (before["closes"] >= 20)
                      & (before["liq"] == 0) & (before["spot"] / before["fills"].clip(lower=1) <= 0.1)
                      & (before["notional"] >= 1e5) & (before["notional"] / before["notional_all"].clip(lower=1e-9) >= 0.7)]
        if len(elig) < k:
            continue
        ts = daily_tstat(p1, elig.index, T - lookback * DAY, T)
        elig = elig.join(ts)
        week = window_stats(p1, T - 7 * DAY, T)["net"].reindex(elig.index).fillna(0)
        p = elig["tstat"].map(lambda t: 0.5 * math.erfc(t / math.sqrt(2)))  # one-sided p from the t-stat
        disc = bh(p, 0.10)
        # Shrink each wallet's edge toward the crowd, more when it traded less (noisier estimate).
        se2 = (elig["gross_bps"].var() / elig["closes"].clip(lower=1)) * elig["closes"].median()
        tau2 = max(elig["gross_bps"].var() - se2.mean(), 1e-9)
        shrunk = elig["gross_bps"].mean() + (elig["gross_bps"] - elig["gross_bps"].mean()) * tau2 / (tau2 + se2)
        sets = {
            "hot (best last 7 days $)": list(week.nlargest(k).index),
            "steady (t-stat, 2/3 months up)": list(elig[elig["months_up"] >= 2].nlargest(k, "tstat").index),
            "edge (best shrunk bps)": list(shrunk.nlargest(k).index),
            "skill (luck test + shrunk edge)": list(shrunk[disc & (shrunk > COST_BPS)].nlargest(k).index),
        }
        after = window_stats(p1, T, T + hold * DAY).reindex(elig.index).fillna(0)

        def score(users):
            a = after.loc[users]
            notional = a["notional"].sum()
            gross = a["gross"].sum()
            return {"leaders": len(users), "gross_bps": gross / notional * 1e4 if notional else math.nan,
                    "copy_bps": (gross - COST_BPS / 1e4 * notional) / notional * 1e4 if notional else math.nan,
                    "leaders_beating_cost_%": float(((a["gross_bps"] > COST_BPS) & (a["notional"] > 0)).mean() * 100) if len(a) else math.nan,
                    "still_active_%": float((a["notional"] > 0).mean() * 100) if len(a) else math.nan}

        plac = np.array([score(list(rng.choice(elig.index, k, replace=False)))["copy_bps"] for _ in range(placebo)])
        month = pd.Timestamp(T, unit="ms").strftime("%Y-%m-%d")
        for name, users in sets.items():
            sc = score(users)
            sc.update({"month": month, "finder": name, "eligible": len(elig), "discoveries": int(disc.sum()),
                       "beats_%_random": float((plac < sc["copy_bps"]).mean() * 100) if users and not math.isnan(sc["copy_bps"]) else math.nan})
            rows.append(sc)
            picks_log.append({"month": month, "finder": name, "wallets": users})
        rows.append({"month": month, "finder": "random sets (median)", "copy_bps": float(np.nanmedian(plac)), "leaders": k,
                     "eligible": len(elig), "beats_%_random": 50.0, "btc_%": btc_return(T, T + hold * DAY)})
        print(f"  {month}: {len(elig):,} eligible, {int(disc.sum())} passed the luck test ({time.time() - t_start:.0f}s)", flush=True)
    return {"rows": pd.DataFrame(rows), "picks": picks_log}
