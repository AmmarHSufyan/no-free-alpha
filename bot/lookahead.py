"""Detect lookahead bias: signals computed on truncated history must equal the full-history signals.

If a strategy (accidentally) peeks at future candles, its signal at row t changes once those
future rows are removed, so the two versions disagree.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .strategies import Strategy


def check(strategy: Strategy, df: pd.DataFrame, samples: int = 40) -> list[pd.Timestamp]:
    """Return the timestamps where truncated and full-history signals differ (empty = clean)."""
    full = strategy.signals(df)
    cuts = np.unique(np.linspace(max(strategy.warmup, 30), len(df) - 2, samples).astype(int))
    bad = []
    for c in cuts:
        part = strategy.signals(df.iloc[: c + 1])
        if (part.iloc[-1].to_numpy() != full.iloc[c].to_numpy()).any():
            bad.append(df.index[c])
    return bad
