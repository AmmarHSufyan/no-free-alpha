"""Command line entry point: python -m bot <command> [options]. Run with -h for help."""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import config, data, fees, paper
from .lookahead import check
from .metrics import format_table
from .optimize import optimize
from .report import columns, evaluate
from .strategies import REGISTRY, expand, split_specs
from .strategies.ai import ClaudeTrader

LEGEND = """\
gross_%    return if trading were free (the strategy's raw edge)
fees       exchange commission paid on every buy and sell (USDT)
slippage   value lost to the spread / price moving on each fill (USDT)
net_%      what you would actually have made after all trading costs
hold_%     simply buying the same pairs on day one and holding (same costs)
vs_hold_%  net_% minus hold_%; negative means holding would have been better"""


def _pairs(text: str) -> list[str]:
    return [p.strip().upper() for p in text.split(",") if p.strip()]


def _load(args) -> dict[str, pd.DataFrame]:
    return {pair: data.load(pair, args.timeframe, args.exchange) for pair in _pairs(args.pairs)}


def _costs(args):
    profile = fees.PROFILES[args.fees]
    return profile, profile.costs(args.fee, args.slippage)


def _print_costs(profile, costs) -> None:
    print(f"Costs: {profile.venue}: fee {costs.fee:.3%} + slippage {costs.slippage:.3%} per fill "
          f"(~{2 * (costs.fee + costs.slippage):.2%} per round trip)")


def _print_ai_usage(strategies) -> None:
    for s in strategies:
        if isinstance(s, ClaudeTrader) and s.llm:
            print(f"AI ({s.label}): {s.llm.calls} new Claude requests (they count toward your Claude plan's usage limits), "
                  f"{s.llm.cache_hits} reused from cache")


def cmd_download(args) -> None:
    for tf in args.timeframe.split(","):
        counts = data.download(_pairs(args.pairs), tf, args.since, args.exchange)
        for pair, n in counts.items():
            print(f"{pair:<10} {tf:>4}: {n} candles cached")


def cmd_fees(args) -> None:
    print(fees.describe())


def cmd_backtest(args) -> None:
    profile, costs = _costs(args)
    funding = fees.Funding(args.deposit_fee, args.withdraw_fee, args.tax)
    strategies = expand(args.strategies)
    dfs = _load(args)
    end = args.end or None
    last = max(df.index[-1] for df in dfs.values())
    print(f"{len(dfs)} pairs, {args.timeframe} candles, {args.start} -> {end or last:%Y-%m-%d %H:%M}, capital {args.capital:,.0f} USDT")
    _print_costs(profile, costs)
    if funding.active:
        print(f"Funding: deposit {funding.deposit:.2%}, withdrawal {funding.withdraw:.2%}, tax on gains {funding.tax:.0%} -> take_home_%")
    periods = [("Full period", args.start, end)]
    if args.split.lower() != "none":
        periods += [(f"In-sample (before {args.split})", args.start, args.split), (f"Out-of-sample (from {args.split})", args.split, end)]

    out = config.RUNS_DIR / "backtests" / pd.Timestamp.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    for i, (title, start, stop) in enumerate(periods):
        rows, results = evaluate(dfs, strategies, start, stop, costs, args.timeframe, funding)
        print(f"\n== {title} ==")
        print(format_table([{k: r[k] for k in columns(rows)} for r in rows]))
        pd.DataFrame(rows).to_csv(out / f"summary_{i}.csv", index=False)
        pd.DataFrame({k: r.equity for k, r in results.items()}).to_csv(out / f"equity_{i}.csv", index_label="ts")
        if args.per_pair:
            per = pd.DataFrame({k: {p: (pr.equity.iloc[-1] / pr.capital - 1) * 100 for p, pr in r.per_pair.items()}
                                for k, r in results.items()}).T
            print("\nNet return % per pair:\n" + per.round(1).to_string())
    print("\n" + LEGEND)
    _print_ai_usage(strategies)
    print(f"\nSaved to {out}")


def cmd_optimize(args) -> None:
    profile, costs = _costs(args)
    cls = REGISTRY[args.strategy]
    if not cls.param_grid:
        sys.exit(f"{args.strategy} has no parameter grid")
    dfs = _load(args)
    _print_costs(profile, costs)
    table, info = optimize(dfs, cls, args.start, args.split, args.end or None, costs, args.timeframe, args.metric, args.min_trades)
    print(f"\n{info['combos']} combos, ranked by in-sample {args.metric} (needs >= {args.min_trades} in-sample trades)")
    print(table.head(args.top).to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print(f"\nBuy & hold: in-sample {info['hold_is_%']:+.1f}%, out-of-sample {info['hold_oos_%']:+.1f}%")
    print(f"The in-sample winner ranked #{info['best_is_oos_rank']} of {info['combos']} out-of-sample.")
    print(f"Median out-of-sample return across all combos: {info['median_oos_%']:+.1f}%; "
          f"{info['share_beating_hold_oos']:.0%} of combos beat buy & hold out-of-sample.")
    print(f"In-sample vs out-of-sample rank correlation: {info['is_oos_rank_corr']:+.2f} "
          "(near 0 or negative = tuning on the past didn't help in the future)")


def cmd_lookahead(args) -> None:
    dfs = _load(args)
    for s in expand(args.strategies):
        if isinstance(s, ClaudeTrader):
            print(f"{s.label:<45} skipped (each decision only receives candles up to that point by construction)")
            continue
        bad = {pair: check(s, df) for pair, df in dfs.items()}
        flagged = {p: b for p, b in bad.items() if b}
        verdict = "OK" if not flagged else "LOOKAHEAD BIAS at " + ", ".join(f"{p} ({len(b)} pts)" for p, b in flagged.items())
        print(f"{s.label:<45} {verdict}")


def cmd_paper(args) -> None:
    profile, costs = _costs(args)
    specs = split_specs(args.strategies) if args.strategies else None
    trader = paper.PaperTrader(args.name, specs, _pairs(args.pairs), args.timeframe, args.capital, costs,
                               args.exchange, args.poll)
    _print_costs(profile, costs)
    trader.run()


def cmd_status(args) -> None:
    df = paper.status(args.name)
    started = pd.Timestamp(df.attrs["started"]).tz_convert(config.DISPLAY_TZ)
    print(f"Paper run {args.name!r}, started {started:%Y-%m-%d %H:%M} ({config.DISPLAY_TZ})")
    print(format_table(df.reset_index().to_dict("records")))


def cmd_copylab(args) -> None:
    from .copylab import lab

    res = lab.run(args.first, args.months, args.k, args.lookback, args.capital, tuple(args.delays), args.slippage,
                  with_funding=not args.no_funding)
    print("\nPer month (return % of the whole copy portfolio):")
    wide = res["periods"].pivot_table(index="period", columns=["mode", "variant"], values="return_%", sort=False)
    wide[("-", "just hold BTC")] = res["btc"].set_index("period")["btc_hold_%"]
    print(wide.round(1).to_string())
    print("\nWhole test, months compounded:")
    print(lab.summarize(res["periods"], res["btc"]).round(1).to_string(index=False))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m bot", description="Our own crypto strategy lab: backtests and paper trading.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def market(p, timeframe=config.TIMEFRAME):
        p.add_argument("--pairs", default=",".join(config.PAIRS), help="comma separated, e.g. BTC/USDT,ETH/USDT")
        p.add_argument("--timeframe", default=timeframe)
        p.add_argument("--exchange", default=config.EXCHANGE)

    def cost_opts(p):
        p.add_argument("--fees", default=fees.DEFAULT, choices=list(fees.PROFILES), help="fee profile, see `python -m bot fees`")
        p.add_argument("--fee", type=float, help="override the per-fill fee, e.g. 0.00075")
        p.add_argument("--slippage", type=float, help="override the per-fill slippage, e.g. 0.0003")

    p = sub.add_parser("download", help="download / update candles from the exchange")
    market(p, timeframe="1h,4h,1d")
    p.add_argument("--since", default=config.DOWNLOAD_SINCE)
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("fees", help="show the fee profiles and where the numbers come from")
    p.set_defaults(func=cmd_fees)

    p = sub.add_parser("backtest", help="backtest strategies vs buy & hold, gross and net of costs")
    market(p)
    cost_opts(p)
    p.add_argument("--strategies", default="buy_hold all", help='e.g. "all", "ema_cross rsi_reversion:buy_below=25", "ai:model=haiku"')
    p.add_argument("--start", default=config.BACKTEST_START)
    p.add_argument("--end", default="")
    p.add_argument("--split", default=config.SPLIT_DATE, help="in-sample / out-of-sample boundary, or 'none'")
    p.add_argument("--capital", type=float, default=config.CAPITAL)
    p.add_argument("--deposit-fee", type=float, default=0.0, help="one-off cost to get money onto the exchange, e.g. 0.02 for a card buy")
    p.add_argument("--withdraw-fee", type=float, default=0.0, help="one-off cost to get money back to your bank")
    p.add_argument("--tax", type=float, default=0.0, help="tax rate on a positive gain, e.g. 0.15")
    p.add_argument("--per-pair", action="store_true", help="also show the net return of each pair")
    p.set_defaults(func=cmd_backtest)

    p = sub.add_parser("optimize", help="grid-search parameters in-sample and check them out-of-sample")
    market(p)
    cost_opts(p)
    p.add_argument("strategy", choices=[n for n, c in REGISTRY.items() if c.param_grid])
    p.add_argument("--start", default=config.BACKTEST_START)
    p.add_argument("--split", default=config.SPLIT_DATE)
    p.add_argument("--end", default="")
    p.add_argument("--metric", default="sharpe", choices=["sharpe", "return_%", "calmar"])
    p.add_argument("--min-trades", type=int, default=20)
    p.add_argument("--top", type=int, default=15)
    p.set_defaults(func=cmd_optimize)

    p = sub.add_parser("lookahead", help="check strategies for accidental use of future candles")
    market(p)
    p.add_argument("--strategies", default="all")
    p.set_defaults(func=cmd_lookahead)

    p = sub.add_parser("paper", help="paper trade on live prices (simulated fills, no real orders)")
    market(p)
    cost_opts(p)
    p.add_argument("--name", default="default", help="run name; re-using a name resumes that run")
    p.add_argument("--strategies", default="", help="required for a new run, e.g. \"all\" or \"ema_cross ai:model=haiku\"")
    p.add_argument("--capital", type=float, default=config.CAPITAL)
    p.add_argument("--poll", type=int, default=30, help="seconds between price checks")
    p.set_defaults(func=cmd_paper)

    p = sub.add_parser("copylab", help="walk-forward test of finding and copying Hyperliquid wallets on past data")
    p.add_argument("--first", default="2026-04-26", help="start of the first copy month")
    p.add_argument("--months", type=int, default=6)
    p.add_argument("--k", type=int, default=10, help="leaders copied at once")
    p.add_argument("--lookback", type=int, default=90, help="days of history the finder may look at")
    p.add_argument("--capital", type=float, default=10_000.0)
    p.add_argument("--delays", type=int, nargs="+", default=[10, 120], help="seconds between the leader's fill and ours")
    p.add_argument("--slippage", type=float, default=0.0003)
    p.add_argument("--no-funding", action="store_true", help="skip funding payments (faster first look)")
    p.set_defaults(func=cmd_copylab)

    p = sub.add_parser("status", help="show how a paper run is doing")
    p.add_argument("--name", default="default")
    p.set_defaults(func=cmd_status)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
