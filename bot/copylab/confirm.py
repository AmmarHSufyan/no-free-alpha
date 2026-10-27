"""Confirm the quick screen with a real copy replay.

The quick screen (quick.py) counts realized PnL, which credits gains made *before* we would have started
copying (a position opened months ago and closed after T). Here the exact same monthly picks are replayed
through the copier from T: we take over their open positions at T's price, follow every trade after T with
delay, fees, slippage and funding, and value everything at market. Random sets of leaders from a random
pool of eligible wallets are replayed the same way as the placebo.

Sizing: the bulk data has no account values, so each leader is scaled so that their typical (90th percentile)
gross exposure over the 90 days before T equals 1x our money for that leader (cap 3x).
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from .. import config
from . import store
from .biglab import BULK, exposure_proxy, load_p2
from .copier import CopyCosts, Prices, copy_leader
from .lab import binance_funding, binance_symbols, to_binance

DAY = 86_400_000
VARIANTS = {"leader raw": CopyCosts(ideal=True), "copy 10s": CopyCosts(delay_s=10),
            "copy 10s + 25% stop": CopyCosts(delay_s=10, kill_dd=0.25)}


def _replay(task):
    """Worker: copy one set of leaders through one month; returns (% return, leaders used, liquidated)."""
    dfs, t0, t1, symbols, fund, costs, capital = task
    prices = Prices({c: store.load(s) for c, s in symbols.items()})
    eqs = {a: exposure_proxy(df, t0 - 90 * DAY, t0) for a, df in dfs.items()}
    use = [a for a, e in eqs.items() if e and not math.isnan(e) and e > 0]
    if not use:
        return 0.0, 0, 0
    res = [copy_leader(dfs[a], t0, t1, eqs[a], capital / len(use), prices, fund, costs) for a in use]
    return (sum(r.equity.iloc[-1] for r in res) / capital - 1) * 100, len(use), sum(r.liquidated for r in res)


def run(placebo_sets: int = 200, k: int = 50, capital: float = 10_000.0, workers: int | None = None,
        only_months: list[str] | None = None) -> pd.DataFrame:
    t_start = time.time()
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    cand = json.loads((BULK / "candidates.json").read_text())
    picks = json.loads((config.RUNS_DIR / "copylab" / "quick_15m_picks.json").read_text())
    dfs = load_p2(set(cand["wallets"]))
    symbols = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, symbols))}
    print(f"{len(dfs):,} wallets with fills loaded after {time.time() - t_start:.0f}s", flush=True)

    months = sorted({p["month"] for p in picks} & set(only_months or {p["month"] for p in picks}))
    finder_sets = {(p["month"], p["finder"]): [w for w in p["wallets"] if w in dfs] for p in picks}
    finder_wallets = {w for p in picks for w in p["wallets"]}
    placebo_pool = sorted(set(dfs) - finder_wallets) if cand.get("placebo_pool") else sorted(dfs)
    ds = min(int(df["time"].min()) for df in dfs.values())
    end = int(pd.Timestamp(months[-1], tz="UTC").timestamp() * 1000) + 30 * DAY
    coins_all = {c for df in dfs.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
    fund = {c: binance_funding(mapping[c], ds, end) for c in coins_all}

    tasks, keys = [], []
    for m in months:
        t0 = int(pd.Timestamp(m, tz="UTC").timestamp() * 1000)
        t1 = t0 + 30 * DAY
        active = [a for a in placebo_pool if ((dfs[a]["time"] >= t0 - 90 * DAY) & (dfs[a]["time"] < t0)).any()]
        rng = random.Random(int(m.replace("-", "")))
        sets = {name: ws for (mm, name), ws in finder_sets.items() if mm == m}
        for i in range(placebo_sets):
            sets[f"placebo{i}"] = rng.sample(active, min(k, len(active)))
        for name, ws in sets.items():
            sub = {a: dfs[a][dfs[a]["time"] < t1] for a in ws}
            coins = {c for df in sub.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
            for vname, vc in VARIANTS.items():
                if name.startswith("placebo") and vname != "copy 10s":
                    continue
                tasks.append((sub, t0, t1, {c: mapping[c] for c in coins}, {c: fund.get(c, {}) for c in coins}, vc, capital))
                keys.append((m, name, vname))
    print(f"replaying {len(tasks):,} leader sets on {workers} processes...", flush=True)
    with ProcessPoolExecutor(workers) as pool:
        out = list(pool.map(_replay, tasks, chunksize=4))
    res = pd.DataFrame([(m, n, v, r, u, l) for (m, n, v), (r, u, l) in zip(keys, out)],
                       columns=["month", "set", "variant", "return_%", "leaders_used", "liquidated"])
    btc_t, btc_o = store.load("BTC/USDT:USDT")
    res["btc_%"] = res["month"].map(lambda m: float((lambda t0: (btc_o[np.searchsorted(btc_t, t0 + 30 * DAY)] /
                                                                  btc_o[np.searchsorted(btc_t, t0)] - 1) * 100)(
        int(pd.Timestamp(m, tz="UTC").timestamp() * 1000))))
    path = config.RUNS_DIR / "copylab" / f"confirm-{pd.Timestamp.now():%Y%m%d-%H%M%S}.csv"
    res.to_csv(path, index=False)
    print(f"done in {(time.time() - t_start) / 60:.1f} min -> {path}", flush=True)
    return res


def summarize(res: pd.DataFrame) -> pd.DataFrame:
    comp = lambda r: ((1 + r / 100).prod() - 1) * 100
    plac = res[res["set"].str.startswith("placebo")]
    by_month_plac = plac.groupby("month")["return_%"]
    rows = []
    for (name, v), g in res[~res["set"].str.startswith("placebo")].groupby(["set", "variant"]):
        g = g.sort_values("month")
        beats = [float((plac[plac["month"] == m]["return_%"] < r).mean() * 100) for m, r in zip(g["month"], g["return_%"])]
        rows.append({"finder": name, "how": v, "total_%": comp(g["return_%"]), "months_up": f"{(g['return_%'] > 0).sum()}/{len(g)}",
                     "worst_month_%": g["return_%"].min(), "avg_beats_%_random": float(np.mean(beats)),
                     "months_beating_90%_random": int(sum(b >= 90 for b in beats))})
    med = by_month_plac.median()
    rows.append({"finder": "random sets (median each month)", "how": "copy 10s", "total_%": comp(med),
                 "months_up": f"{(med > 0).sum()}/{len(med)}", "worst_month_%": med.min(), "avg_beats_%_random": 50.0})
    btc = res.groupby("month")["btc_%"].first()
    rows.append({"finder": "hold BTC", "how": "-", "total_%": comp(btc), "months_up": f"{(btc > 0).sum()}/{len(btc)}",
                 "worst_month_%": btc.min()})
    return pd.DataFrame(rows)
