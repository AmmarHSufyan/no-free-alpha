"""Small, fast checks on the bulk data (each a few minutes). Run as: python -c "from bot.copylab import minitests as m; m.steady_in_pool()".

(Worker processes on Windows need an importable module, so these live here instead of inline scripts.)
"""
from __future__ import annotations

import json
import random
from concurrent.futures import ProcessPoolExecutor

import pandas as pd

from . import confirm, quick
from .biglab import BULK, load_p1, load_p2
from .copier import CopyCosts
from .lab import binance_funding, binance_symbols, to_binance

DAY = quick.DAY


def eligible(p1: pd.DataFrame, T: int) -> pd.DataFrame:
    b = quick.window_stats(p1, T - 90 * DAY, T)
    return b[(b["orders"] / 90 <= 30) & (b["days"] >= 20) & (b["closes"] >= 20) & (b["liq"] == 0)
             & (b["spot"] / b["fills"].clip(lower=1) <= 0.1) & (b["notional"] >= 1e5)
             & (b["notional"] / b["notional_all"].clip(lower=1e-9) >= 0.7)]


def _weekly_curve(task):
    """Worker: copy a set of leaders for 4 weeks; returns the set's value at the end of weeks 1-4 (start = 1.0)."""
    dfs, t0, symbols, fund, costs = task
    from . import store
    from .biglab import exposure_proxy
    from .copier import Prices, copy_leader
    import math
    prices = Prices({c: store.load(s) for c, s in symbols.items()})
    eqs = {a: exposure_proxy(df, t0 - 90 * DAY, t0) for a, df in dfs.items()}
    use = [a for a, e in eqs.items() if e and not math.isnan(e) and e > 0]
    if not use:
        return [1.0, 1.0, 1.0, 1.0]
    curves = [copy_leader(dfs[a], t0, t0 + 28 * DAY, eqs[a], 1.0, prices, fund, costs).equity for a in use]
    marks = [pd.Timestamp(t0 + k * 7 * DAY, unit="ms", tz="UTC") for k in (1, 2, 3, 4)]
    return [sum(float(c[c.index <= m].iloc[-1]) for c in curves) / len(use) for m in marks]


def hot_streak(top_n: int = 10, random_sets: int = 20) -> pd.DataFrame:
    """Hot-streak hypothesis: a trader good for 1 week stays good for 1 more week, then fades.
    Weekly, inside the random pool: pick the top `top_n` by last-7-day realized PnL, copy them 4 weeks, and
    record weeks 1-4 separately, vs random sets from the same eligible pool."""
    p1 = load_p1()
    cand = json.loads((BULK / "candidates.json").read_text())
    picks = json.loads((confirm.config.RUNS_DIR / "copylab" / "quick_15m_picks.json").read_text())
    finder_w = {w for p in picks for w in p["wallets"]}
    dfs = load_p2(set(cand["wallets"]) - finder_w)
    sym = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, sym))}
    first, last = int(p1["t"].min()), int(p1["t"].max()) + DAY
    starts = list(range(first + 90 * DAY, last - 28 * DAY + 1, 7 * DAY))
    coins = {c for df in dfs.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
    fund = {c: binance_funding(mapping[c], first, last) for c in coins}
    tasks, keys = [], []
    for T in starts:
        pool = pd.Index([w for w in eligible(p1, T).index.astype(str) if w in dfs])
        week = quick.window_stats(p1, T - 7 * DAY, T)["net"].reindex(pool).fillna(0)
        hot = list(week.nlargest(top_n).index)
        rng = random.Random(T // DAY)
        sets = {"hot": hot, **{f"rand{i}": rng.sample(list(pool), top_n) for i in range(random_sets)}}
        for name, ws in sets.items():
            sub = {a: dfs[a][dfs[a]["time"] < T + 28 * DAY] for a in ws}
            cs = {c for df in sub.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
            tasks.append((sub, T, {c: mapping[c] for c in cs}, {c: fund.get(c, {}) for c in cs}, CopyCosts(delay_s=10)))
            keys.append((pd.Timestamp(T, unit="ms").strftime("%Y-%m-%d"), name))
    with ProcessPoolExecutor(18) as ex:
        curves = list(ex.map(_weekly_curve, tasks, chunksize=8))
    rows = []
    for (wk, name), c in zip(keys, curves):
        prev = [1.0, *c[:3]]
        rows.append({"week": wk, "set": "hot" if name == "hot" else "random",
                     **{f"week{k + 1}_%": (c[k] / prev[k] - 1) * 100 for k in range(4)}})
    r = pd.DataFrame(rows)
    r.to_csv(confirm.config.RUNS_DIR / "copylab" / "mini_hot_streak.csv", index=False)
    return r


def _wallet_weeks(task):
    """Worker: our copy return (%) for one wallet in each requested week (take over its positions at T,
    follow for 7 days with delay and costs, close at T+7d). Prices load once per wallet."""
    import math
    from . import store
    from .biglab import exposure_proxy
    from .copier import Prices, copy_leader
    addr, df, weeks, symbols, fund, costs = task
    prices = Prices({c: store.load(s) for c, s in symbols.items()})
    out = {}
    for T in weeks:
        eq = exposure_proxy(df, T - 90 * DAY, T)
        if not eq or math.isnan(eq) or eq <= 0:
            continue
        out[T] = copy_leader(df[df["time"] < T + 7 * DAY], T, T + 7 * DAY, eq, 1.0, prices, fund, costs).ret * 100
    return addr, out


def weekly_table() -> pd.DataFrame:
    """Build once: R[wallet, week] = our 1-week copy return for every pool wallet eligible that week, plus each
    week's selection scores (hot = last-7-day PnL, steady = 90-day t-stat with 2/3 months up) and BTC's weekly
    return. Saved under runs/copylab/weekly_* so rotation rules can be tested in seconds."""
    import numpy as np
    from . import store
    p1 = load_p1()
    cand = json.loads((BULK / "candidates.json").read_text())
    picks = json.loads((confirm.config.RUNS_DIR / "copylab" / "quick_15m_picks.json").read_text())
    dfs = load_p2(set(cand["wallets"]) - {w for p in picks for w in p["wallets"]})
    sym = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, sym))}
    first, last = int(p1["t"].min()), int(p1["t"].max()) + DAY
    starts = list(range(first + 90 * DAY, last - 7 * DAY + 1, 7 * DAY))
    coins = {c for df in dfs.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
    fund = {c: binance_funding(mapping[c], first, last) for c in coins}
    weeks_of: dict[str, list[int]] = {}
    scores = {}
    for T in starts:
        pool = pd.Index([w for w in eligible(p1, T).index.astype(str) if w in dfs])
        hot = quick.window_stats(p1, T - 7 * DAY, T)["net"].reindex(pool).fillna(0)
        ts = quick.daily_tstat(p1, pool, T - 90 * DAY, T)
        steady = ts["tstat"].where(ts["months_up"] >= 2, -99)
        scores[str(T)] = {"hot": hot.to_dict(), "steady": steady.to_dict()}
        for w in pool:
            weeks_of.setdefault(w, []).append(T)
    tasks = []
    for w, weeks in weeks_of.items():
        cs = {c for c in dfs[w]["coin"].unique() if c in mapping} | {"BTC"}
        tasks.append((w, dfs[w], weeks, {c: mapping[c] for c in cs}, {c: fund.get(c, {}) for c in cs}, CopyCosts(delay_s=10)))
    print(f"{len(starts)} weeks, {len(tasks):,} wallets, {sum(len(v) for v in weeks_of.values()):,} wallet-weeks to replay", flush=True)
    with ProcessPoolExecutor(18) as ex:
        res = dict(ex.map(_wallet_weeks, tasks, chunksize=4))
    R = pd.DataFrame(res).T.reindex(columns=starts)  # rows wallets, columns week start; NaN = not eligible
    R.columns = [str(c) for c in R.columns]
    out = confirm.config.RUNS_DIR / "copylab"
    R.to_parquet(out / "weekly_R.parquet")
    (out / "weekly_scores.json").write_text(json.dumps(scores))
    bt, bo = store.load("BTC/USDT:USDT")
    btc = {str(T): float((bo[np.searchsorted(bt, T + 7 * DAY)] / bo[np.searchsorted(bt, T)] - 1) * 100) for T in starts}
    (out / "weekly_btc.json").write_text(json.dumps(btc))
    return R


def rotation(R: pd.DataFrame, scores: dict, select: str = "hot", n: int = 10, drop_after: int = 1,
             drop_dd: float | None = None, cooldown: int = 4) -> pd.Series:
    """Keep-winners / swap-losers rule: hold n leaders; keep a leader while copying them makes money; drop after `drop_after`
    losing weeks in a row (or, if `drop_dd` is set, once our copy of them is drop_dd% below where we started
    following); replace with the best-scored eligible wallet not held and not dropped within `cooldown` weeks.
    A leader that is no longer eligible (inactive / too risky) is replaced. Returns weekly portfolio returns (%)."""
    held: dict[str, dict] = {}
    dropped: dict[str, int] = {}
    out = {}
    for i, wk in enumerate(R.columns):
        col = R[wk]
        held = {w: h for w, h in held.items() if not pd.isna(col.get(w))}
        for w in pd.Series(scores[wk][select]).sort_values(ascending=False).index:
            if len(held) >= n:
                break
            if w in held or pd.isna(col.get(w)) or i - dropped.get(w, -99) <= cooldown:
                continue
            held[w] = {"losses": 0, "cum": 1.0}
        rets = [col[w] for w in held]
        out[wk] = float(sum(rets) / len(rets)) if rets else 0.0
        for w in list(held):
            r, h = col[w], held[w]
            h["cum"] *= 1 + r / 100
            h["losses"] = h["losses"] + 1 if r < 0 else 0
            if h["losses"] >= drop_after or (drop_dd is not None and (h["cum"] - 1) * 100 <= -drop_dd):
                held.pop(w)
                dropped[w] = i
    return pd.Series(out)


def recurrent_scores(R: pd.DataFrame, lookback: int = 8, min_weeks: int = 6, min_win: float = 0.7) -> dict:
    """Recurring-winner selection: wallets whose *copied* weekly results were profitable again and again.
    For each week, using only the previous `lookback` weeks of R (our own 1-week copy returns): needs at least
    `min_weeks` observed weeks, a share of winning weeks >= min_win and a positive average; ranked by
    average / standard deviation x sqrt(weeks) (steadiness). Wallets failing the test are not candidates."""
    cols = list(R.columns)
    out = {}
    for i, wk in enumerate(cols):
        past = R[cols[max(0, i - lookback):i]]
        n = past.notna().sum(axis=1)
        mean, std = past.mean(axis=1), past.std(axis=1)
        win = (past > 0).sum(axis=1) / n.clip(lower=1)
        ok = (n >= min_weeks) & (win >= min_win) & (mean > 0)
        score = (mean / std.replace(0, float("nan")) * n.pow(0.5))[ok].dropna()
        out[wk] = {"recurrent": score.to_dict()}
    return out


def rotation_report(random_draws: int = 200) -> pd.DataFrame:
    """Every variant of the 'keep winners, swap losers' rule, from the saved weekly table (seconds)."""
    import numpy as np
    out = confirm.config.RUNS_DIR / "copylab"
    R = pd.read_parquet(out / "weekly_R.parquet")
    scores = json.loads((out / "weekly_scores.json").read_text())
    btc = pd.Series(json.loads((out / "weekly_btc.json").read_text()))
    comp = lambda s: ((1 + s / 100).prod() - 1) * 100
    rng = np.random.default_rng(0)
    rows = []

    def add(name, weekly):
        weekly = weekly.reindex(R.columns).fillna(0.0)
        rows.append({"strategy": name, "total_%": comp(weekly), "weeks_up_%": (weekly > 0).mean() * 100,
                     "worst_week_%": weekly.min(), "avg_week_%": weekly.mean()})

    # The recurring-winner selection (own copy history), in three strictness levels.
    rec = {"recurrent 6/8 wks": recurrent_scores(R, 8, 6, 0.7), "recurrent 5/8 wks": recurrent_scores(R, 8, 5, 0.6),
           "recurrent 12 wks": recurrent_scores(R, 12, 8, 0.65)}
    for name, sc in rec.items():
        merged = {wk: {**scores[wk], "recurrent": sc[wk]["recurrent"]} for wk in R.columns}
        cand = pd.Series({wk: len(sc[wk]["recurrent"]) for wk in R.columns})
        for after, dd in ((1, None), (2, None), (99, 5.0)):
            label = f"drop after {after} losing week{'s' if after > 1 else ''}" if dd is None else f"drop at -{dd:.0f}% since followed"
            add(f"rule: {name} (median {int(cand.median())} candidates/wk); {label}",
                rotation(R, merged, select="recurrent", drop_after=after, drop_dd=dd))
    variants = [("hot", 1, None), ("steady", 1, None), ("steady", 99, 5.0)]
    for select, after, dd in variants:
        label = f"{select} picks; drop after {after} losing week" if dd is None else f"{select} picks; drop at -{dd:.0f}% since followed"
        add("same rule, " + label, rotation(R, scores, select=select, drop_after=after, drop_dd=dd))
    # Same rule, but replacements chosen at random: does 'keep winners, drop losers' help on its own?
    rand_rule = []
    for d in range(30):
        sc = {wk: {"rand": {w: float(rng.random()) for w in v["hot"]}} for wk, v in scores.items()}
        rand_rule.append(rotation(R, sc, select="rand", drop_after=1))
    add("same rule, random replacements (avg of 30)", pd.concat(rand_rule, axis=1).mean(axis=1))
    # Plain baselines.
    add("top 10 by last week's profit, swapped every week (no keep rule)", rotation(R, scores, select="hot", drop_after=0, cooldown=0))
    rand_weeks = []
    for wk in R.columns:
        col = R[wk].dropna()
        draws = [col.sample(min(10, len(col)), random_state=int(rng.integers(1e9))).mean() for _ in range(random_draws)]
        rand_weeks.append(float(np.median(draws)))
    add("random 10 leaders each week (median)", pd.Series(rand_weeks, index=R.columns))
    add("just hold Bitcoin", btc)
    t = pd.DataFrame(rows).set_index("strategy")
    t.to_csv(out / "rotation_report.csv")
    return t


def _one_leader(task):
    """Worker: copy one leader alone for one month; returns % return on that leader's allocation."""
    df, t0, t1, symbols, fund, costs = task
    ret, used, _ = confirm._replay(({"x": df}, t0, t1, symbols, fund, costs, 1_000.0))
    return ret if used else float("nan")


def steady_contributions() -> pd.DataFrame:
    """Per-leader results behind the steady finder's 12-month copy result: is it a few wallets/operators?"""
    cand = json.loads((BULK / "candidates.json").read_text())
    picks = [p for p in json.loads((confirm.config.RUNS_DIR / "copylab" / "quick_15m_picks.json").read_text())
             if p["finder"].startswith("steady")]
    dfs = load_p2({w for p in picks for w in p["wallets"]})
    sym = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, sym))}
    ds = min(int(df["time"].min()) for df in dfs.values())
    end = int(pd.Timestamp(max(p["month"] for p in picks), tz="UTC").timestamp() * 1000) + 30 * DAY
    coins = {c for df in dfs.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
    fund = {c: binance_funding(mapping[c], ds, end) for c in coins}
    tasks, keys = [], []
    for p in picks:
        T = int(pd.Timestamp(p["month"], tz="UTC").timestamp() * 1000)
        for w in p["wallets"]:
            if w not in dfs:
                continue
            df = dfs[w][dfs[w]["time"] < T + 30 * DAY]
            cs = {c for c in df["coin"].unique() if c in mapping} | {"BTC"}
            tasks.append((df, T, T + 30 * DAY, {c: mapping[c] for c in cs}, {c: fund.get(c, {}) for c in cs}, CopyCosts(delay_s=10)))
            main_coin = df.loc[df["time"] >= T - 90 * DAY, "coin"].value_counts().index[0] if len(df) else "-"
            keys.append((p["month"], w, main_coin, len(df[(df["time"] >= T) & (df["time"] < T + 30 * DAY)])))
    with ProcessPoolExecutor(18) as ex:
        rets = list(ex.map(_one_leader, tasks, chunksize=8))
    r = pd.DataFrame([(*k, x) for k, x in zip(keys, rets)], columns=["month", "wallet", "main_coin", "trades_in_month", "ret_%"])
    r.to_csv(confirm.config.RUNS_DIR / "copylab" / "mini_steady_contributions.csv", index=False)
    return r


def steady_in_pool(top_n: int = 10, rest_sets: int = 30) -> pd.DataFrame:
    """Does the steady ranking generalize? Inside the random pool (never touched by the quick finders), rank
    wallets the steady way with past data only; copy the top `top_n` vs random groups from the rest."""
    p1 = load_p1()
    cand = json.loads((BULK / "candidates.json").read_text())
    picks = json.loads((confirm.config.RUNS_DIR / "copylab" / "quick_15m_picks.json").read_text())
    finder_w = {w for p in picks for w in p["wallets"]}
    dfs = load_p2(set(cand["wallets"]) - finder_w)
    sym = binance_symbols()
    mapping = {c: s for c in cand["coins"] if (s := to_binance(c, sym))}
    months = sorted({p["month"] for p in picks})
    ds = int(p1["t"].min())
    end = int(pd.Timestamp(months[-1], tz="UTC").timestamp() * 1000) + 30 * DAY
    coins = {c for df in dfs.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
    fund = {c: binance_funding(mapping[c], ds, end) for c in coins}
    tasks, keys = [], []
    for m in months:
        T = int(pd.Timestamp(m, tz="UTC").timestamp() * 1000)
        pool = [w for w in eligible(p1, T).index.astype(str) if w in dfs]
        ts = quick.daily_tstat(p1, pd.Index(pool), T - 90 * DAY, T)
        top = list(ts[ts["months_up"] >= 2].sort_values("tstat", ascending=False).index[:top_n])
        rest = [w for w in pool if w not in top]
        rng = random.Random(int(m.replace("-", "")))
        sets = {"top": top, **{f"rest{i}": rng.sample(rest, top_n) for i in range(rest_sets)}}
        for name, ws in sets.items():
            sub = {a: dfs[a][dfs[a]["time"] < T + 30 * DAY] for a in ws}
            cs = {c for df in sub.values() for c in df["coin"].unique() if c in mapping} | {"BTC"}
            tasks.append((sub, T, T + 30 * DAY, {c: mapping[c] for c in cs}, {c: fund.get(c, {}) for c in cs},
                          CopyCosts(delay_s=10), 10_000.0))
            keys.append((m, name, len(pool)))
    with ProcessPoolExecutor(18) as ex:
        out = list(ex.map(confirm._replay, tasks, chunksize=4))
    r = pd.DataFrame([(m, n, p, o[0]) for (m, n, p), o in zip(keys, out)], columns=["month", "set", "pool", "ret"])
    top = r[r["set"] == "top"].set_index("month")["ret"]
    rest = r[r["set"].str.startswith("rest")]
    table = pd.DataFrame({"pool size": r.groupby("month")["pool"].first(), f"top {top_n} %": top,
                          "rest median %": rest.groupby("month")["ret"].median(),
                          "top beats % of rest sets": [float((rest[rest["month"] == m]["ret"] < top[m]).mean() * 100) for m in top.index]})
    comp = lambda x: ((1 + x / 100).prod() - 1) * 100
    table.attrs["summary"] = (f"12 months compounded: top-{top_n} {comp(top):.1f}% vs typical rest "
                              f"{comp(table['rest median %']):.1f}%; top beat the rest median in "
                              f"{int((top > table['rest median %']).sum())}/{len(top)} months")
    r.to_csv(confirm.config.RUNS_DIR / "copylab" / "mini_steady_in_pool.csv", index=False)
    return table
