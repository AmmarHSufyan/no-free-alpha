"""Walk-forward copy-trading test.

Every month: each finder ranks the pool using only fills before that month, picks leaders, and the
copier follows them through the month. Each month is out-of-sample for the choice made at its start.
Results are compared with the leaders' own raw result (no delay, no costs) and with holding BTC.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

from .. import config, data
from . import hyperliquid as hl
from . import store
from .copier import CopyCosts, Prices, copy_leader
from .finder import MODES, equity_at, perp_fills, select, wallet_stats
from .pool import load_pool

HOUR = 3_600_000
DAY = 86_400_000
EXCHANGE = "binanceusdm"
DATA_START = "2026-01-26"  # first day of downloaded wallet fills
ALL_MODES = (*MODES, "skill")


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.timestamp() * 1000)


def binance_symbols() -> set[str]:
    path = hl.DIR / "binance_usdm_symbols.json"
    if not path.exists():
        markets = data.make_exchange(EXCHANGE).load_markets()
        path.write_text(json.dumps(sorted(s for s, m in markets.items() if m.get("swap") and m.get("quote") == "USDT")))
    return set(json.loads(path.read_text()))


def to_binance(coin: str, symbols: set[str]) -> str | None:
    # Hyperliquid quotes some small-price coins per 1000 units ("kPEPE"); Binance calls those "1000PEPE".
    base = "1000" + coin[1:] if coin.startswith("k") and coin[1:].isupper() else coin
    sym = f"{base}/USDT:USDT"
    return sym if sym in symbols else None


def binance_funding(symbol: str, start_ms: int, end_ms: int) -> dict[int, float]:
    """Funding rates at each settlement hour. A stand-in for Hyperliquid's (similar, not identical):
    one request covers ~10 months, versus hundreds of rate-limited calls to Hyperliquid."""
    path = config.DATA_DIR / EXCHANGE / "funding" / f"{symbol.split('/')[0]}-{start_ms}-{end_ms}.json"
    if path.exists():
        return {int(k): v for k, v in json.loads(path.read_text()).items()}
    ex, out, cursor = data.make_exchange(EXCHANGE), {}, start_ms
    while cursor < end_ms:
        rows = ex.fetch_funding_rate_history(symbol, since=cursor, limit=1000)
        if not rows:
            break
        out.update({r["timestamp"] // HOUR * HOUR: float(r["fundingRate"]) for r in rows if r["timestamp"] < end_ms})
        nxt = rows[-1]["timestamp"] + 1
        if nxt <= cursor or len(rows) < 1000:
            break
        cursor = nxt
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out))
    return out


def periods(first: str, months: int) -> list[tuple[int, int]]:
    now = pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=1)
    starts = [pd.Timestamp(first, tz="UTC") + pd.DateOffset(months=i) for i in range(months)]
    out = []
    for i, s in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else min(s + pd.DateOffset(months=1), now)
        if s < end:
            out.append((_ms(s), _ms(end)))
    return out


def load_wallets(addrs: list[str]) -> dict:
    out = {}
    for a in addrs:
        fp, hp = hl.FILLS_DIR / f"{a}.json", hl.PORTFOLIO_DIR / f"{a}.json"
        if not fp.exists():
            continue
        try:  # a download running alongside may be mid-write on the newest file
            rec = json.loads(fp.read_text(encoding="utf-8"))
            hist = [tuple(p) for p in json.loads(hp.read_text(encoding="utf-8"))] if hp.exists() else []
        except json.JSONDecodeError:
            continue
        spot_times = np.array([f["time"] for f in rec["fills"] if f["coin"].startswith("@") or "/" in f["coin"]], dtype="int64")
        out[a] = (perp_fills(rec), rec["capped"], hist, spot_times)
    return out


def stats_table(wallets: dict, t0: int, lookback: int, supported: set[str]) -> pd.DataFrame:
    rows = {}
    lo = t0 - lookback * DAY
    for a, (df, capped, hist, spot_times) in wallets.items():
        if df.empty:
            continue
        s = wallet_stats(df, t0, lookback, supported, equity_at(hist, t0))
        if s:
            # Spot trades alongside perps usually mean a hedge we can't see; copying only the perp leg
            # turns a hedged position into a naked bet.
            spot = int(((spot_times >= lo) & (spot_times < t0)).sum())
            perp = int(((df["time"] >= lo) & (df["time"] < t0)).sum())
            rows[a] = {**s, "capped": capped, "spot_share": spot / max(spot + perp, 1)}
    return pd.DataFrame.from_dict(rows, orient="index")


def coins_needed(df: pd.DataFrame, t0: int, t1: int, supported: set[str]) -> set[str]:
    held = df[df["time"] < t0].groupby("coin").tail(1)
    open_at_t0 = set(held.loc[(held["startPosition"] + held["signed"]).abs() > 0, "coin"])
    traded = set(df.loc[(df["time"] >= t0) & (df["time"] < t1), "coin"])
    return (open_at_t0 | traded) & supported


def run(first: str = "2026-04-26", months: int = 6, k: int = 10, lookback: int = 90, capital: float = 10_000.0,
        delays: tuple[int, ...] = (10,), slippage: float = 0.0003, with_funding: bool = True,
        modes: tuple[str, ...] = ALL_MODES) -> dict:
    from .skill import select_skill

    t_start = time.time()
    wallets = load_wallets(load_pool())
    symbols = binance_symbols()
    all_coins = set().union(*(set(df["coin"]) for df, *_ in wallets.values() if not df.empty))
    mapping = {c: s for c in all_coins if (s := to_binance(c, symbols))}
    supported = set(mapping)
    pers = periods(first, months)
    data_start = _ms(pd.Timestamp(DATA_START, tz="UTC"))
    print(f"{len(wallets)} wallets loaded, {len(supported)}/{len(all_coins)} of their coins have Binance price data; "
          f"{len(pers)} monthly periods; finders: {', '.join(modes)}", flush=True)

    stats = {pi: stats_table(wallets, t0, lookback, supported) for pi, (t0, _) in enumerate(pers)}
    # Every coin any candidate traded before the end of its month: covers the copy months for all finders
    # and the history replays the skill finder runs.
    need = {"BTC"}
    for pi, (t0, t1) in enumerate(pers):
        for a in stats[pi].index:
            need |= set(wallets[a][0].loc[wallets[a][0]["time"] < t1, "coin"]) & supported
    since = str((pd.Timestamp(DATA_START, tz="UTC") - pd.Timedelta(days=2)).date())
    print(f"Preparing 1-minute prices for {len(need)} coins (bulk files, 12 at a time) and funding...", flush=True)
    with ThreadPoolExecutor(12) as pool:
        list(pool.map(lambda c: store.ensure(mapping[c], since), sorted(need)))
    end_ms = pers[-1][1]
    fund = {}
    if with_funding:
        with ThreadPoolExecutor(8) as pool:
            fund = dict(zip(sorted(need), pool.map(lambda c: binance_funding(mapping[c], data_start, end_ms), sorted(need))))
    prices = Prices({c: store.load(mapping[c]) for c in need})
    btc_t, btc_o = prices.t["BTC"], prices.o["BTC"]
    days = np.arange(data_start, end_ms + DAY, DAY)
    btc_open = pd.Series([prices.next_open("BTC", d) for d in days], index=pd.to_datetime(days, unit="ms", utc=True))
    btc_daily = btc_open.pct_change().shift(-1).dropna()  # return over each day, labelled by its start
    print(f"Data ready after {time.time() - t_start:.0f}s. Choosing leaders...", flush=True)

    picks, skill_tables = {}, {}
    for pi, (t0, t1) in enumerate(pers):
        for mode in modes:
            if mode == "skill":
                picks[(mode, pi)], skill_tables[pi] = select_skill(wallets, stats[pi], t0, data_start, mapping, fund, btc_daily)
                print(f"  {pd.Timestamp(t0, unit='ms'):%Y-%m-%d}: skill finder tested {len(skill_tables[pi])} wallets, "
                      f"{int(skill_tables[pi]['discovery'].sum()) if len(skill_tables[pi]) and 'discovery' in skill_tables[pi] else 0} "
                      f"passed the luck test, picked {len(picks[(mode, pi)])}", flush=True)
            else:
                picks[(mode, pi)] = select(mode, stats[pi], k, seed=pi)

    variants = {"leader raw (no delay, no costs)": CopyCosts(ideal=True),
                **{f"copy, {d}s delay": CopyCosts(delay_s=d, slippage=slippage) for d in delays},
                f"copy, {delays[0]}s + 25% safety stop": CopyCosts(delay_s=delays[0], slippage=slippage, kill_dd=0.25)}
    rows = []
    for mode in modes:
        for pi, (t0, t1) in enumerate(pers):
            addrs = picks[(mode, pi)]
            for vname, vc in variants.items():
                res = [copy_leader(wallets[a][0], t0, t1, equity_at(wallets[a][2], t0), capital / len(addrs), prices, fund, vc)
                       for a in addrs]
                end = sum(r.equity.iloc[-1] for r in res) if res else capital
                rows.append({
                    "mode": mode, "period": pd.Timestamp(t0, unit="ms", tz="UTC").strftime("%Y-%m-%d"), "variant": vname,
                    "return_%": (end / capital - 1) * 100, "leaders": len(addrs),
                    "leaders_up": sum(r.ret > 0 for r in res), "liquidated": sum(r.liquidated for r in res),
                    "trades": sum(r.trades for r in res), "fees": sum(r.fees for r in res),
                    "slippage": sum(r.slippage for r in res), "funding": sum(r.funding for r in res),
                })
    btc = [{"period": pd.Timestamp(t0, unit="ms", tz="UTC").strftime("%Y-%m-%d"),
            "btc_hold_%": ((prices.next_open("BTC", t1) / prices.next_open("BTC", t0)) * (1 - hl.TAKER_FEE) ** 2 - 1) * 100}
           for t0, t1 in pers]

    out = config.RUNS_DIR / "copylab" / pd.Timestamp.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    per = pd.DataFrame(rows)
    per.to_csv(out / "periods.csv", index=False)
    pd.DataFrame(btc).to_csv(out / "btc.csv", index=False)
    (out / "picks.json").write_text(json.dumps({f"{m}|{pi}": v for (m, pi), v in picks.items()}, indent=1))
    for pi, t in skill_tables.items():
        if len(t):
            t.to_csv(out / f"skill_scores_{pi}.csv")
    print(f"done in {time.time() - t_start:.0f}s, saved to {out}")
    return {"periods": per, "btc": pd.DataFrame(btc), "dir": out}


def summarize(per: pd.DataFrame, btc: pd.DataFrame) -> pd.DataFrame:
    def compound(r):
        return ((1 + r / 100).prod() - 1) * 100

    rows = []
    for (mode, variant), g in per.groupby(["mode", "variant"], sort=False):
        rows.append({"finder": mode, "how": variant, "total_%": compound(g["return_%"]),
                     "months_up": f"{(g['return_%'] > 0).sum()}/{len(g)}", "worst_month_%": g["return_%"].min(),
                     "avg_leaders": g["leaders"].mean(),
                     "leaders_up_%": g["leaders_up"].sum() / max(g["leaders"].sum(), 1) * 100,
                     "liquidated": int(g["liquidated"].sum()), "fees": g["fees"].sum(), "funding": g["funding"].sum()})
    rows.append({"finder": "-", "how": "just hold BTC", "total_%": compound(btc["btc_hold_%"]),
                 "months_up": f"{(btc['btc_hold_%'] > 0).sum()}/{len(btc)}", "worst_month_%": btc["btc_hold_%"].min()})
    return pd.DataFrame(rows)
