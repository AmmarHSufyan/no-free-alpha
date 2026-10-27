"""The finder: rank wallets at time T using only their fills *before* T, so the test can't peek ahead.

Three ways of choosing leaders, so they can be compared on the same data:
  hot     best % return over the last 7 days (the "who's hot right now" leaderboard approach)
  steady  profitable over ~3 months, in most months, with a luck test (t-stat of daily PnL >= 2)
  random  random active wallets (a control: a finder is only useful if it beats picking at random)
"""
from __future__ import annotations

import math
import random

import numpy as np
import pandas as pd

DAY = 86_400_000
MODES = ("hot", "steady", "random")


def perp_fills(rec: dict) -> pd.DataFrame:
    df = pd.DataFrame(rec["fills"])
    if df.empty:
        return df
    df = df[~df["coin"].str.startswith("@") & ~df["coin"].str.contains("/")].copy()  # drop spot fills
    for col in ("px", "sz", "startPosition", "closedPnl", "fee"):
        df[col] = df[col].astype(float)
    df["time"] = df["time"].astype("int64")
    df["signed"] = np.where(df["side"] == "B", df["sz"], -df["sz"])
    df["notional"] = df["px"] * df["sz"]
    df["net"] = df["closedPnl"] - df["fee"]
    return df.sort_values("time", kind="stable").reset_index(drop=True)


def equity_at(history: list[tuple[int, float]], t_ms: int) -> float:
    """Account value at the latest (roughly weekly) snapshot at or before t_ms."""
    value = math.nan
    for t, v in history:
        if t > t_ms:
            break
        value = v
    return value


def wallet_stats(df: pd.DataFrame, t_ms: int, lookback_days: int, supported: set[str], equity: float) -> dict | None:
    w = df[(df["time"] < t_ms) & (df["time"] >= t_ms - lookback_days * DAY)]
    if w.empty or not equity or equity <= 0 or math.isnan(equity):
        return None
    age = (t_ms - w["time"]) // DAY
    daily = w.groupby(age)["net"].sum().reindex(range(lookback_days), fill_value=0.0)
    months = w.groupby((t_ms - w["time"]) // (30 * DAY))["net"].sum()
    week = w[w["time"] >= t_ms - 7 * DAY]
    month = w[w["time"] >= t_ms - 30 * DAY]
    std = daily.std()
    total_notional = w["notional"].sum()
    return {
        "equity": equity,
        "net": daily.sum(),
        "ret_%": daily.sum() / equity * 100,
        "closes": int((w["closedPnl"] != 0).sum()),
        "fills_per_day": len(w) / lookback_days,
        "months_profitable": int((months > 0).sum()),
        "tstat": daily.mean() / std * math.sqrt(lookback_days) if std > 0 else 0.0,
        "sharpe": daily.mean() / std * math.sqrt(365) if std > 0 else 0.0,
        "supported_share": w.loc[w["coin"].isin(supported), "notional"].sum() / total_notional if total_notional else 0.0,
        "week_ret_%": week["net"].sum() / equity * 100,
        "week_closes": int((week["closedPnl"] != 0).sum()),
        "month_fills": len(month),
    }


def select(mode: str, stats: pd.DataFrame, k: int, seed: int = 0) -> list[str]:
    """`stats` is indexed by wallet address. Returns up to k addresses."""
    ok = stats[(~stats["capped"]) & (stats["supported_share"] >= 0.7) & (stats["equity"] >= 1_000)
               & (stats.get("spot_share", 0) <= 0.1)]
    if mode == "hot":
        c = ok[(ok["week_closes"] >= 3) & (ok["week_ret_%"] > 0)]
        return list(c.sort_values("week_ret_%", ascending=False).index[:k])
    if mode == "steady":
        c = ok[(ok["closes"] >= 30) & (ok["months_profitable"] >= 2) & (ok["net"] > 0)
               & (ok["tstat"] >= 2) & (ok["fills_per_day"] <= 60)]
        return list(c.sort_values("sharpe", ascending=False).index[:k])
    if mode == "random":
        c = sorted(ok[ok["month_fills"] >= 3].index)
        return random.Random(seed).sample(c, min(k, len(c)))
    raise ValueError(mode)
