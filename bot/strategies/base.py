"""Strategy interface shared by the backtester, the paper trader and the lookahead checker."""
from __future__ import annotations

import pandas as pd


class Strategy:
    """A long-only spot strategy.

    `signals(df)` returns a DataFrame with boolean columns `enter` and `exit`, aligned to df.
    Row t is decided at the *close* of candle t and may only use rows <= t; the engine fills
    the order at the *open* of candle t+1. If both are true on the same row, exit wins.

    `stoploss` / `takeprofit` (fractions, e.g. 0.05 = 5%) are handled by the engine intrabar.
    """

    name = "base"
    defaults: dict = {}
    param_grid: dict = {}
    warmup = 0

    def __init__(self, **params):
        allowed = self.default_params()
        unknown = set(params) - set(allowed)
        if unknown:
            raise ValueError(f"{self.name}: unknown parameter(s) {sorted(unknown)}; allowed: {sorted(allowed)}")
        self.p = {**allowed, **params}

    @classmethod
    def default_params(cls) -> dict:
        return {**cls.defaults, "stoploss": cls.defaults.get("stoploss"), "takeprofit": cls.defaults.get("takeprofit")}

    @classmethod
    def is_valid(cls, params: dict) -> bool:
        """Lets the optimizer skip nonsensical grid combos (e.g. fast EMA slower than slow EMA)."""
        return params.get("fast", 0) < params.get("slow", float("inf"))

    @property
    def stoploss(self) -> float | None:
        return self.p["stoploss"]

    @property
    def takeprofit(self) -> float | None:
        return self.p["takeprofit"]

    @property
    def label(self) -> str:
        base = self.default_params()
        changed = [f"{k}={v}" for k, v in self.p.items() if v != base[k]]
        return f"{self.name}({','.join(changed)})" if changed else self.name

    def signals(self, df: pd.DataFrame, start: int = 0) -> pd.DataFrame:
        """`start` is a hint that rows before it won't be traded; expensive strategies may skip them."""
        raise NotImplementedError

    @staticmethod
    def frame(df: pd.DataFrame, enter: pd.Series, exit: pd.Series) -> pd.DataFrame:
        return pd.DataFrame({"enter": enter.fillna(False).astype(bool), "exit": exit.fillna(False).astype(bool)}, index=df.index)
