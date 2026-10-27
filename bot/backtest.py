"""Bar-by-bar backtest engine (long-only spot) and an equal-weight multi-pair portfolio.

Timing model, which is what keeps backtests honest:
  * a strategy looks at candle t once it has closed and emits enter/exit,
  * the order is filled at the open of candle t+1, worse by `slippage`, minus `fee`,
  * stop-loss / take-profit are checked against each candle's low / high; if a candle gaps
    through the stop, the fill is the (worse) open; if both could trigger, the stop wins.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config
from .strategies.base import Strategy


@dataclass(frozen=True)
class Costs:
    fee: float = config.FEE
    slippage: float = config.SLIPPAGE


@dataclass
class Trade:
    pair: str
    entry_time: object
    entry_price: float
    exit_time: object
    exit_price: float
    qty: float
    cost: float      # cash spent, including the entry fee
    proceeds: float  # cash received, after the exit fee
    reason: str      # signal | stoploss | takeprofit | end

    @property
    def pnl(self) -> float:
        return self.proceeds - self.cost

    @property
    def ret(self) -> float:
        return self.proceeds / self.cost - 1


@dataclass
class Account:
    """Cash plus at most one open long position. Used by both the backtester and the paper trader."""

    cash: float
    fee: float = config.FEE
    slippage: float = config.SLIPPAGE
    stoploss: float | None = None
    takeprofit: float | None = None
    qty: float = 0.0
    entry_time: object = None
    entry_price: float = 0.0
    entry_cost: float = 0.0
    fees_paid: float = 0.0
    slippage_paid: float = 0.0

    @property
    def in_position(self) -> bool:
        return self.qty > 0

    @property
    def stop_price(self) -> float | None:
        return self.entry_price * (1 - self.stoploss) if self.stoploss and self.in_position else None

    @property
    def tp_price(self) -> float | None:
        return self.entry_price * (1 + self.takeprofit) if self.takeprofit and self.in_position else None

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def buy(self, price: float, time) -> None:
        px = price * (1 + self.slippage)
        fee = self.cash * self.fee
        self.qty = (self.cash - fee) / px
        self.entry_time, self.entry_price, self.entry_cost = time, px, self.cash
        self.fees_paid += fee
        self.slippage_paid += self.qty * (px - price)
        self.cash = 0.0

    def sell(self, price: float, time, reason: str, pair: str = "") -> Trade:
        px = price * (1 - self.slippage)
        gross = self.qty * px
        fee = gross * self.fee
        trade = Trade(pair, self.entry_time, self.entry_price, time, px, self.qty, self.entry_cost, gross - fee, reason)
        self.cash += gross - fee
        self.fees_paid += fee
        self.slippage_paid += self.qty * (price - px)
        self.qty, self.entry_time, self.entry_price, self.entry_cost = 0.0, None, 0.0, 0.0
        return trade


@dataclass
class Result:
    equity: pd.Series  # mark-to-market value at each candle close
    trades: list[Trade]
    exposure: float    # fraction of candles spent holding a position
    fees: float        # commission paid, in quote currency
    slippage: float    # value lost to spread / price movement on fills, in quote currency
    capital: float
    per_pair: dict[str, "Result"] = field(default_factory=dict)


def simulate(df: pd.DataFrame, sig: pd.DataFrame, capital: float, costs: Costs = Costs(),
             stoploss: float | None = None, takeprofit: float | None = None, pair: str = "") -> Result:
    """Run one pair through the window covered by df (signals already computed with full history)."""
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    enter, exit_ = sig["enter"].to_numpy(), sig["exit"].to_numpy()
    idx = df.index
    acct = Account(capital, costs.fee, costs.slippage, stoploss, takeprofit)
    trades: list[Trade] = []
    equity = np.empty(len(df))
    pending, bars_in = None, 0

    for i in range(len(df)):
        if pending == "buy":
            acct.buy(o[i], idx[i])
        elif pending == "sell":
            trades.append(acct.sell(o[i], idx[i], "signal", pair))
        pending = None

        if acct.in_position:
            stop, tp = acct.stop_price, acct.tp_price
            if stop is not None and l[i] <= stop:
                trades.append(acct.sell(min(o[i], stop), idx[i], "stoploss", pair))
            elif tp is not None and h[i] >= tp:
                trades.append(acct.sell(max(o[i], tp), idx[i], "takeprofit", pair))

        if acct.in_position:
            bars_in += 1
            if exit_[i]:
                pending = "sell"
        elif enter[i] and not exit_[i]:
            pending = "buy"
        equity[i] = acct.equity(c[i])

    # Liquidate at the end so open positions pay their exit costs like everything else.
    if acct.in_position:
        trades.append(acct.sell(c[-1], idx[-1], "end", pair))
        equity[-1] = acct.cash

    return Result(pd.Series(equity, index=idx), trades, bars_in / len(df), acct.fees_paid, acct.slippage_paid, capital)


def window(df: pd.DataFrame, start=None, end=None) -> tuple[pd.DataFrame, int]:
    """Cut df at `end` (exclusive) and return it with the row index where `start` begins."""
    hist = df.iloc[: df.index.searchsorted(pd.Timestamp(end, tz="UTC"))] if end else df
    i0 = hist.index.searchsorted(pd.Timestamp(start, tz="UTC")) if start else 0
    return hist, i0


def backtest_pair(df: pd.DataFrame, strategy: Strategy, start=None, end=None, capital: float = config.CAPITAL,
                  costs: Costs = Costs(), pair: str = "") -> Result:
    hist, i0 = window(df, start, end)
    if i0 >= len(hist):
        raise ValueError(f"{pair}: no candles between {start} and {end}")
    # The strategy sees all history up to `end` (indicator warm-up), but never anything after it.
    sig = strategy.signals(hist, start=i0)
    return simulate(hist.iloc[i0:], sig.iloc[i0:], capital, costs, strategy.stoploss, strategy.takeprofit, pair)


def backtest(data: dict[str, pd.DataFrame], strategy: Strategy, start=None, end=None,
             capital: float = config.CAPITAL, costs: Costs = Costs()) -> Result:
    """Equal-weight portfolio: each pair trades its own capital / len(data) slice."""
    per = capital / len(data)
    results = {pair: backtest_pair(df, strategy, start, end, per, costs, pair) for pair, df in data.items()}
    eq = pd.concat({p: r.equity for p, r in results.items()}, axis=1).sort_index().ffill()
    eq = eq.fillna(per)  # a pair listed after `start` keeps its slice in cash until its first candle
    trades = sorted((t for r in results.values() for t in r.trades), key=lambda t: t.entry_time)
    exposure = float(np.mean([r.exposure for r in results.values()]))
    fees = sum(r.fees for r in results.values())
    slippage = sum(r.slippage for r in results.values())
    return Result(eq.sum(axis=1), trades, exposure, fees, slippage, capital, results)
