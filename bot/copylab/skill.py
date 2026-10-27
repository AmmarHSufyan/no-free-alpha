"""The skill finder: look for wallets whose edge would have survived *our* copying, beyond luck.

For every candidate wallet at selection time T, using only data before T:
  1. Behaviour checks that flag gamblers and uncopyable styles: liquidations, averaging down into
     losers (martingale), profit that comes from 1-3 lucky trades, high-frequency trading, hedging.
  2. Replay copying the wallet through our own copier over its history (delay, fees, slippage,
     3x cap): this is the return *we* would have had, not the wallet's own.
  3. Remove the market's own move: regress our daily copy returns on BTC's daily returns; only the
     part BTC doesn't explain (alpha) counts as skill.
  4. Luck test: a block bootstrap (5,000 resamples) gives each wallet's chance of an alpha this
     good with zero true skill; Benjamini-Hochberg then controls false discoveries across all
     wallets tested at once.
  5. Shrinkage: alphas measured over few days or with noisy returns are pulled toward the average
     (empirical Bayes), so extreme lucky numbers don't win the ranking.
"""
from __future__ import annotations

import math
import os
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import store
from .copier import CopyCosts, Prices, copy_leader
from .finder import DAY

SELECTION_COSTS = CopyCosts(delay_s=10)


def behaviour(df: pd.DataFrame, addr: str, lo: int, hi: int) -> dict:
    w = df[(df["time"] >= lo) & (df["time"] < hi)]
    closes = w.loc[w["closedPnl"] != 0, "net"]
    wins = closes[closes > 0].sort_values(ascending=False)
    if "liq" in w:  # bulk data: boolean flag already computed
        liquidated = bool(w["liq"].any())
    else:
        liq = w["liquidation"] if "liquidation" in w else pd.Series(dtype=object)
        liquidated = any(isinstance(x, dict) and str(x.get("liquidatedUser", "")).lower() == addr.lower() for x in liq)
    return {
        "active_days": int((w["time"] // DAY).nunique()),
        "closes": int(len(closes)),
        "fills_per_day": len(w) / max((hi - lo) / DAY, 1),
        "maker_share": float(1 - w["crossed"].astype(bool).mean()) if len(w) else 0.0,
        "top3_share": float(wins.iloc[:3].sum() / wins.sum()) if wins.sum() > 0 else 1.0,
        "martingale": martingale_share(w),
        "liquidated": liquidated,
    }


def martingale_share(w: pd.DataFrame) -> float:
    """Share of position increases made while that position was losing (averaging down)."""
    adds = underwater = 0
    for _, g in w.groupby("coin", sort=False):
        pos, avg = 0.0, 0.0
        for sp, sg, px in zip(g["startPosition"], g["signed"], g["px"]):
            new = sp + sg
            if abs(sp - pos) > 1e-9 * max(abs(sp), 1):  # history gap: we don't know the entry price
                pos, avg = sp, px
            if sp != 0 and np.sign(new) == np.sign(sp) and abs(new) > abs(sp):
                adds += 1
                underwater += (px - avg) * np.sign(sp) < 0
                avg = (avg * abs(sp) + px * abs(sg)) / abs(new)
            elif np.sign(new) != np.sign(sp) and new != 0:
                avg = px
            elif sp == 0:
                avg = px
            pos = new
    return underwater / adds if adds else 0.0


def block_bootstrap_p(x: np.ndarray, n_boot: int = 5000, block: int = 5, seed: int = 0) -> float:
    """One-sided p-value that mean(x) > 0 under 'no skill', resampling blocks of days to keep streaks."""
    n = len(x)
    if n < 10 or x.std() == 0:
        return 1.0
    rng = np.random.default_rng(seed)
    centred = x - x.mean()
    nblocks = math.ceil(n / block)
    starts = rng.integers(0, n, size=(n_boot, nblocks))
    idx = (starts[:, :, None] + np.arange(block)) % n  # circular blocks
    means = centred[idx.reshape(n_boot, -1)[:, :n]].mean(axis=1)
    return float((1 + (means >= x.mean()).sum()) / (n_boot + 1))


def copy_stream_stats(task: dict) -> dict | None:
    """Worker: replay copying one wallet over [lo, hi) and test the result. Runs in a separate process."""
    df, lo, hi, eq0 = task["df"], task["lo"], task["hi"], task["equity"]
    prices = Prices({c: store.load(s) for c, s in task["symbols"].items()})
    res = copy_leader(df, lo, hi, eq0, 10_000.0, prices, task["funding"], SELECTION_COSTS)
    daily = res.equity.resample("1D").last().dropna()
    if len(daily) < 30:
        return None
    r = daily.pct_change().dropna().clip(lower=-1)
    btc = task["btc_daily"].reindex(r.index).fillna(0.0)
    X = np.column_stack([np.ones(len(r)), btc.to_numpy()])
    coef, *_ = np.linalg.lstsq(X, r.to_numpy(), rcond=None)
    resid = r.to_numpy() - X @ coef
    s2 = resid @ resid / max(len(r) - 2, 1)
    se_alpha = math.sqrt(s2 * np.linalg.inv(X.T @ X)[0, 0])
    adj = r.to_numpy() - coef[1] * btc.to_numpy()  # our copy return with BTC's part removed
    peak = np.maximum.accumulate(daily.to_numpy())
    return {
        "addr": task["addr"], "days": len(r), "copy_ret_%": (daily.iloc[-1] / daily.iloc[0] - 1) * 100,
        "alpha_daily": float(coef[0]), "alpha_se": se_alpha, "beta": float(coef[1]),
        "alpha_t": float(coef[0] / se_alpha) if se_alpha > 0 else 0.0,
        "p_boot": block_bootstrap_p(adj, seed=int(task["addr"][2:10], 16)),
        "copy_max_dd_%": float((daily.to_numpy() / peak - 1).min() * 100), "copy_liquidated": res.liquidated,
    }


def benjamini_hochberg(p: pd.Series, q: float) -> pd.Series:
    """True where the wallet passes the false-discovery-rate test at level q."""
    order = p.sort_values()
    m = len(order)
    passed = order.to_numpy() <= q * np.arange(1, m + 1) / m
    cutoff = np.nonzero(passed)[0].max() + 1 if passed.any() else 0
    return pd.Series(np.arange(m) < cutoff, index=order.index).reindex(p.index)


def shrink(alpha: pd.Series, se: pd.Series) -> pd.Series:
    """Empirical-Bayes shrinkage: noisy estimates move most of the way back to the group average."""
    mean = alpha.mean()
    tau2 = max(alpha.var() - (se ** 2).mean(), 1e-12)
    return mean + (alpha - mean) * tau2 / (tau2 + se ** 2)


def select_skill(wallets: dict, stats: pd.DataFrame, t0: int, data_start: int, mapping: dict, funding: dict,
                 btc_daily: pd.Series, k: int = 15, q: float = 0.10, workers: int | None = None) -> tuple[list[str], pd.DataFrame]:
    from .finder import equity_at

    base = stats[(~stats["capped"]) & (stats["supported_share"] >= 0.7) & (stats["equity"] >= 1_000)
                 & (stats["spot_share"] <= 0.1) & (stats["fills_per_day"] <= 60)]
    tasks, beh = [], {}
    for addr in base.index:
        df, _, hist, _ = wallets[addr]
        lo = data_start
        eq = equity_at(hist, lo)
        if not eq or math.isnan(eq) or eq <= 0:
            later = [(t, v) for t, v in hist if lo <= t < t0 and v > 0]
            if not later:
                continue
            lo, eq = later[0]
        b = behaviour(df, addr, lo, t0)
        beh[addr] = b
        if (b["liquidated"] or b["active_days"] < 30 or b["closes"] < 20 or b["top3_share"] > 0.6 or b["martingale"] > 0.5):
            continue
        coins = set(df.loc[df["time"] < t0, "coin"]) & set(mapping)
        tasks.append({"addr": addr, "df": df[df["time"] < t0], "lo": lo, "hi": t0, "equity": eq,
                      "symbols": {c: mapping[c] for c in coins}, "funding": {c: funding.get(c, {}) for c in coins},
                      "btc_daily": btc_daily})
    with ProcessPoolExecutor(workers or max(1, (os.cpu_count() or 2) - 2)) as pool:
        scored = [s for s in pool.map(copy_stream_stats, tasks, chunksize=2) if s]
    table = pd.DataFrame(scored).set_index("addr") if scored else pd.DataFrame()
    info = pd.DataFrame.from_dict(beh, orient="index")
    if table.empty:
        return [], info
    table["shrunk_alpha"] = shrink(table["alpha_daily"], table["alpha_se"])
    table["discovery"] = benjamini_hochberg(table["p_boot"], q)
    table = table.join(info, how="left")
    picks = table[table["discovery"] & (table["shrunk_alpha"] > 0) & ~table["copy_liquidated"]]
    return list(picks.sort_values("shrunk_alpha", ascending=False).index[:k]), table
