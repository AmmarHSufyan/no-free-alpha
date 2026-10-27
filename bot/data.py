"""Download and cache OHLCV candles from crypto exchanges via ccxt.

Candles are stored as CSV under data/<exchange>/<BASE_QUOTE>-<timeframe>.csv, indexed by the
candle's open time in UTC. Only closed candles are ever written to disk.
"""
from __future__ import annotations

import io
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import ccxt
import pandas as pd

from . import config

COLUMNS = ["open", "high", "low", "close", "volume"]
_UNIT_MS = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}


def timeframe_ms(timeframe: str) -> int:
    return int(timeframe[:-1]) * _UNIT_MS[timeframe[-1]]


def bars_per_year(timeframe: str) -> float:
    # Crypto trades 24/7, so a year is 365 full days.
    return 365 * _UNIT_MS["d"] / timeframe_ms(timeframe)


def make_exchange(name: str = config.EXCHANGE) -> ccxt.Exchange:
    return getattr(ccxt, name)({"enableRateLimit": True})


def cache_path(exchange: str, pair: str, timeframe: str) -> Path:
    # Futures symbols look like BTC/USDT:USDT; ':' isn't allowed in Windows file names.
    return config.DATA_DIR / exchange / f"{pair.replace('/', '_').replace(':', '_')}-{timeframe}.csv"


def to_frame(rows: list[list]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["ts", *COLUMNS])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts").astype(float)
    return df[~df.index.duplicated(keep="last")].sort_index()


def drop_unclosed(df: pd.DataFrame, timeframe: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Remove the still-forming candle (exchanges return it as the last row)."""
    now = now or pd.Timestamp.now(tz="UTC")
    return df[df.index + pd.Timedelta(milliseconds=timeframe_ms(timeframe)) <= now]


def _fetch_with_retry(ex: ccxt.Exchange, pair: str, timeframe: str, since: int | None, limit: int, attempts: int = 4):
    for attempt in range(attempts):
        try:
            return ex.fetch_ohlcv(pair, timeframe, since=since, limit=limit)
        except ccxt.NetworkError:
            if attempt == attempts - 1:
                raise
            time.sleep(2 ** attempt)


def fetch_range(ex: ccxt.Exchange, pair: str, timeframe: str, since_ms: int, limit: int = 1000) -> pd.DataFrame:
    """Page through the exchange's candle history from since_ms up to now."""
    step = timeframe_ms(timeframe)
    rows: list[list] = []
    cursor = since_ms
    while cursor < ex.milliseconds():
        batch = _fetch_with_retry(ex, pair, timeframe, cursor, limit)
        if not batch:
            break
        rows.extend(batch)
        nxt = batch[-1][0] + step
        if nxt <= cursor:
            break
        cursor = nxt
    return to_frame(rows) if rows else pd.DataFrame(columns=COLUMNS)


def fetch_recent(ex: ccxt.Exchange, pair: str, timeframe: str, bars: int) -> pd.DataFrame:
    """Latest `bars` closed candles, straight from the exchange (used by the paper trader)."""
    rows = _fetch_with_retry(ex, pair, timeframe, None, min(bars + 1, 1000))
    return drop_unclosed(to_frame(rows), timeframe)


def download(pairs: list[str], timeframe: str, since: str, exchange: str = config.EXCHANGE) -> dict[str, int]:
    """Fill the cache from `since` (or from the last cached candle) up to the latest closed candle."""
    ex = make_exchange(exchange)
    counts = {}
    for pair in pairs:
        path = cache_path(exchange, pair, timeframe)
        cached = load(pair, timeframe, exchange) if path.exists() else None
        start = pd.Timestamp(since, tz="UTC")
        if cached is not None and len(cached) and cached.index[0] <= start:
            start = cached.index[-1] + pd.Timedelta(milliseconds=timeframe_ms(timeframe))
        else:
            cached = None
        fresh = fetch_range(ex, pair, timeframe, int(start.timestamp() * 1000))
        df = fresh if cached is None else pd.concat([cached, fresh])
        df = drop_unclosed(df[~df.index.duplicated(keep="last")].sort_index(), timeframe)
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index_label="ts")
        counts[pair] = len(df)
    return counts


VISION = {"binanceusdm": "https://data.binance.vision/data/futures/um", "binance": "https://data.binance.vision/data/spot"}


def _vision_month_or_day(url: str) -> pd.DataFrame | None:
    """One of Binance's bulk kline zips (data.binance.vision), or None if it isn't published."""
    try:
        with urllib.request.urlopen(url, timeout=120) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as z, z.open(z.namelist()[0]) as f:
        df = pd.read_csv(f, header=None, usecols=range(6), dtype=str)
    if not df.iloc[0, 0].isdigit():  # newer files start with a header row
        df = df.iloc[1:]
    ts = df[0].astype("int64")
    ts = ts.where(ts < 10**14, ts // 1000)  # some files use microseconds
    out = pd.DataFrame({c: df[i + 1].astype(float).to_numpy() for i, c in enumerate(COLUMNS)},
                       index=pd.to_datetime(ts.to_numpy(), unit="ms", utc=True))
    out.index.name = "ts"
    return out


def download_bulk_1m(pair: str, since: str, exchange: str = "binanceusdm") -> int:
    """1-minute candles from `since` to now into the CSV cache (see bulk_1m_frame)."""
    path = cache_path(exchange, pair, "1m")
    start = pd.Timestamp(since, tz="UTC").normalize()
    try:
        covered = path.exists() and load(pair, "1m", exchange).index[0] <= start + pd.Timedelta(minutes=1)
    except (ValueError, KeyError, pd.errors.ParserError):  # half-written by an interrupted run: rebuild
        covered = False
    if covered:
        return download([pair], "1m", since, exchange)[pair]
    df = bulk_1m_frame(pair, since, exchange)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index_label="ts")
    return len(df)


def bulk_1m_frame(pair: str, since: str, exchange: str = "binanceusdm") -> pd.DataFrame:
    """1-minute candles from `since` to now: monthly/daily bulk files, then the API for today's candles.

    About 25x faster than paging the API.
    """
    start = pd.Timestamp(since, tz="UTC").normalize()
    sym = pair.split("/")[0] + pair.split("/")[1].split(":")[0]
    base = f"{VISION[exchange]}/{{kind}}/klines/{sym}/1m/{sym}-1m-{{when}}.zip"
    now = pd.Timestamp.now(tz="UTC")
    this_month = now.normalize().replace(day=1)
    frames, month = [], start.replace(day=1)
    while month < this_month:
        df = _vision_month_or_day(base.format(kind="monthly", when=f"{month:%Y-%m}"))
        if df is None:  # not published yet (or listed mid-month): use daily files for that month
            day_end = month + pd.DateOffset(months=1)
            days = pd.date_range(max(month, start), day_end - pd.Timedelta(days=1), freq="D")
            frames += [d for d in (_vision_month_or_day(base.format(kind="daily", when=f"{x:%Y-%m-%d}")) for x in days) if d is not None]
        else:
            frames.append(df)
        month += pd.DateOffset(months=1)
    for day in pd.date_range(max(this_month, start), now.normalize() - pd.Timedelta(days=1), freq="D"):
        df = _vision_month_or_day(base.format(kind="daily", when=f"{day:%Y-%m-%d}"))
        if df is not None:
            frames.append(df)
    last = max((f.index[-1] for f in frames), default=start - pd.Timedelta(minutes=1))
    frames.append(fetch_range(make_exchange(exchange), pair, "1m", int(last.timestamp() * 1000) + 60_000))
    df = pd.concat([f for f in frames if len(f)]).sort_index()
    df = drop_unclosed(df[~df.index.duplicated(keep="last")], "1m")
    return df[df.index >= start]


def load(pair: str, timeframe: str, exchange: str = config.EXCHANGE) -> pd.DataFrame:
    path = cache_path(exchange, pair, timeframe)
    if not path.exists():
        raise FileNotFoundError(f"No cached data for {pair} {timeframe}. Run: python -m bot download --pairs {pair} --timeframe {timeframe}")
    df = pd.read_csv(path, index_col="ts")
    df.index = pd.to_datetime(df.index, utc=True)
    return df.astype(float)
