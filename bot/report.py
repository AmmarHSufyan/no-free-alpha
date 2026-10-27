"""Turn backtest results into the comparison tables: gross vs net, costs, and the buy-and-hold benchmark."""
from __future__ import annotations

import pandas as pd

from .backtest import Costs, Result, backtest
from .fees import Funding
from .metrics import summarize
from .strategies import Strategy
from .strategies.classic import BuyAndHold

FREE = Costs(fee=0.0, slippage=0.0)
COLUMNS = ["gross_%", "fees", "slippage", "net_%", "hold_%", "vs_hold_%", "max_dd_%", "sharpe", "trades", "win_%", "exposure_%"]


def evaluate(data: dict[str, pd.DataFrame], strategies: list[Strategy], start, end, costs: Costs, timeframe: str,
             funding: Funding = Funding()) -> tuple[list[dict], dict[str, Result]]:
    """One row per strategy. `gross_%` is the same strategy with zero costs, i.e. its raw edge."""
    hold = summarize(backtest(data, BuyAndHold(), start, end, costs=costs), timeframe)
    rows, results = [], {}
    for s in strategies:
        net = backtest(data, s, start, end, costs=costs)
        gross = backtest(data, s, start, end, costs=FREE) if costs != FREE else net
        m = summarize(net, timeframe)
        row = {
            "strategy": s.label,
            "gross_%": summarize(gross, timeframe)["return_%"],
            "fees": net.fees,
            "slippage": net.slippage,
            "net_%": m["return_%"],
            "hold_%": hold["return_%"],
            "vs_hold_%": m["return_%"] - hold["return_%"],
            **{k: m[k] for k in ("cagr_%", "max_dd_%", "sharpe", "calmar", "trades", "win_%", "profit_factor", "avg_trade_%", "exposure_%")},
        }
        if funding.active:
            row["take_home_%"] = funding.take_home(m["return_%"] / 100) * 100
        rows.append(row)
        results[s.label] = net
    return rows, results


def columns(rows: list[dict]) -> list[str]:
    return ["strategy", *COLUMNS, *(["take_home_%"] if rows and "take_home_%" in rows[0] else [])]
