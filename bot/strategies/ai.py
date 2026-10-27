"""AI strategy: Claude reads an anonymized summary of recent candles and answers "long" or "flat".

The asset name and dates are hidden and prices are rebased so the latest close is 100. Without
that, a backtest on 2024-2026 data would partly measure what the model remembers about those
years instead of how well it reads a chart.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor

import pandas as pd

from .. import indicators as ind
from ..llm import ClaudeCLI
from .base import Strategy

SYSTEM = """You are the decision module of an automated, long-only spot trading system that is being evaluated with simulated money.
Each request describes one asset at one moment. The asset's identity and the dates are hidden on purpose, and prices are rebased so the latest close is 100; do not try to guess them.
Decide whether the account should hold the asset (stance "long") or hold cash (stance "flat") until the next decision.
A round trip costs about 0.3% in fees and slippage, so only choose "long" when you expect a move clearly larger than that. "flat" is a perfectly good answer.
Base the decision only on the data given. Give a confidence between 0 and 1 and a one-sentence reason."""

SCHEMA = {
    "type": "object",
    "properties": {
        "stance": {"type": "string", "enum": ["long", "flat"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": ["stance", "confidence", "reason"],
    "additionalProperties": False,
}

HISTORY = 600  # candles fed into the indicator calculations for each decision


class TooManyCalls(RuntimeError):
    pass


def _pct(x: float) -> str:
    return "n/a" if pd.isna(x) else f"{x:+.2f}%"


def describe(df: pd.DataFrame, window: int = 48, horizon: int = 1) -> str:
    """Anonymized prompt describing the last candle of df. Uses nothing outside df."""
    close, last = df["close"], df["close"].iloc[-1]
    step = df.index[-1] - df.index[-2]
    scale = 100 / last
    vol_ratio = df["volume"] / df["volume"].rolling(20).mean()
    recent = df.iloc[-window:]
    rows = [
        f"{o * scale:.2f} {h * scale:.2f} {l * scale:.2f} {c * scale:.2f} {v:.1f}"
        for o, h, l, c, v in zip(recent["open"], recent["high"], recent["low"], recent["close"], vol_ratio.iloc[-window:])
    ]
    changes = " / ".join(_pct((last / close.iloc[-1 - n] - 1) * 100 if len(close) > n else math.nan) for n in (1, 6, 24, 72, 168))
    emas = " / ".join(_pct((last / ind.ema(close, n).iloc[-1] - 1) * 100) for n in (20, 50, 200))
    month = max(1, int(pd.Timedelta(days=30) / step))
    hi, lo = df["high"].iloc[-month:].max() * scale, df["low"].iloc[-month:].min() * scale
    hours = step / pd.Timedelta(hours=1)
    return "\n".join([
        f"Candle size: {hours:g} hour(s). Decision horizon: the next {horizon} candle(s).",
        f"Last {len(rows)} candles, oldest first. Columns: open high low close volume_vs_20_candle_average",
        *rows,
        "",
        "Summary at the latest close:",
        f"- change over the last 1 / 6 / 24 / 72 / 168 candles: {changes}",
        f"- RSI(14): {ind.rsi(close).iloc[-1]:.1f}",
        f"- latest close vs EMA20 / EMA50 / EMA200: {emas}",
        f"- ATR(14): {ind.atr(df).iloc[-1] * scale:.2f}% of price",
        f"- last-30-days range: high {hi:.2f}, low {lo:.2f} (latest close = 100)",
        "",
        "Should the account be long or flat?",
    ])


class ClaudeTrader(Strategy):
    name = "ai"
    defaults = {"model": "haiku", "window": 48, "every": 1, "min_confidence": 0.0, "max_calls": 300, "workers": 4}
    warmup = 250

    def __init__(self, **params):
        super().__init__(**params)
        self.llm: ClaudeCLI | None = None
        self.decisions: list[tuple[pd.Timestamp, dict]] = []

    def signals(self, df, start=0):
        self.llm = self.llm or ClaudeCLI(model=self.p["model"])
        points = list(range(max(start, self.warmup), len(df), self.p["every"]))
        prompts = {t: describe(df.iloc[max(0, t + 1 - HISTORY): t + 1], self.p["window"], self.p["every"]) for t in points}
        new = sum(not self.llm.cached(SYSTEM, p, SCHEMA) for p in prompts.values())
        if new > self.p["max_calls"]:
            raise TooManyCalls(
                f"This needs {new} new Claude requests (max_calls={self.p['max_calls']}); each one uses a little of your "
                "Claude plan's usage limit. "
                "Use a later --start, a bigger timeframe, every=N, or raise max_calls.")
        with ThreadPoolExecutor(self.p["workers"]) as pool:
            answers = list(pool.map(lambda t: self.llm.ask(SYSTEM, prompts[t], SCHEMA), points))

        enter = pd.Series(False, index=df.index)
        exit_ = pd.Series(False, index=df.index)
        self.decisions = []
        for t, a in zip(points, answers):
            long = a["stance"] == "long" and a["confidence"] >= self.p["min_confidence"]
            (enter if long else exit_).iloc[t] = True
            self.decisions.append((df.index[t], a))
        return self.frame(df, enter, exit_)
