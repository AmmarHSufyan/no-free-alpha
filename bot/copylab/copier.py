"""The copier: replay following one leader from T to T_end, the way a real copy bot would have done it.

How a copied trade is priced:
  * the leader's fill at time t is noticed after `delay_s` seconds and executed at the open of the
    next 1-minute candle (so a 10 s delay really means 10-70 s),
  * every price we trade or mark at is the Binance futures price at that moment - one price scale
    throughout. (An earlier version scaled the leader's Hyperliquid fill price by Binance's move from the
    minute's open; Hyperliquid and Binance prices drift 3-16% apart on coins like HYPE/PUMP and the
    minute-open reference flattered dip buyers, inflating copy returns. Fixed 2026-10-26.)
  * `ideal` ("leader raw") = no delay beyond the next minute open and no costs,
  * plus slippage, plus Hyperliquid's taker fee; open positions pay/receive hourly funding,
  * our size = leader size x (our allocation / leader's account value at T), never above `lev_cap`
    times our equity. Equity at or below zero = liquidated.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .hyperliquid import TAKER_FEE

MIN = 60_000
HOUR = 3_600_000


@dataclass(frozen=True)
class CopyCosts:
    delay_s: int = 10
    fee: float = TAKER_FEE
    slippage: float = 0.0003
    lev_cap: float = 3.0
    ideal: bool = False  # True = copy at the leader's exact prices with no delay and no costs (the leader's raw edge)
    kill_dd: float | None = None  # safety stop: stop copying this leader once we're this far below our peak
    # Waiting (maker) orders: post at the current price; filled only if a later minute's price goes strictly
    # through it within maker_wait_min (so we only get filled when the market moves against us, like real
    # limit orders). Otherwise "chase" (pay the taker price then) or "skip" the trade.
    maker: bool = False
    maker_fee: float = 0.00015
    maker_wait_min: int = 5
    maker_fallback: str = "chase"


class Prices:
    """1-minute candle opens per Hyperliquid coin, as numpy arrays for fast lookups.

    Built from DataFrames (tests) or from (times_ms, opens) array pairs (the memory-mapped store).
    """

    def __init__(self, series: dict[str, pd.DataFrame | tuple[np.ndarray, np.ndarray]]):
        self.t, self.o = {}, {}
        for coin, s in series.items():
            if isinstance(s, pd.DataFrame):
                self.t[coin], self.o[coin] = s.index.as_unit("ms").asi8, s["open"].to_numpy()
            else:
                self.t[coin], self.o[coin] = s

    def __contains__(self, coin: str) -> bool:
        return coin in self.t

    def next_open(self, coin: str, ms: int) -> float:
        """Open of the first candle starting at or after ms."""
        i = min(np.searchsorted(self.t[coin], ms, "left"), len(self.t[coin]) - 1)
        return float(self.o[coin][i])

    def current_open(self, coin: str, ms: int) -> float:
        """Open of the candle that contains ms."""
        i = max(np.searchsorted(self.t[coin], ms, "right") - 1, 0)
        return float(self.o[coin][i])

    def crosses(self, coin: str, ms: int, minutes: int, level: float, buy: bool) -> bool:
        """Does a minute open after ms (within `minutes`) trade strictly through `level`?"""
        t, o = self.t[coin], self.o[coin]
        i0, i1 = np.searchsorted(t, ms + 60_000, "left"), np.searchsorted(t, ms + minutes * 60_000, "right")
        window = o[i0:i1]
        return bool((window < level).any() if buy else (window > level).any())


@dataclass
class CopyResult:
    equity: pd.Series
    trades: int
    fees: float
    slippage: float
    funding: float
    liquidated: bool

    @property
    def ret(self) -> float:
        return self.equity.iloc[-1] / self.equity.iloc[0] - 1


def copy_leader(fills: pd.DataFrame, t0: int, t1: int, leader_equity: float, alloc: float, prices: Prices,
                funding: dict[str, dict[int, float]], costs: CopyCosts = CopyCosts()) -> CopyResult:
    scale = alloc / leader_equity
    ideal = costs.ideal
    fee = 0.0 if ideal else costs.fee
    slip = 0.0 if ideal else costs.slippage

    # Leader's open positions at t0 (from the last fill before t0), then every fill during the window.
    before = fills[fills["time"] < t0].groupby("coin").tail(1)
    events = [(t0, c, sp + sg, None, t0) for c, sp, sg in zip(before["coin"], before["startPosition"], before["signed"])
              if c in prices and abs(sp + sg) > 0]
    during = fills[(fills["time"] >= t0) & (fills["time"] < t1) & fills["coin"].isin(list(prices.t))]
    for t, c, sp, sg, px in zip(during["time"], during["coin"], during["startPosition"], during["signed"], during["px"]):
        exec_ms = int(t) if ideal else int(-(-(int(t) + costs.delay_s * 1000) // MIN) * MIN)
        events.append((exec_ms, c, sp + sg, px, int(t)))
    events.sort(key=lambda e: (e[0], e[4]))

    cash, pos, last_px = alloc, {}, {}
    n_trades, fees_paid, slip_paid, funding_paid, dead = 0, 0.0, 0.0, 0.0, False
    peak = alloc
    curve_t, curve_v = [t0], [alloc]

    def equity() -> float:
        return cash + sum(q * last_px[c] for c, q in pos.items())

    def trade(coin: str, target: float, px: float, at_ms: int | None = None) -> None:
        nonlocal cash, n_trades, fees_paid, slip_paid
        delta = target - pos.get(coin, 0.0)
        if abs(delta) * px < 1e-6:
            return
        f, s = fee, slip
        if costs.maker and not ideal and at_ms is not None:
            if prices.crosses(coin, at_ms, costs.maker_wait_min, px, buy=delta > 0):
                f, s = costs.maker_fee, 0.0  # filled at our posted price
            elif costs.maker_fallback == "skip":
                return
            else:  # chase: give up waiting and take the price at the deadline
                px = prices.next_open(coin, at_ms + costs.maker_wait_min * 60_000)
        fill = px * (1 + s * np.sign(delta))
        cash -= delta * fill + abs(delta) * fill * f
        fees_paid += abs(delta) * fill * f
        slip_paid += abs(delta) * px * s
        pos[coin] = target
        n_trades += 1

    def apply(group: list) -> None:
        # Several leader fills landing in the same execution minute are netted into one order per coin.
        latest = {}
        for exec_ms, coin, leader_pos, leader_px, leader_ms in group:
            latest[coin] = (leader_pos, prices.next_open(coin, exec_ms))
        at_ms = group[0][0]
        for coin, (leader_pos, px) in latest.items():
            last_px[coin] = px
            target = leader_pos * scale
            others = sum(abs(q) * last_px[c] for c, q in pos.items() if c != coin)
            room = max(costs.lev_cap * equity() - others, 0.0)
            if abs(target) * px > room:
                target = np.sign(target) * room / px
            trade(coin, target, px, at_ms)

    i = 0
    for h in [*range(-(-t0 // HOUR) * HOUR, t1, HOUR), t1]:
        group = []
        while i < len(events) and events[i][0] < h:
            if group and events[i][0] != group[0][0]:
                apply(group)
                group = []
            group.append(events[i])
            i += 1
        if group:
            apply(group)
        if h >= t1:
            break
        for coin, q in pos.items():
            last_px[coin] = prices.next_open(coin, h)
            if q and not ideal:
                rate = funding.get(coin, {}).get(h, 0.0)
                cash -= q * last_px[coin] * rate
                funding_paid += q * last_px[coin] * rate
        eq = equity()
        if eq <= 0:
            dead, cash, pos = True, 0.0, {}
            curve_t.append(h)
            curve_v.append(0.0)
            break
        curve_t.append(h)
        curve_v.append(eq)
        peak = max(peak, eq)
        if costs.kill_dd and eq < peak * (1 - costs.kill_dd):
            for coin in list(pos):
                trade(coin, 0.0, prices.next_open(coin, h))
            break

    if not dead:  # close everything at the end of the window
        for coin in list(pos):
            trade(coin, 0.0, prices.next_open(coin, t1))
        curve_t.append(t1)
        curve_v.append(cash)
    eq = pd.Series(curve_v, index=pd.to_datetime(curve_t, unit="ms", utc=True))
    return CopyResult(eq[~eq.index.duplicated(keep="last")], n_trades, fees_paid, slip_paid, funding_paid, dead)
