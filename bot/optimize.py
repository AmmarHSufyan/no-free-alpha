"""Grid-search a strategy's parameters on in-sample data, then show how each combo did out-of-sample.

The point isn't to find "the best" parameters, it's to see whether in-sample winners keep winning
on data they were never tuned on. If they don't, the strategy has no real edge.
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from .backtest import Costs, backtest
from .metrics import summarize
from .strategies import Strategy
from .strategies.classic import BuyAndHold


def combos(cls: type[Strategy]) -> list[dict]:
    keys = list(cls.param_grid)
    grid = [dict(zip(keys, values)) for values in itertools.product(*cls.param_grid.values())]
    return [p for p in grid if cls.is_valid({**cls.default_params(), **p})]


def optimize(data: dict[str, pd.DataFrame], cls: type[Strategy], start, split, end, costs: Costs, timeframe: str,
             metric: str = "sharpe", min_trades: int = 20) -> tuple[pd.DataFrame, dict]:
    rows = []
    for params in combos(cls):
        s = cls(**params)
        ins = summarize(backtest(data, s, start, split, costs=costs), timeframe)
        oos = summarize(backtest(data, s, split, end, costs=costs), timeframe)
        rows.append({"strategy": s.label, "is_net_%": ins["return_%"], "is_sharpe": ins["sharpe"], "is_trades": ins["trades"],
                     "oos_net_%": oos["return_%"], "oos_sharpe": oos["sharpe"], "oos_max_dd_%": oos["max_dd_%"],
                     "oos_trades": oos["trades"], "_score": ins[metric] if ins["trades"] >= min_trades else -np.inf})
    df = pd.DataFrame(rows).sort_values("_score", ascending=False).reset_index(drop=True)
    df["oos_rank"] = df["oos_net_%"].rank(ascending=False).astype(int)
    hold_is = summarize(backtest(data, BuyAndHold(), start, split, costs=costs), timeframe)["return_%"]
    hold_oos = summarize(backtest(data, BuyAndHold(), split, end, costs=costs), timeframe)["return_%"]
    info = {
        "combos": len(df),
        "hold_is_%": hold_is,
        "hold_oos_%": hold_oos,
        "best_is_oos_rank": int(df["oos_rank"].iloc[0]) if len(df) else None,
        "median_oos_%": float(df["oos_net_%"].median()) if len(df) else float("nan"),
        "share_beating_hold_oos": float((df["oos_net_%"] > hold_oos).mean()) if len(df) else float("nan"),
        # Rank correlation between in-sample and out-of-sample results: ~0 or negative means tuning didn't help.
        "is_oos_rank_corr": float(df["is_net_%"].rank().corr(df["oos_net_%"].rank())) if len(df) > 2 else float("nan"),
    }
    return df.drop(columns="_score"), info
