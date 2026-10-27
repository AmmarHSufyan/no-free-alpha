"""Rebuild every figure and derived table in the paper.

  python -m bot.trend               # once: daily candles for the trend study (live + delisted coins)
  python paper/make_figures.py      # figures -> paper/figures/, derived tables -> results/

The copy-trading and engine figures need only the files in results/. The trend figure and table also need
the daily candles. Every number printed here is quoted in the paper.
"""
from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RES, FIG = ROOT / "results", ROOT / "paper" / "figures"

# First three slots of a colour-blind-checked categorical palette; benchmarks in neutral ink.
BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, BASE, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "axes.edgecolor": BASE, "axes.labelcolor": INK2, "text.color": INK, "xtick.color": INK2, "ytick.color": INK2,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
    "axes.spines.top": False, "axes.spines.right": False, "axes.titlelocation": "left", "axes.titlesize": 11,
    "axes.titleweight": "bold", "font.size": 9.5, "lines.linewidth": 1.6, "legend.frameon": False,
    "savefig.dpi": 200, "savefig.bbox": "tight",
})
COPY = "copy 10s"


def save(fig, name: str) -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG / name)
    plt.close(fig)
    print("wrote", FIG / name)


def compound(pct: pd.Series) -> float:
    return ((1 + pct / 100).prod() - 1) * 100


def tip_labels(ax, bars, fmt="{:+.0f}%", pad=1.5) -> None:
    for b in bars:
        w = b.get_width()
        ax.text(w + (pad if w >= 0 else -pad), b.get_y() + b.get_height() / 2, fmt.format(w),
                va="center", ha="left" if w >= 0 else "right", color=INK2, fontsize=8.5)


def cap_labels(ax, bars, fmt="{:+.1f}%", pad=0.0) -> None:
    for b in bars:
        h = b.get_height()
        ax.text(b.get_x() + b.get_width() / 2, h + (pad if h >= 0 else -pad), fmt.format(h),
                ha="center", va="bottom" if h >= 0 else "top", color=INK2, fontsize=8.5)


def ref_label(ax, x: float, text: str, ha: str) -> None:
    """Label a vertical reference line just above the plot area (x in data units, y in axes units)."""
    ax.text(x, 1.01, text, transform=ax.get_xaxis_transform(), ha=ha, va="bottom", color=INK2, fontsize=8)


def legend_below(ax, drop: float = -0.2) -> None:
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, drop), ncol=2, fontsize=8.5)


def engine_costs() -> None:
    d = pd.read_csv(RES / "engine" / "backtest_1h_full_2024-10_2026-10.csv").set_index("strategy")
    hold = d.loc["buy_hold", "net_%"]
    d = d.drop("buy_hold").sort_values("gross_%")
    y = np.arange(len(d))
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.barh(y + 0.19, d["gross_%"], 0.36, color=BLUE, label="before costs")
    net = ax.barh(y - 0.19, d["net_%"], 0.36, color=ORANGE, label="after 0.10% fee + 0.05% slippage per fill")
    tip_labels(ax, net)
    ax.set_xlim(d["net_%"].min() - 18, d["gross_%"].max() + 10)
    ax.axvline(0, color=BASE, lw=1)
    ax.axvline(hold, color=INK2, lw=1)
    ref_label(ax, hold, f"buy & hold {hold:+.0f}%", "center")
    ax.set_yticks(y, [f"{s}  ({int(t):,} trades)" for s, t in zip(d.index, d["trades"])])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("total return, %")
    ax.set_title("Figure 1. Five indicator strategies on 5 coins, 1-hour candles, Oct 2024 - Oct 2026", pad=18)
    legend_below(ax)
    save(fig, "fig1_costs.png")


FINDERS = {"hot (best last 7 days $)": "Hot: best last 7 days",
           "edge (best shrunk bps)": "Edge: best shrunk edge",
           "skill (luck test + shrunk edge)": "Skill: luck test + shrinkage",
           "steady (t-stat, 2/3 months up)": "Steady: t-stat >= 2, 2 of 3 months up"}


def copy_walkforward() -> None:
    d = pd.read_csv(RES / "copylab" / "walkforward_12m.csv")
    tot = d.groupby(["set", "variant"])["return_%"].apply(compound).unstack()
    placebo = tot.loc[tot.index.str.startswith("placebo"), COPY]
    btc = compound(d.drop_duplicates("month").set_index("month")["btc_%"])
    months_up = d[d.variant == COPY].groupby("set")["return_%"].apply(lambda r: int((r > 0).sum()))
    rows = [{"finder": FINDERS[k], "leader_raw_%": tot.loc[k, "leader raw"], "copy_10s_%": tot.loc[k, COPY],
             "copy_10s_stop25_%": tot.loc[k, "copy 10s + 25% stop"], "copy_months_up": months_up[k],
             "share_of_random_sets_beaten_%": (placebo < tot.loc[k, COPY]).mean() * 100} for k in FINDERS]
    rows += [{"finder": f"random sets (median of {len(placebo)})", "copy_10s_%": placebo.median()},
             {"finder": "hold BTC", "copy_10s_%": btc}]
    summary = pd.DataFrame(rows)
    summary.to_csv(RES / "copylab" / "walkforward_12m_summary.csv", index=False)
    print(summary.round(1).to_string(index=False))

    s = summary.iloc[:4].iloc[::-1]
    y = np.arange(len(s))
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    ax.barh(y + 0.19, s["leader_raw_%"], 0.36, color=BLUE, label="the leaders themselves (no delay, no costs)")
    cp = ax.barh(y - 0.19, s["copy_10s_%"], 0.36, color=ORANGE, label="our copy (10 s delay, fees, slippage, funding)")
    tip_labels(ax, cp)
    for x, name, ha in ((btc, "hold BTC", "right"), (placebo.median(), "random wallets", "left")):
        ax.axvline(x, color=INK2, lw=1)
        ref_label(ax, x, f"{name} {x:+.0f}%", ha)
    ax.axvline(0, color=BASE, lw=1)
    ax.set_yticks(y, s["finder"])
    ax.grid(axis="y", visible=False)
    ax.set_xlabel("12-month total return, %  (Oct 2025 - Oct 2026, 50 leaders re-picked monthly)")
    ax.set_title("Figure 2. Copying Hyperliquid wallets chosen by four finders", pad=18)
    legend_below(ax, -0.24)
    save(fig, "fig2_copy_walkforward.png")


def copy_cost_ladder() -> None:
    wf = pd.read_csv(RES / "copylab" / "walkforward_12m.csv")
    cs = pd.read_csv(RES / "copylab" / "cost_sensitivity_steady.csv")
    wo = pd.read_csv(RES / "copylab" / "waiting_orders_steady.csv")
    steady = "steady (t-stat, 2/3 months up)"
    leader = compound(wf[(wf.set == steady) & (wf.variant == "leader raw")]["return_%"])
    c = cs.groupby("variant")["return_%"].apply(compound)
    w = wo.groupby("variant")["return_%"].apply(compound)
    steps = [("The leaders'\nown result", leader), ("Our copy,\n10 s late,\nno costs", c["no fees at all, 10s delay"]),
             ("+ maker fee\n(assumes every\norder fills)", c["maker fee 0.015%, no slippage"]),
             ("+ taker fee\nand slippage", c["taker fee + slippage (current)"]),
             ("Waiting orders,\nrealistic fills\n(chase after 5 min)", w["waiting orders, else chase after 5 min"])]
    pd.DataFrame(steps, columns=["step", "return_12m_%"]).assign(step=lambda x: x.step.str.replace("\n", " ")) \
        .to_csv(RES / "copylab" / "steady_cost_ladder.csv", index=False)
    print({k.replace("\n", " "): round(v, 1) for k, v in steps}, {k: round(v, 1) for k, v in w.items()})
    fig, ax = plt.subplots(figsize=(7.2, 3.4))
    bars = ax.bar(range(len(steps)), [v for _, v in steps], 0.5, color=BLUE)
    cap_labels(ax, bars, pad=1.0)
    ax.set_ylim(min(v for _, v in steps) - 12, max(v for _, v in steps) + 10)
    ax.axhline(0, color=BASE, lw=1)
    ax.set_xticks(range(len(steps)), [k for k, _ in steps], fontsize=8.5)
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("12-month total return, %")
    ax.set_title("Figure 3. Where the best finder's edge goes: costs of copying the 'steady' leaders")
    save(fig, "fig3_cost_ladder.png")


def persistence() -> None:
    R = pd.read_parquet(RES / "copylab" / "weekly_copy_returns.parquet")
    cols = list(R.columns)
    rows = []
    for i in range(8, len(cols)):
        past = R[cols[i - 8:i]]
        n = past.notna().sum(axis=1)
        d = pd.DataFrame({"past_mean": past.mean(axis=1), "past_win": (past > 0).sum(axis=1) / n.clip(lower=1),
                          "n": n, "next": R[cols[i]]})
        rows.append(d[(d.n >= 4) & d.next.notna()])
    d = pd.concat(rows)
    spear = lambda a, b: a.rank().corr(b.rank())
    print(f"{len(d):,} wallet-weeks; rank corr past mean -> next {spear(d.past_mean, d.next):.3f}; "
          f"past win share -> next {spear(d.past_win, d.next):.3f}")
    d["decile"] = pd.qcut(d.past_mean.rank(method="first"), 10, labels=[f"D{i}" for i in range(1, 11)])
    dec = d.groupby("decile", observed=True).agg(past_avg_pct=("past_mean", "mean"), next_avg_pct=("next", "mean"),
                                                 next_median_pct=("next", "median"),
                                                 next_win_pct=("next", lambda x: (x > 0).mean() * 100))
    win = d.groupby(pd.cut(d.past_win, [-0.01, 0.25, 0.5, 0.625, 0.75, 1.0]), observed=True)["next"].agg(
        next_avg_pct="mean", wallet_weeks="count", next_win_pct=lambda x: (x > 0).mean() * 100)
    dec.to_csv(RES / "copylab" / "persistence_deciles.csv")
    win.to_csv(RES / "copylab" / "persistence_win_share.csv")
    print(dec.round(2).to_string()); print(win.round(2).to_string())

    fig, (a, b) = plt.subplots(1, 2, figsize=(7.6, 3.1), sharey=True, gridspec_kw={"width_ratios": [10, 6]})
    a.bar(range(10), dec.next_avg_pct, 0.6, color=BLUE)
    a.set_xticks(range(10), [f"D{i}" for i in range(1, 11)])
    a.set_xlabel("decile of the past 8 weeks' average copy return\n(D1 = worst past, D10 = best past)")
    a.set_ylabel("average copy return the next week, %")
    a.set_title("Figure 4. Past copy profit does not predict next week")
    labels = ["0-25", "25-50", "50-62", "62-75", "75-100"]
    b.bar(range(len(win)), win.next_avg_pct, 0.6, color=BLUE)
    b.set_xticks(range(len(win)), labels, fontsize=8)
    b.set_xlabel("share of the past 8 weeks\nthat were winners, %")
    for ax in (a, b):
        ax.axhline(0, color=BASE, lw=1)
        ax.grid(axis="x", visible=False)
    save(fig, "fig4_persistence.png")


def hot_streak() -> None:
    d = pd.read_csv(RES / "copylab" / "hot_streak_weekly.csv")
    wk = [f"week{k}_%" for k in (1, 2, 3, 4)]
    hot, rnd = d[d.set == "hot"].set_index("week")[wk], d[d.set == "random"]
    beat = (hot["week1_%"] > rnd.groupby("week")["week1_%"].median().reindex(hot.index)).mean() * 100
    means = pd.DataFrame({"hot": hot.mean(), "random": rnd[wk].mean()})
    means.to_csv(RES / "copylab" / "hot_streak_summary.csv")
    print(means.round(2).to_string(), f"\nhot beat the random median in {beat:.0f}% of week-1s ({len(hot)} weeks)")
    x = np.arange(4)
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    ax.bar(x - 0.17, means.hot, 0.32, color=ORANGE, label="last week's top 10 wallets")
    ax.bar(x + 0.17, means.random, 0.32, color=BLUE, label="random wallets from the same pool")
    ax.axhline(0, color=BASE, lw=1)
    ax.set_xticks(x, [f"week {k}" for k in (1, 2, 3, 4)])
    ax.set_xlabel("weeks after the wallets were picked")
    ax.grid(axis="x", visible=False)
    ax.set_ylabel("average weekly copy return, %")
    ax.set_title("Figure 5. Copying last week's winners: no hot streak, not even for one week")
    legend_below(ax, -0.28)
    save(fig, "fig5_hot_streak.png")


def skill_funnel() -> None:
    # 1,000 random API-pool wallets, finder run for the copy month starting 2026-07-26 (see the paper, Sec. 5.2).
    steps = [("random wallets in the pool", 1000), ("traded perps in the prior 90 days", 471), ("not high-frequency", 427),
             (">= 70% of volume in coins we can price", 276), ("account >= $1,000", 175), ("no spot hedge", 125),
             ("never liquidated", 66), ("active enough (days, closes)", 45), ("not carried by 3 lucky trades", 39),
             ("does not average down into losers", 16), ("passed the luck test (bootstrap + FDR)", 0)]
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    y = np.arange(len(steps))[::-1]
    bars = ax.barh(y, [v for _, v in steps], 0.55, color=BLUE)
    for b, (_, v) in zip(bars, steps):
        ax.text(b.get_width() + 12, b.get_y() + b.get_height() / 2, f"{v:,}", va="center", color=INK2, fontsize=8.5)
    ax.set_yticks(y, [k for k, _ in steps], fontsize=8.5)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, 1120)
    ax.set_xlabel("wallets remaining")
    ax.set_title("Figure 6. The skill finder's funnel: 1,000 wallets in, none out")
    save(fig, "fig6_skill_funnel.png")


def trend() -> None:
    from bot import trend as tr
    periods = [("2020-01-01", "2026-10-26"), ("2020-01-01", "2022-12-31"), ("2023-01-01", "2026-10-26")]
    rows, curves = [], {}
    for top in (10, 20, 30):
        o, c, uni = tr.point_in_time_panels(top=top)
        score = tr.trend_score(c)
        for label, vol_days, weekly in (("daily", 30, False), ("weekly", 90, True)):
            w = tr.target_weights(c, score, target_vol=1.0, vol_days=vol_days, universe=uni)
            if weekly:
                w = w.where(pd.Series(c.index.day_of_week == 0, index=c.index), np.nan).ffill().fillna(0.0)
            for a, b in periods:
                yrs = (pd.Timestamp(b) - pd.Timestamp(a)).days / 365.25
                res = tr.backtest(o.loc[:b], c.loc[:b], w.loc[:b], a, band=0.25)
                rows.append({"period": f"{a[:4]}-{b[:4]}", "strategy": f"trend top {top}, {label}",
                             **tr.stats(res["equity"]), "trades_per_coin_per_year": res["trades"] / yrs / top,
                             "avg_invested_%": res["exposure"] * 100})
                if top == 30 and label == "daily" and a == "2020-01-01" and b == "2026-10-26":
                    curves["trend rotation (top 30, daily)"] = res["equity"]
    o, c, uni = tr.point_in_time_panels(top=30)
    for a, b in periods:
        first = [x for x in c.columns if uni.loc[uni.index >= pd.Timestamp(a, tz="UTC")].iloc[0][x]]
        for name, coins in (("hold BTC", ["BTC"]), (f"hold the top 30 of {a[:4]}", first)):
            eq = tr.buy_and_hold(o.loc[:b], c.loc[:b], coins, a)
            rows.append({"period": f"{a[:4]}-{b[:4]}", "strategy": name, **tr.stats(eq)})
            if a == "2020-01-01" and b == "2026-10-26":
                curves[name.replace("2020", "Jan 2020")] = eq
    t = pd.DataFrame(rows)
    (RES / "trend").mkdir(parents=True, exist_ok=True)
    t.to_csv(RES / "trend" / "trend_variants.csv", index=False)
    print(t.round(2).to_string(index=False))

    fig, (a, b) = plt.subplots(2, 1, figsize=(7.2, 5.0), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    for (name, eq), color in zip(curves.items(), (BLUE, ORANGE, AQUA)):
        a.plot(eq.index, eq / 10_000, color=color, label=f"{name}: {eq.iloc[-1] / 10_000:.1f}x")
        b.plot(eq.index, (eq / eq.cummax() - 1) * 100, color=color, lw=1.2)
    a.set_yscale("log")
    a.set_yticks([0.5, 1, 2, 5, 10, 20, 50])
    a.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}x"))
    a.yaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    a.set_ylabel("growth of $1 (log scale)")
    a.set_title("Figure 7. Daily trend rotation vs holding, Jan 2020 - Oct 2026 (dead coins included)")
    a.legend(loc="lower right", fontsize=8.5)
    b.set_ylabel("drop from peak, %")
    b.axhline(0, color=BASE, lw=1)
    save(fig, "fig7_trend.png")


if __name__ == "__main__":
    for step in (engine_costs, copy_walkforward, copy_cost_ladder, persistence, hot_streak, skill_funnel, trend):
        step()
