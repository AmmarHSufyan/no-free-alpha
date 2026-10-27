"""Compact 1-minute price store for the copy lab.

One .npy file per coin holding a (2, N) float64 array: row 0 = candle open time (ms), row 1 = open
price. About 4x smaller than CSV, loads instantly, and is memory-mapped, so many worker processes
can share it without each holding a copy in RAM.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .. import config, data

DIR = config.DATA_DIR / "binanceusdm" / "npy"
EXCHANGE = "binanceusdm"


def path(symbol: str):
    return DIR / f"{symbol.split('/')[0]}-1m.npy"


def _arrays(df: pd.DataFrame) -> np.ndarray:
    return np.vstack([df.index.as_unit("ms").asi8.astype("float64"), df["open"].to_numpy(dtype="float64")])


def ensure(symbol: str, since: str) -> int:
    """Make sure the store covers `since` .. the latest closed minute for this symbol."""
    p = path(symbol)
    start_ms = pd.Timestamp(since, tz="UTC").normalize().timestamp() * 1000
    stale_ms = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=2)).timestamp() * 1000
    if p.exists():
        try:
            arr = np.load(p)
        except (ValueError, OSError):  # half-written by an interrupted run
            arr = None
        if arr is not None and arr.shape[1] and arr[0, 0] <= start_ms + 60_000:
            if arr[0, -1] >= stale_ms:
                return arr.shape[1]
            tail = data.drop_unclosed(data.fetch_range(data.make_exchange(EXCHANGE), symbol, "1m", int(arr[0, -1]) + 60_000), "1m")
            if len(tail):
                arr = np.hstack([arr, _arrays(tail)])
                np.save(p, arr)
            return arr.shape[1]
    arr = _arrays(data.bulk_1m_frame(symbol, since, EXCHANGE))
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp.npy")
    np.save(tmp, arr)
    tmp.replace(p)
    return arr.shape[1]


def load(symbol: str) -> tuple[np.ndarray, np.ndarray]:
    arr = np.load(path(symbol), mmap_mode="r")
    return arr[0], arr[1]
