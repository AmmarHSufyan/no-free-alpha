"""Slow daily trend rotation across many coins.

Data: `python -m bot.trend` downloads the daily candles (live coins from the exchange API, delisted coins from
data.binance.vision).

Hold a coin only while it trends up, size it by its risk, and trade rarely:
  * trend score per coin = share of its moving averages (20/50/100/200 days) the close is above (0..1),
  * weight = score x min(1, target_vol / the coin's recent volatility) / number of coins with data,
    so wild coins get less money and the total never exceeds 100% (no borrowing),
  * decided at the daily close, traded at the next day's open, Binance spot costs on every trade,
  * a coin is only traded when its position is more than `band` (e.g. 20%) away from its target, or when it
    must be entered from zero / exited to zero (keeps fees low). (Fixed 2026-10-27: the band used to be
    measured against the coin's fair share, which a target can never exceed, so large bands blocked entries.)
"""
from __future__ import annotations

import math
import sys
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from . import config, data

COINS = ["BTC", "ETH", "BNB", "XRP", "ADA", "SOL", "DOGE", "TRX", "LINK", "LTC", "BCH", "DOT", "AVAX", "XLM", "ATOM",
         "ETC", "UNI", "FIL", "NEAR", "ALGO", "VET", "EOS", "XTZ", "AAVE", "THETA", "ICP", "HBAR", "SAND", "MANA", "AXS"]
LOOKBACKS = (20, 50, 100, 200)


def panel(coins=COINS, field="close") -> pd.DataFrame:
    frames = {c: data.load(f"{c}/USDT", "1d")[field] for c in coins}
    return pd.DataFrame(frames).sort_index()


def trend_score(close: pd.DataFrame, lookbacks=LOOKBACKS) -> pd.DataFrame:
    """Share of moving averages the close is above; 0 until a coin has enough history for the longest one."""
    votes = sum((close > close.rolling(n, min_periods=n).mean()).astype(float) for n in lookbacks) / len(lookbacks)
    enough = close.notna().cumsum() >= max(lookbacks)
    return votes.where(enough, 0.0).fillna(0.0)


def target_weights(close: pd.DataFrame, score: pd.DataFrame, target_vol: float = 0.6, vol_days: int = 30,
                   universe: pd.DataFrame | None = None) -> pd.DataFrame:
    """`universe` (bool, same shape) limits trading to coins that are in the universe that day."""
    vol = close.pct_change(fill_method=None).rolling(vol_days).std() * math.sqrt(365)
    risk = (target_vol / vol).clip(upper=1.0).fillna(0.0)
    tradable = close.notna().cumsum() >= max(LOOKBACKS)  # coins old enough to trade
    if universe is not None:
        tradable &= universe
    n = tradable.sum(axis=1).clip(lower=1)
    return (score * risk).where(tradable, 0.0).div(n, axis=0).fillna(0.0)


DEAD = ["LUNA", "FTT", "MATIC", "FTM", "WAVES", "SRM", "OMG", "XEM"]  # delisted / collapsed / renamed coins


def point_in_time_panels(coins: list[str] = COINS + DEAD, top: int = 30, volume_days: int = 30):
    """Open/close panels including dead coins, plus a universe mask: each day the `top` coins by average daily
    dollar volume over the previous `volume_days` (what was big *then*, not what survived until now)."""
    o, c = panel(coins, "open"), panel(coins, "close")
    v = panel(coins, "volume")
    if "LUNA" in c:  # Binance reused the LUNA ticker for a new coin on 2022-05-31; the original died 2022-05-13
        cut = c.index > pd.Timestamp("2022-05-13", tz="UTC")
        o.loc[cut, "LUNA"] = c.loc[cut, "LUNA"] = v.loc[cut, "LUNA"] = np.nan
    dollar = (c * v).rolling(volume_days, min_periods=10).mean()
    universe = dollar.rank(axis=1, ascending=False, method="first") <= top
    return o, c, universe


def backtest(open_: pd.DataFrame, close: pd.DataFrame, target: pd.DataFrame, start: str, fee: float = config.FEE,
             slippage: float = config.SLIPPAGE, band: float = 0.2, capital: float = 10_000.0) -> dict:
    """Weights decided at day t's close are traded at day t+1's open."""
    idx = close.index[close.index >= pd.Timestamp(start, tz="UTC")]
    o, c, w = open_.loc[idx].to_numpy(), close.loc[idx].to_numpy(), target.shift(1).loc[idx].fillna(0).to_numpy()
    units, cash = np.zeros(c.shape[1]), capital
    equity, trades, fees_paid = np.empty(len(idx)), 0, 0.0
    for t in range(len(idx)):
        px = np.nan_to_num(o[t], nan=0.0)
        eq_open = cash + float(units @ px)
        want = w[t] * eq_open
        have = units * px
        diff = want - have
        size = np.maximum(np.abs(want), np.abs(have))
        do = (np.abs(diff) > band * size) & (px > 0) & (np.abs(diff) > 1e-9 * eq_open)
        do |= ((want == 0) & (have > 0) | (want > 0) & (have == 0)) & (px > 0)  # always enter from / exit to zero
        for i in np.nonzero(do)[0]:
            fill = px[i] * (1 + slippage * np.sign(diff[i]))
            qty = diff[i] / fill
            cost = abs(diff[i]) * fee
            units[i] += qty
            cash -= qty * fill + cost
            fees_paid += cost + abs(diff[i]) * slippage
            trades += 1
        equity[t] = cash + float(units @ np.nan_to_num(c[t], nan=0.0))
    eq = pd.Series(equity, index=idx)
    return {"equity": eq, "trades": trades, "costs": fees_paid, "exposure": float(np.mean(w.sum(axis=1)))}


def stats(eq: pd.Series, capital: float = 10_000.0) -> dict:
    curve = np.concatenate([[capital], eq.to_numpy()])
    years = (eq.index[-1] - eq.index[0]) / pd.Timedelta(days=365.25)
    total = curve[-1] / capital - 1
    r = np.diff(curve) / curve[:-1]
    return {"total_%": total * 100, "cagr_%": ((1 + total) ** (1 / years) - 1) * 100,
            "max_dd_%": (curve / np.maximum.accumulate(curve) - 1).min() * 100,
            "sharpe": r.mean() / r.std() * math.sqrt(365) if r.std() > 0 else math.nan}


def buy_and_hold(open_: pd.DataFrame, close: pd.DataFrame, coins: list[str], start: str, capital: float = 10_000.0,
                 fee: float = config.FEE, slippage: float = config.SLIPPAGE) -> pd.Series:
    """Benchmark: buy equal amounts of the coins that exist at the start, on the first open, and never trade."""
    idx = close.index[close.index >= pd.Timestamp(start, tz="UTC")]
    first = open_.loc[idx[0], coins].dropna()
    units = capital / len(first) * (1 - fee) / (first * (1 + slippage))
    return (close.loc[idx, first.index].ffill() * units).sum(axis=1)


def monthly_equal_weight(open_: pd.DataFrame, close: pd.DataFrame, start: str) -> pd.Series:
    """Benchmark: hold every tradable coin in equal weight, rebalanced on the 1st of each month (with costs)."""
    ok = close.notna().cumsum() >= max(LOOKBACKS)
    w = ok.div(ok.sum(axis=1).clip(lower=1), axis=0).astype(float)
    w = w.where(pd.Series(close.index.day == 1, index=close.index), np.nan).ffill().fillna(0.0)
    return backtest(open_, close, w, start, band=0.05)["equity"]


def download(since: str = "2019-06-01") -> None:
    """Daily candles for COINS (exchange API) and DEAD (no longer on the API: Binance's monthly bulk files)."""
    print(data.download([f"{c}/USDT" for c in COINS], "1d", since))
    months = pd.period_range(since[:7], pd.Timestamp.now(tz="UTC").to_period("M") - 1, freq="M")
    url = "{}/monthly/klines/{s}USDT/1d/{s}USDT-1d-{m}.zip"
    with ThreadPoolExecutor(16) as ex:
        for coin in DEAD:
            parts = [d for d in ex.map(lambda m: data._vision_month_or_day(url.format(data.VISION["binance"], s=coin, m=m)),
                                       months) if d is not None]
            df = pd.concat(parts).sort_index()
            df = df[~df.index.duplicated()]
            df.to_csv(data.cache_path("binance", f"{coin}/USDT", "1d"), index_label="ts")
            print(f"{coin}: {len(df)} days, {df.index[0].date()} .. {df.index[-1].date()}")


if __name__ == "__main__":
    download(*sys.argv[1:])
