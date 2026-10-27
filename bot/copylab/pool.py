"""Build the candidate pool and download every candidate's fill history.

The pool is a *random* sample of wallets in a mid-size trading-volume band, chosen without looking at
profit, so it contains plenty of losers. Picking from today's top earners would bake the answer in.
"""
from __future__ import annotations

import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import hyperliquid as hl

POOL_PATH = hl.DIR / "pool.json"


def build_pool(size: int = 1000, min_volume: float = 1e6, max_volume: float = 3e8, seed: int = 7) -> list[str]:
    rows = hl.leaderboard()
    band = sorted(r["ethAddress"] for r in rows if min_volume <= hl.window(r, "allTime", "vlm") <= max_volume)
    pool = random.Random(seed).sample(band, min(size, len(band)))
    hl.DIR.mkdir(parents=True, exist_ok=True)
    POOL_PATH.write_text(json.dumps({"size": len(pool), "band": [min_volume, max_volume], "seed": seed,
                                     "band_count": len(band), "wallets": pool}), encoding="utf-8")
    return pool


def load_pool() -> list[str]:
    return json.loads(POOL_PATH.read_text(encoding="utf-8"))["wallets"]


def download(start: str, size: int = 1000, threads: int = 6) -> None:
    """Fetch every pool wallet's fills and account history, `threads` wallets at a time.

    The threads share one client, so together they stay under Hyperliquid's per-IP weight limit.
    """
    import pandas as pd

    pool = load_pool() if POOL_PATH.exists() else build_pool(size)
    start_ms = int(pd.Timestamp(start, tz="UTC").timestamp() * 1000)
    client = hl.Client(budget_per_min=1100)
    todo = [a for a in pool if not ((hl.FILLS_DIR / f"{a}.json").exists() and (hl.PORTFOLIO_DIR / f"{a}.json").exists())]
    print(f"{len(pool) - len(todo)} wallets already cached, {len(todo)} to go, {threads} at a time", flush=True)

    def one(addr: str) -> int:
        rec = hl.fills(client, addr, start_ms)
        hl.account_history(client, addr)
        return len(rec["fills"])

    t0 = time.time()
    with ThreadPoolExecutor(threads) as ex:
        for i, _ in enumerate(as_completed([ex.submit(one, a) for a in todo]), 1):
            if i % 25 == 0 or i == len(todo):
                rate = i / (time.time() - t0)
                print(f"{len(pool) - len(todo) + i}/{len(pool)} wallets, ~{(len(todo) - i) / max(rate, 1e-9) / 60:.0f} min left, "
                      f"{rate * 60:.0f}/min, rate-limited {client.rate_limited}x", flush=True)


if __name__ == "__main__":
    download(sys.argv[1] if len(sys.argv) > 1 else "2026-01-26", int(sys.argv[2]) if len(sys.argv) > 2 else 1000,
             int(sys.argv[3]) if len(sys.argv) > 3 else 6)
