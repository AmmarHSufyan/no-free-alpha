"""Big-pool walk-forward copy test on Hyperliquid bulk data (every wallet, not 1,000 samples).

Inputs (from the AWS job, see docs/aws-bulk-pipeline.md):
  data/hyperliquid/bulk/p1/<day>.parquet  per (wallet, day) totals for every wallet
  data/hyperliquid/bulk/p2/<day>.parquet  individual fills of the candidate wallets (Binance-priced coins)

Differences from lab.py:
  * finder stats come straight from the per-day totals (fast, vectorized),
  * no account values exist in bulk data, so each leader is sized by their *typical exposure*: the 90th
    percentile of their gross position value over the lookback. At that exposure we are 1x our money
    (cap 3x). This is "own sizing" rather than inheriting the leader's leverage,
  * a placebo: copy many random sets of leaders each month; a finder is only real if it beats most of them.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .. import config
from . import hyperliquid as hl
from . import store
from .copier import CopyCosts, Prices, copy_leader
from .finder import DAY, select
from .lab import binance_funding, binance_symbols, periods, to_binance

BULK = config.DATA_DIR / "hyperliquid" / "bulk"
HOUR = 3_600_000


def day_ms(day: int) -> int:
    return int(pd.Timestamp(str(day), tz="UTC").timestamp() * 1000)


def load_p1() -> pd.DataFrame:
    files = sorted((BULK / "p1").glob("*.parquet"))
    df = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    df["t"] = df["day"].map({d: day_ms(d) for d in df["day"].unique()})
    df["user"] = df["user"].astype("category")
    return df


def choose_candidates(p1: pd.DataFrame, supported: list[str], out: Path, max_fills: float = 100e6, seed: int = 7) -> dict:
    """Wallets worth extracting fills for. Activity/behaviour only - never profit - so no future peeking
    beyond 'this wallet was an active, non-HFT perp trader of some size at some point'. If the list is too
    big to handle, a *random* subset is kept (random can't favour winners)."""
    g = p1.groupby("user", observed=True).agg(days=("orders", lambda s: int((s > 0).sum())), orders=("orders", "sum"),
                                               close_orders=("close_orders", "sum"), fills=("fills", "sum"),
                                               spot=("spot", "sum"), notional=("notional", "sum"),
                                               notional_sup=("notional_sup", "sum"))
    g["orders_per_day"] = g["orders"] / g["days"].clip(lower=1)
    ok = g[(g["days"] >= 30) & (g["close_orders"] >= 20) & (g["orders_per_day"] <= 30) & (g["notional"] >= 1e5)
           & (g["spot"] / g["fills"].clip(lower=1) <= 0.2) & (g["notional_sup"] / g["notional"].clip(lower=1e-9) >= 0.5)]
    eligible, eligible_fills = len(ok), int(ok["fills"].sum())
    if eligible_fills > max_fills:
        ok = ok.sample(frac=max_fills / eligible_fills, random_state=seed)
    cand = {"wallets": sorted(map(str, ok.index)), "coins": supported,
            "criteria": "days>=30, close_orders>=20, orders/active day<=30, traded>=$100k, spot share<=0.2, "
                        "Binance-priced notional>=50%; random subset if raw fills > max_fills",
            "eligible": eligible, "eligible_fills": eligible_fills, "kept": len(ok), "expected_fills": int(ok["fills"].sum())}
    out.write_text(json.dumps(cand))
    return cand


def load_p2(wallets: set[str] | None = None) -> dict[str, pd.DataFrame]:
    files = sorted((BULK / "p2").glob("*.parquet"))
    tab = pd.concat([pq.read_table(f).to_pandas() for f in files], ignore_index=True)
    if wallets is not None:
        tab = tab[tab["user"].isin(wallets)]
    tab = tab.sort_values(["user", "time"], kind="stable")
    tab["signed"] = np.where(tab["buy"], tab["sz"], -tab["sz"])
    tab["notional"] = tab["px"] * tab["sz"]
    tab["net"] = tab["closedPnl"] - tab["fee"]
    users = tab["user"].to_numpy()
    cut = np.flatnonzero(users[1:] != users[:-1]) + 1
    bounds = np.concatenate([[0], cut, [len(tab)]])
    cols = ["time", "coin", "px", "sz", "startPosition", "closedPnl", "fee", "crossed", "liq", "signed", "notional", "net"]
    return {users[a]: tab.iloc[a:b][cols].reset_index(drop=True) for a, b in zip(bounds[:-1], bounds[1:])}


def exposure_proxy(df: pd.DataFrame, lo: int, hi: int, q: float = 0.9) -> float:
    """90th percentile of the wallet's gross position value (sum over coins of |position| x price)."""
    w = df[(df["time"] >= lo) & (df["time"] < hi)]
    if w.empty:
        return math.nan
    pos, px, gross = {}, {}, []
    for c, sp, sg, p in zip(w["coin"], w["startPosition"], w["signed"], w["px"]):
        pos[c] = sp + sg
        px[c] = p
        gross.append(sum(abs(pos[k]) * px[k] for k in pos))
    return float(np.quantile(gross, q)) if gross else math.nan


def _proxies(task):
    addr, df, points = task
    return addr, [(t, exposure_proxy(df, lo, t)) for t, lo in points]


def period_stats(p1: pd.DataFrame, t0: int, lookback: int, equity: pd.Series) -> pd.DataFrame:
    """Same columns finder.select / skill.select_skill expect, computed from per-day totals."""
    w = p1[(p1["t"] < t0) & (p1["t"] >= t0 - lookback * DAY)]
    w = w[w["user"].isin(equity.index)]
    age = (t0 - w["t"]) // DAY
    by = w.groupby("user", observed=True)
    daily = w.pivot_table(index="user", columns=age, values="net_sup", aggfunc="sum", fill_value=0.0, observed=True)
    daily = daily.reindex(columns=range(lookback), fill_value=0.0)
    mean, std = daily.mean(axis=1), daily.std(axis=1)
    months = w.assign(m=(t0 - w["t"]) // (30 * DAY)).groupby(["user", "m"], observed=True)["net_sup"].sum().unstack(fill_value=0)
    week = w[w["t"] >= t0 - 7 * DAY].groupby("user", observed=True)
    month = w[w["t"] >= t0 - 30 * DAY].groupby("user", observed=True)
    s = pd.DataFrame({
        "equity": equity, "net": by["net_sup"].sum(), "closes": by["close_orders"].sum(),
        "fills_per_day": by["orders"].sum() / lookback, "months_profitable": (months > 0).sum(axis=1),
        "tstat": (mean / std * math.sqrt(lookback)).where(std > 0, 0.0), "sharpe": (mean / std * math.sqrt(365)).where(std > 0, 0.0),
        "supported_share": by["notional_sup"].sum() / by["notional"].sum().clip(lower=1e-9),
        "week_net": week["net_sup"].sum(), "week_closes": week["close_orders"].sum(), "month_fills": month["orders"].sum(),
        "spot_share": by["spot"].sum() / by["fills"].sum().clip(lower=1), "liq": by["liq"].sum(),
    })
    s = s.dropna(subset=["net"])
    s[["week_net", "week_closes", "month_fills"]] = s[["week_net", "week_closes", "month_fills"]].fillna(0)
    s["ret_%"] = s["net"] / s["equity"] * 100
    s["week_ret_%"] = s["week_net"] / s["equity"] * 100
    s["capped"] = False
    return s[s["equity"] > 0]


def _copy_set(task):
    """Worker: copy one set of leaders for one month; returns the portfolio return in %."""
    addrs, t0, t1, capital, symbols, fund, costs, dfs, eqs = task
    prices = Prices({c: store.load(s) for c, s in symbols.items()})
    if not addrs:
        return 0.0
    res = [copy_leader(dfs[a], t0, t1, eqs[a], capital / len(addrs), prices, fund, costs) for a in addrs]
    return (sum(r.equity.iloc[-1] for r in res) / capital - 1) * 100


def run(first: str = "2025-10-27", months: int = 12, k: int = 10, lookback: int = 90, capital: float = 10_000.0,
        placebo_draws: int = 200, data_start: str = "2025-07-27", workers: int | None = None) -> dict:
    from .skill import select_skill

    t_start = time.time()
    workers = workers or max(1, (os.cpu_count() or 2) - 2)
    p1 = load_p1()
    cand = json.loads((BULK / "candidates.json").read_text())
    dfs = load_p2(set(cand["wallets"]))
    symbols = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, symbols))}
    pers = periods(first, months)
    ds = int(pd.Timestamp(data_start, tz="UTC").timestamp() * 1000)
    print(f"{len(dfs):,} candidate wallets with fills, {p1['user'].nunique():,} wallets in pass 1, {len(pers)} months", flush=True)

    # Exposure proxies at every period start (and at the data start, for the skill finder's history replay).
    points = [(t0, max(ds, t0 - lookback * DAY)) for t0, _ in pers]
    tasks = [(a, df, [(ds, ds), *points]) for a, df in dfs.items()]
    with ProcessPoolExecutor(workers) as pool:
        prox = dict(pool.map(_proxies, tasks, chunksize=64))
    for a, pts in prox.items():  # the data-start point uses the first lookback window
        pts[0] = (ds, exposure_proxy(dfs[a], ds, ds + lookback * DAY))
    hist = {a: [(t, v) for t, v in pts if v and not math.isnan(v)] for a, pts in prox.items()}
    print(f"exposure proxies done after {time.time() - t_start:.0f}s", flush=True)

    need = {"BTC"} | {c for df in dfs.values() for c in df["coin"].unique() if c in mapping}
    since = str((pd.Timestamp(data_start, tz="UTC") - pd.Timedelta(days=2)).date())
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda c: store.ensure(mapping[c], since), sorted(need)))
    with ThreadPoolExecutor(8) as pool:
        fund = dict(zip(sorted(need), pool.map(lambda c: binance_funding(mapping[c], ds, pers[-1][1]), sorted(need))))
    prices = Prices({c: store.load(mapping[c]) for c in need})
    days = np.arange(ds, pers[-1][1] + DAY, DAY)
    btc_open = pd.Series([prices.next_open("BTC", d) for d in days], index=pd.to_datetime(days, unit="ms", utc=True))
    btc_daily = btc_open.pct_change().shift(-1).dropna()
    print(f"prices + funding ready after {time.time() - t_start:.0f}s", flush=True)

    wallets = {a: (df, False, hist.get(a, []), np.array([], dtype="int64")) for a, df in dfs.items()}
    picks, skill_tables, stats_by = {}, {}, {}
    for pi, (t0, t1) in enumerate(pers):
        eq = pd.Series({a: next((v for t, v in hist.get(a, []) if t == t0), math.nan) for a in dfs})
        st = period_stats(p1, t0, lookback, eq.dropna())
        stats_by[pi] = st
        for mode in ("hot", "steady", "random"):
            picks[(mode, pi)] = select(mode, st, k, seed=pi)
        picks[("skill", pi)], skill_tables[pi] = select_skill(wallets, st, t0, ds, mapping, fund, btc_daily, k=15, workers=workers)
        n_disc = int(skill_tables[pi]["discovery"].sum()) if len(skill_tables[pi]) and "discovery" in skill_tables[pi] else 0
        print(f"  {pd.Timestamp(t0, unit='ms'):%Y-%m-%d}: {len(st):,} wallets ranked; skill tested {len(skill_tables[pi])}, "
              f"{n_disc} passed the luck test, picked {len(picks[('skill', pi)])}", flush=True)

    variants = {"leader raw": CopyCosts(ideal=True), "copy 10s": CopyCosts(delay_s=10),
                "copy 10s + 25% stop": CopyCosts(delay_s=10, kill_dd=0.25)}
    out = config.RUNS_DIR / "copylab" / ("big-" + pd.Timestamp.now().strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)

    def eq_at(a, t0):
        return next((v for t, v in hist.get(a, []) if t == t0), math.nan)

    tasks, keys = [], []
    for pi, (t0, t1) in enumerate(pers):
        active = sorted(stats_by[pi][stats_by[pi]["month_fills"] >= 3].index)
        rng = random.Random(1000 + pi)
        sets = {mode: picks[(mode, pi)] for mode in ("hot", "steady", "random", "skill")}
        for d in range(placebo_draws):
            sets[f"placebo{d}"] = rng.sample(active, min(k, len(active)))
        for name, addrs in sets.items():
            coins = {c for a in addrs for c in dfs[a]["coin"].unique() if c in mapping} | {"BTC"}
            sub_dfs = {a: dfs[a] for a in addrs}
            eqs = {a: eq_at(a, t0) for a in addrs}
            for vname, vc in variants.items():
                if name.startswith("placebo") and vname == "leader raw":
                    continue
                tasks.append((addrs, t0, t1, capital, {c: mapping[c] for c in coins}, {c: fund.get(c, {}) for c in coins}, vc, sub_dfs, eqs))
                keys.append((pi, name, vname))
    print(f"copying {len(tasks):,} leader sets in parallel...", flush=True)
    with ProcessPoolExecutor(workers) as pool:
        rets = list(pool.map(_copy_set, tasks, chunksize=8))
    res = pd.DataFrame([(pd.Timestamp(pers[pi][0], unit="ms").strftime("%Y-%m-%d"), n, v, r) for (pi, n, v), r in zip(keys, rets)],
                       columns=["period", "set", "variant", "return_%"])
    res.to_csv(out / "returns.csv", index=False)
    btc = pd.DataFrame([{"period": pd.Timestamp(t0, unit="ms").strftime("%Y-%m-%d"),
                         "btc_hold_%": ((prices.next_open("BTC", t1) / prices.next_open("BTC", t0)) * (1 - hl.TAKER_FEE) ** 2 - 1) * 100}
                        for t0, t1 in pers])
    btc.to_csv(out / "btc.csv", index=False)
    (out / "picks.json").write_text(json.dumps({f"{m}|{pi}": v for (m, pi), v in picks.items()}, indent=1))
    for pi, t in skill_tables.items():
        if len(t):
            t.to_csv(out / f"skill_scores_{pi}.csv")
    print(f"done in {(time.time() - t_start) / 60:.0f} min, saved to {out}", flush=True)
    return {"returns": res, "btc": btc, "dir": out}


def summarize(res: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    """Compounded totals per finder, and where each finder ranks among the random placebo sets."""
    comp = lambda r: ((1 + r / 100).prod() - 1) * 100
    tot = res.groupby(["set", "variant"])["return_%"].apply(comp).unstack()
    plac = tot[tot.index.str.startswith("placebo")]
    rows = []
    for name in ("hot", "steady", "random", "skill"):
        for v in tot.columns:
            val = tot.loc[name, v] if name in tot.index else math.nan
            pct = (plac[v] < val).mean() * 100 if v in plac and plac[v].notna().any() else math.nan
            rows.append({"finder": name, "how": v, "total_%": val, "beats_%_of_random_sets": pct})
    for v in plac.columns:
        if plac[v].notna().any():
            rows.append({"finder": "random sets (median)", "how": v, "total_%": plac[v].median(),
                         "beats_%_of_random_sets": 50.0})
    rows.append({"finder": "hold BTC", "how": "-", "total_%": comp(btc["btc_hold_%"])})
    return pd.DataFrame(rows)
