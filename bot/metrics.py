"""Performance statistics and table formatting."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from .backtest import Result
from .data import bars_per_year


def summarize(res: Result, timeframe: str) -> dict:
    curve = np.concatenate([[res.capital], res.equity.to_numpy()])
    total = curve[-1] / res.capital - 1
    years = (res.equity.index[-1] - res.equity.index[0]) / pd.Timedelta(days=365.25)
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else math.nan
    max_dd = (curve / np.maximum.accumulate(curve) - 1).min()
    rets = np.diff(curve) / curve[:-1]
    std = rets.std()
    pnl = np.array([t.pnl for t in res.trades])
    gains, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    return {
        "return_%": total * 100,
        "cagr_%": cagr * 100,
        "max_dd_%": max_dd * 100,
        "sharpe": rets.mean() / std * math.sqrt(bars_per_year(timeframe)) if std > 0 else math.nan,
        "calmar": cagr / abs(max_dd) if max_dd < 0 else math.nan,
        "trades": len(pnl),
        "win_%": (pnl > 0).mean() * 100 if len(pnl) else math.nan,
        "profit_factor": gains / losses if losses > 0 else math.nan,
        "avg_trade_%": np.mean([t.ret for t in res.trades]) * 100 if len(pnl) else math.nan,
        "exposure_%": res.exposure * 100,
        "fees": res.fees,
        "slippage": res.slippage,
    }


_FORMATS = {"trades": "{:.0f}", "sharpe": "{:.2f}", "calmar": "{:.2f}", "profit_factor": "{:.2f}",
            "fees": "{:,.0f}", "slippage": "{:,.0f}"}


def format_table(rows: list[dict], index: str = "strategy") -> str:
    df = pd.DataFrame(rows).set_index(index)
    out = df.copy().astype(object)
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            fmt = _FORMATS.get(col, "{:+.1f}" if col.endswith("_%") and col not in ("win_%", "exposure_%") else "{:.1f}")
            out[col] = [("-" if pd.isna(v) else fmt.format(v)) for v in df[col]]
    return out.to_string()
