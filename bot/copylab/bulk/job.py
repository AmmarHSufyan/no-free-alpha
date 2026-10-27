"""Hyperliquid bulk-fills job. Runs on a temporary EC2 server in ap-northeast-1 (or locally for tests).

Self-contained on purpose (only boto3, lz4, orjson, pyarrow), because the server doesn't have our repo.

Pass 1 - every wallet, every day: one row per (wallet, day) with activity and realized-PnL totals.
         Days are processed newest first; after the most recent PILOT_DAYS a _PILOT_DONE marker is
         uploaded so the summary can be checked while the rest keeps running.
Pass 2 - waits for candidates.json (wallets + coins to keep, chosen on this PC from pass-1 output),
         then extracts those wallets' individual fills.

Outputs go to s3://<out bucket>/pass1/<day>.parquet and pass2/<day>.parquet; progress to status/log.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

import boto3
import lz4.frame
import orjson
import pyarrow as pa
import pyarrow.parquet as pq

SRC_BUCKET = "hl-mainnet-node-data"
SRC_PREFIX = "node_fills_by_block/hourly/"
PILOT_DAYS = 92
# Distinct-order counting keeps a set per wallet; high-frequency wallets place millions of orders a day,
# which ran an 8 GB server out of memory. We only need to know "<= 60 orders/day", so cap the sets.
ORDER_CAP = 5000
# fills/closes count every partial fill; orders/close_orders count distinct orders (comparable to the API's
# aggregated fills), which is what the "trades per day" filters should use.
P1_COLS = ["fills", "closes", "net", "fees", "notional", "notional_sup", "net_sup", "maker", "liq", "spot",
           "coins", "orders", "close_orders"]

LOG = Path("job.log")
_log_lock = threading.Lock()


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {msg}"
    print(line, flush=True)
    with _log_lock, LOG.open("a") as f:
        f.write(line + "\n")


def s3():
    return boto3.client("s3", region_name="ap-northeast-1")


def list_days(client) -> list[str]:
    days, token = [], None
    while True:
        kw = {"Bucket": SRC_BUCKET, "Prefix": SRC_PREFIX, "Delimiter": "/", "RequestPayer": "requester"}
        if token:
            kw["ContinuationToken"] = token
        r = client.list_objects_v2(**kw)
        days += [p["Prefix"].split("/")[-2] for p in r.get("CommonPrefixes", [])]
        if not r.get("IsTruncated"):
            return sorted(days)
        token = r["NextContinuationToken"]


def hour_keys(client, day: str) -> list[str]:
    r = client.list_objects_v2(Bucket=SRC_BUCKET, Prefix=f"{SRC_PREFIX}{day}/", RequestPayer="requester")
    return sorted((o["Key"] for o in r.get("Contents", [])), key=lambda k: int(k.rsplit("/", 1)[1].split(".")[0]))


def read_blocks(client, key: str, local_dir: str | None):
    """Yield the events list of every block in one hourly file."""
    if local_dir:  # test files are saved as <day>-<hour>.lz4
        raw = Path(local_dir, f"{key.split('/')[-2]}-{key.split('/')[-1]}").read_bytes()
    else:
        raw = client.get_object(Bucket=SRC_BUCKET, Key=key, RequestPayer="requester")["Body"].read()
    for line in lz4.frame.decompress(raw).split(b"\n"):
        if line:
            yield orjson.loads(line)["events"]


def pass1_day(day: str, keys: list[str], supported: set[str], local_dir: str | None) -> pa.Table:
    client = None if local_dir else s3()
    agg: dict[str, list] = {}
    coins: dict[str, set] = defaultdict(set)
    orders: dict[str, set] = defaultdict(set)
    close_orders: dict[str, set] = defaultdict(set)
    for key in keys:
        for events in read_blocks(client, key, local_dir):
            for user, f in events:
                a = agg.get(user)
                if a is None:
                    a = agg[user] = [0, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0]
                coin = f["coin"]
                fee = float(f["fee"]) if f.get("feeToken", "USDC") == "USDC" else 0.0
                pnl = f["closedPnl"]
                net = (float(pnl) if pnl != "0.0" else 0.0) - fee
                notional = float(f["px"]) * float(f["sz"])
                a[0] += 1
                oid = f.get("oid")
                o = orders[user]
                if len(o) < ORDER_CAP:
                    o.add(oid)
                if pnl != "0.0":
                    a[1] += 1
                    co = close_orders[user]
                    if len(co) < ORDER_CAP:
                        co.add(oid)
                a[2] += net
                a[3] += fee
                a[4] += notional
                if coin in supported:
                    a[5] += notional
                    a[6] += net
                if not f.get("crossed", True):
                    a[7] += 1
                liq = f.get("liquidation")
                if liq and liq.get("liquidatedUser") == user:
                    a[8] += 1
                if coin[0] == "@" or "/" in coin:
                    a[9] += 1
                coins[user].add(coin)
    users = list(agg)
    cols = list(zip(*(agg[u] for u in users))) if users else [[] for _ in range(10)]
    data = {"user": users, "day": [int(day)] * len(users)}
    for name, values in zip(P1_COLS[:10], cols):
        data[name] = list(values)
    data["coins"] = [len(coins[u]) for u in users]
    data["orders"] = [len(orders[u]) for u in users]
    data["close_orders"] = [len(close_orders.get(u, ())) for u in users]
    return pa.table(data)


P2_FIELDS = ["user", "time", "coin", "px", "sz", "buy", "startPosition", "closedPnl", "fee", "crossed", "liq"]


def pass2_day(day: str, keys: list[str], wanted: set[str], keep_coins: set[str], local_dir: str | None) -> pa.Table:
    """Fills of the wanted wallets. Partial fills of one order at the same moment are merged into one row
    (like the API's aggregateByTime): sizes and PnL/fees summed, price volume-weighted, start position kept
    from the first piece. Cuts rows ~2-3x without changing any position or PnL."""
    client = None if local_dir else s3()
    rows = {k: [] for k in P2_FIELDS}
    last: dict[str, tuple] = {}  # user -> (oid, time, row index) of that user's latest row
    for key in keys:
        for events in read_blocks(client, key, local_dir):
            for user, f in events:
                if user not in wanted or f["coin"] not in keep_coins:
                    continue
                liq = f.get("liquidation")
                px, sz = float(f["px"]), float(f["sz"])
                fee = float(f["fee"]) if f.get("feeToken", "USDC") == "USDC" else 0.0
                prev = last.get(user)
                if prev and prev[0] == f["oid"] and prev[1] == f["time"]:
                    i = prev[2]
                    tot = rows["sz"][i] + sz
                    rows["px"][i] = (rows["px"][i] * rows["sz"][i] + px * sz) / tot
                    rows["sz"][i] = tot
                    rows["closedPnl"][i] += float(f["closedPnl"])
                    rows["fee"][i] += fee
                    rows["liq"][i] = rows["liq"][i] or bool(liq and liq.get("liquidatedUser") == user)
                    continue
                last[user] = (f["oid"], f["time"], len(rows["user"]))
                rows["user"].append(user)
                rows["time"].append(f["time"])
                rows["coin"].append(f["coin"])
                rows["px"].append(px)
                rows["sz"].append(sz)
                rows["buy"].append(f["side"] == "B")
                rows["startPosition"].append(float(f["startPosition"]))
                rows["closedPnl"].append(float(f["closedPnl"]))
                rows["fee"].append(fee)
                rows["crossed"].append(bool(f.get("crossed", True)))
                rows["liq"].append(bool(liq and liq.get("liquidatedUser") == user))
    return pa.table(rows) if rows["user"] else pa.table({"user": pa.array([], pa.string())})


def _run_day(kind: str, day: str, keys: list[str], arg_a, arg_b, out_dir: str, local_dir: str | None) -> tuple[str, int, float]:
    t0 = time.time()
    if kind == "p1":
        table = pass1_day(day, keys, arg_a, local_dir)
    else:
        table = pass2_day(day, keys, arg_a, arg_b, local_dir)
    path = Path(out_dir, kind, f"{day}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, path, compression="zstd")
    return day, table.num_rows, time.time() - t0


class Uploader:
    """Uploads finished day files and the log to the output bucket in the background."""

    def __init__(self, bucket: str | None, out_dir: str):
        self.bucket, self.out_dir = bucket, out_dir
        self.client = s3() if bucket else None
        self.stop = threading.Event()
        if bucket:
            threading.Thread(target=self._log_loop, daemon=True).start()

    def put_file(self, local: Path, key: str) -> None:
        if self.client:
            self.client.upload_file(str(local), self.bucket, key)

    def put_text(self, key: str, text: str) -> None:
        if self.client:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=text.encode())

    def existing_days(self, kind: str) -> set[str]:
        """Days already uploaded by an earlier (interrupted) run, so a restart can resume."""
        if not self.client:
            return set()
        out = set()
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=f"{kind}/"):
            out |= {o["Key"].split("/")[-1][:-8] for o in page.get("Contents", []) if o["Key"].endswith(".parquet")}
        return out

    def _log_loop(self) -> None:
        while not self.stop.wait(60):
            if LOG.exists():
                self.put_file(LOG, "status/log.txt")


def run_pass(kind: str, days: list[str], keys: dict, arg_a, arg_b, out_dir: str, local_dir: str | None,
             workers: int, up: Uploader, pilot_marker: int | None = None) -> None:
    rows, t_start = 0, time.time()
    pilot, pilot_sent = set(days[:pilot_marker or 0]), False
    finished = up.existing_days(kind) & set(days)
    if finished:
        log(f"{kind}: resuming, {len(finished)} days already uploaded")
    done_before, crashes = len(finished), 0
    while len(finished) < len(days) and crashes <= 6:
        todo = [d for d in days if d not in finished]
        try:
            with ProcessPoolExecutor(workers) as pool:
                futs = [pool.submit(_run_day, kind, d, keys[d], arg_a, arg_b, out_dir, local_dir) for d in todo]
                for fut in as_completed(futs):
                    day, n, secs = fut.result()
                    finished.add(day)
                    rows += n
                    up.put_file(Path(out_dir, kind, f"{day}.parquet"), f"{kind}/{day}.parquet")
                    new = len(finished) - done_before
                    rate = new / (time.time() - t_start)
                    log(f"{kind} {day}: {n:,} rows in {secs:.0f}s | {len(finished)}/{len(days)} days, "
                        f"~{(len(days) - len(finished)) / rate / 60:.0f} min left")
                    if pilot and not pilot_sent and pilot <= finished:  # newest days all in: check them while the rest runs
                        up.put_text(f"{kind}/_PILOT_DONE", json.dumps(sorted(pilot)))
                        pilot_sent = True
                        log(f"{kind}: pilot block ({len(pilot)} newest days) complete")
        except BrokenProcessPool:  # a worker died (usually out of memory): retry the rest with fewer workers
            crashes += 1
            workers = max(1, workers - 1)
            log(f"{kind}: a worker crashed; retrying {len(days) - len(finished)} days with {workers} workers")
    up.put_text(f"{kind}/_DONE", json.dumps({"days": len(days), "rows": rows}))
    log(f"{kind} complete: {len(days)} days, {rows:,} rows in {(time.time() - t_start) / 60:.0f} min")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-bucket", default=None, help="S3 bucket for outputs (omit for local tests)")
    ap.add_argument("--out-dir", default="out")
    ap.add_argument("--local-dir", default=None, help="read hourly files from this folder instead of S3 (tests)")
    ap.add_argument("--days", default="", help="comma list of days (tests); default = all days in the bucket")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--supported", default="supported_coins.json")
    ap.add_argument("--wait-candidates-min", type=int, default=120)
    ap.add_argument("--skip-pass2", action="store_true")
    a = ap.parse_args()

    up = Uploader(a.out_bucket, a.out_dir)
    supported = set(json.loads(Path(a.supported).read_text()))
    log(f"job start: workers={a.workers}, supported coins={len(supported)}")
    if a.local_dir:
        files = sorted(f for f in os.listdir(a.local_dir) if f.endswith(".lz4"))
        days = sorted({f.split("-")[0] for f in files}, reverse=True)
        keys = {d: [f"{SRC_PREFIX}{d}/{f.split('-')[1]}" for f in files if f.split("-")[0] == d] for d in days}
    else:
        client = s3()
        days = sorted(a.days.split(",") if a.days else list_days(client), reverse=True)
        keys = {d: hour_keys(client, d) for d in days}
    log(f"{len(days)} days, {sum(len(v) for v in keys.values())} hourly files")

    run_pass("p1", days, keys, supported, None, a.out_dir, a.local_dir, a.workers, up, pilot_marker=min(PILOT_DAYS, len(days)))
    if a.skip_pass2:
        return
    if not a.out_bucket:
        cand = json.loads(Path("candidates.json").read_text())
    else:
        log(f"waiting up to {a.wait_candidates_min} min for candidates.json")
        deadline, cand = time.time() + a.wait_candidates_min * 60, None
        while time.time() < deadline:
            try:
                cand = json.loads(up.client.get_object(Bucket=a.out_bucket, Key="candidates.json")["Body"].read())
                break
            except up.client.exceptions.NoSuchKey:
                time.sleep(30)
        if cand is None:
            log("no candidates.json arrived; finishing without pass 2")
            up.put_text("status/FINISHED", "no pass2")
            return
    log(f"pass 2: {len(cand['wallets'])} wallets, {len(cand['coins'])} coins")
    run_pass("p2", days, keys, set(cand["wallets"]), set(cand["coins"]), a.out_dir, a.local_dir, a.workers, up)
    up.put_text("status/FINISHED", "ok")
    if a.out_bucket and LOG.exists():
        up.put_file(LOG, "status/log.txt")


if __name__ == "__main__":
    sys.exit(main())
