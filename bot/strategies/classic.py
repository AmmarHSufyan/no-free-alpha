"""Well-known textbook strategies, written against our Strategy interface."""
from __future__ import annotations

import pandas as pd

from .. import indicators as ind
from .base import Strategy


class BuyAndHold(Strategy):
    """The benchmark every other strategy has to beat."""

    name = "buy_hold"

    def signals(self, df, start=0):
        return self.frame(df, pd.Series(True, index=df.index), pd.Series(False, index=df.index))


class EmaCross(Strategy):
    """Trend following: hold while the fast EMA is above the slow EMA."""

    name = "ema_cross"
    defaults = {"fast": 20, "slow": 100}
    param_grid = {"fast": [10, 20, 50], "slow": [50, 100, 200]}

    @property
    def warmup(self):
        return self.p["slow"] * 3

    def signals(self, df, start=0):
        fast, slow = ind.ema(df["close"], self.p["fast"]), ind.ema(df["close"], self.p["slow"])
        return self.frame(df, fast > slow, fast < slow)


class RsiReversion(Strategy):
    """Mean reversion: buy oversold dips (optionally only in an uptrend), sell on recovery."""

    name = "rsi_reversion"
    defaults = {"n": 14, "buy_below": 30, "sell_above": 60, "trend": 200, "stoploss": 0.10}
    param_grid = {"buy_below": [25, 30, 35], "sell_above": [50, 60, 70], "trend": [0, 200], "stoploss": [0.05, 0.10]}

    @property
    def warmup(self):
        return max(self.p["n"], self.p["trend"]) * 3

    def signals(self, df, start=0):
        r = ind.rsi(df["close"], self.p["n"])
        enter = r < self.p["buy_below"]
        if self.p["trend"]:
            enter &= df["close"] > ind.ema(df["close"], self.p["trend"])
        return self.frame(df, enter, r > self.p["sell_above"])


class BollingerReversion(Strategy):
    """Mean reversion: buy a close below the lower band, sell back at the middle band."""

    name = "bollinger"
    defaults = {"n": 20, "k": 2.0, "stoploss": 0.10}
    param_grid = {"n": [20, 50], "k": [1.5, 2.0, 2.5], "stoploss": [0.05, 0.10]}

    @property
    def warmup(self):
        return self.p["n"]

    def signals(self, df, start=0):
        lower, mid, _ = ind.bollinger(df["close"], self.p["n"], self.p["k"])
        return self.frame(df, df["close"] < lower, df["close"] > mid)


class DonchianBreakout(Strategy):
    """Turtle-style breakout: buy a new N-candle high, sell a new M-candle low."""

    name = "donchian"
    defaults = {"entry": 55, "exit": 20}
    param_grid = {"entry": [20, 55, 100], "exit": [10, 20, 50]}

    @property
    def warmup(self):
        return max(self.p["entry"], self.p["exit"]) + 1

    def signals(self, df, start=0):
        upper, _ = ind.donchian(df, self.p["entry"])
        _, lower = ind.donchian(df, self.p["exit"])
        return self.frame(df, df["close"] > upper, df["close"] < lower)


class MacdTrend(Strategy):
    """Momentum: MACD above its signal line, filtered by a long EMA trend."""

    name = "macd"
    defaults = {"fast": 12, "slow": 26, "signal": 9, "trend": 200}
    param_grid = {"fast": [8, 12], "slow": [26, 50], "trend": [0, 100, 200]}

    @property
    def warmup(self):
        return max(self.p["slow"], self.p["trend"]) * 3

    def signals(self, df, start=0):
        line, sig = ind.macd(df["close"], self.p["fast"], self.p["slow"], self.p["signal"])
        enter = line > sig
        if self.p["trend"]:
            enter &= df["close"] > ind.ema(df["close"], self.p["trend"])
        return self.frame(df, enter, line < sig)
