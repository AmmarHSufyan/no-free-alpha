"""Read-only Hyperliquid data: the trader leaderboard, each wallet's fills, account value history, funding.

Everything is public (no account or key needed) and cached under data/hyperliquid/. The info
endpoint allows 1200 "weight" per minute per IP; we stay under ~1000 to be polite.
"""
from __future__ import annotations

import json
import math
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from .. import config

API = "https://api.hyperliquid.xyz/info"
LEADERBOARD_URL = "https://stats-data.hyperliquid.xyz/Mainnet/leaderboard"
DIR = config.DATA_DIR / "hyperliquid"
FILLS_DIR = DIR / "fills"
PORTFOLIO_DIR = DIR / "portfolio"
FUNDING_DIR = DIR / "funding"

TAKER_FEE = 0.00045  # base tier, hyperliquid.gitbook.io/hyperliquid-docs/trading/fees (checked 2026-10-26)
MAKER_FEE = 0.00015
MAX_FILLS = 10_000   # the API only serves a wallet's 10k most recent fills
TOO_ACTIVE_PER_DAY = 60  # more fills/day than this = a high-frequency bot; each trade earns too little to copy after fees


class Client:
    """Thread-safe: several threads can share one client and its per-minute weight budget."""

    def __init__(self, budget_per_min: int = 1000):
        self.budget = budget_per_min
        self.spent: deque[tuple[float, int]] = deque()
        self.lock = threading.Lock()
        self.rate_limited = 0

    def _reserve(self, weight: int) -> None:
        while True:
            with self.lock:
                now = time.time()
                while self.spent and now - self.spent[0][0] > 60:
                    self.spent.popleft()
                if sum(w for _, w in self.spent) + weight <= self.budget:
                    self.spent.append((now, weight))
                    return
            time.sleep(0.5)

    def info(self, body: dict, items_per_weight: int = 20):
        for attempt in range(6):
            self._reserve(20)
            try:
                req = urllib.request.Request(API, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    out = json.load(r)
                n = len(out) if isinstance(out, list) else 0
                with self.lock:  # big responses cost extra weight
                    self.spent.append((time.time(), math.ceil(n / items_per_weight)))
                return out
            except urllib.error.HTTPError as e:
                if e.code != 429 and e.code < 500:
                    raise
                if e.code == 429:
                    with self.lock:
                        self.rate_limited += 1
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(15 * (attempt + 1))
        raise RuntimeError(f"Hyperliquid API kept failing for {body.get('type')}")


def leaderboard(refresh: bool = False) -> list[dict]:
    path = DIR / "leaderboard.json"
    if refresh or not path.exists():
        DIR.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(LEADERBOARD_URL, timeout=120) as r:
            path.write_bytes(r.read())
    return json.loads(path.read_text(encoding="utf-8"))["leaderboardRows"]


def window(row: dict, name: str, field: str) -> float:
    for key, v in row["windowPerformances"]:
        if key == name:
            return float(v.get(field) or 0)
    return 0.0


def fills(client: Client, addr: str, start_ms: int, refresh: bool = False) -> dict:
    """All fills since start_ms (oldest first), paging forward.

    Wallets that hit the 10k cap, or whose first full page shows high-frequency trading, are marked
    `capped` and not paged further: we can't see their full history and wouldn't copy them anyway.
    """
    path = FILLS_DIR / f"{addr}.json"
    if path.exists() and not refresh:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:  # half-written by an interrupted run: fetch again
            pass
    out, cursor, capped = [], start_ms, False
    while True:
        page = client.info({"type": "userFillsByTime", "user": addr, "startTime": cursor, "aggregateByTime": True})
        if not page:
            break
        out.extend(page)
        span_days = max((page[-1]["time"] - page[0]["time"]) / 86_400_000, 1e-3)
        if len(out) >= MAX_FILLS or (len(page) >= 2000 and len(page) / span_days > TOO_ACTIVE_PER_DAY):
            capped = True
            break
        if len(page) < 2000:
            break
        cursor = page[-1]["time"] + 1
    seen, uniq = set(), []
    for f in out:  # page boundaries can repeat a fill
        if f["tid"] not in seen:
            seen.add(f["tid"])
            uniq.append(f)
    rec = {"addr": addr, "start_ms": start_ms, "fetched_ms": int(time.time() * 1000), "capped": capped, "fills": uniq}
    FILLS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")
    return rec


def account_history(client: Client, addr: str) -> list[tuple[int, float]]:
    """(time_ms, account value) points, roughly weekly, for the wallet's whole life."""
    path = PORTFOLIO_DIR / f"{addr}.json"
    if path.exists():
        try:
            return [tuple(p) for p in json.loads(path.read_text(encoding="utf-8"))]
        except json.JSONDecodeError:
            pass
    hist = []
    for name, v in client.info({"type": "portfolio", "user": addr}):
        if name == "allTime":
            hist = [(int(t), float(x)) for t, x in v["accountValueHistory"]]
    PORTFOLIO_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(hist), encoding="utf-8")
    return hist


def funding(client: Client, coin: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    """Hourly funding rates (time_ms, rate). Positive rate: longs pay shorts."""
    path = FUNDING_DIR / f"{coin}-{start_ms}-{end_ms}.json"
    if path.exists():
        return [tuple(p) for p in json.loads(path.read_text(encoding="utf-8"))]
    out, cursor = [], start_ms
    while cursor < end_ms:
        page = client.info({"type": "fundingHistory", "coin": coin, "startTime": cursor, "endTime": end_ms})
        if not page:
            break
        out.extend((int(p["time"]), float(p["fundingRate"])) for p in page)
        nxt = int(page[-1]["time"]) + 1
        if nxt <= cursor or len(page) < 500:
            break
        cursor = nxt
    FUNDING_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out), encoding="utf-8")
    return out
