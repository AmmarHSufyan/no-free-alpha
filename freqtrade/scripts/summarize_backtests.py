"""Summarize exported Freqtrade backtest results into a markdown table.

Runs inside the Freqtrade container:
    docker compose run --rm -T --entrypoint python freqtrade /freqtrade/scripts/summarize_backtests.py

Reads every backtest-result-*.zip under user_data/backtest_results/<period>/ and prints, per
period, one row per strategy (latest result wins if a strategy was run more than once).
"""

import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path("/freqtrade/user_data/backtest_results")
PERIODS = sys.argv[1:] or ["full", "recent6m"]


def load_result(zpath: Path) -> dict:
    with zipfile.ZipFile(zpath) as zf:
        names = [
            n
            for n in zf.namelist()
            if n.endswith(".json") and not n.endswith("_config.json") and "_strategy" not in n
        ]
        # main result json is named like the zip
        main = [n for n in names if Path(n).stem == zpath.stem] or names
        return json.loads(zf.read(main[0]))


def pct(x, nd=2):
    return "n/a" if x is None else f"{x * 100:.{nd}f}%"


def num(x, nd=2):
    return "n/a" if x is None else f"{x:.{nd}f}"


rows_by_period = {}
for period in PERIODS:
    pdir = ROOT / period
    latest = {}
    for z in sorted(pdir.glob("backtest-result-*.zip")):
        try:
            data = load_result(z)
        except Exception as e:  # noqa: BLE001
            print(f"WARN could not read {z.name}: {e}", file=sys.stderr)
            continue
        for name, st in data.get("strategy", {}).items():
            latest[name] = (z.name, st)
    rows = []
    for log in sorted(pdir.glob("*.log")):
        if log.stem not in latest:
            txt = log.read_text(errors="replace").strip().splitlines()
            err = next((ln for ln in reversed(txt) if "ERROR" in ln or "Error" in ln), txt[-1] if txt else "")
            print(f"FAILED [{period}] {log.stem}: {err[-300:]}", file=sys.stderr)
    for name, (zname, st) in latest.items():
        sharpe_daily = None
        log = pdir / f"{name}.log"
        if log.exists():
            m = re.search(r"Sharpe \(daily wallet balance\)\s*│\s*(-?[\d.]+)", log.read_text(errors="replace"))
            sharpe_daily = float(m.group(1)) if m else None
        rows.append(
            {
                "strategy": name,
                "timeframe": st.get("timeframe"),
                "start": str(st.get("backtest_start"))[:10],
                "end": str(st.get("backtest_end"))[:10],
                "profit_total": st.get("profit_total"),
                "profit_abs": st.get("profit_total_abs"),
                "trades": st.get("total_trades"),
                "winrate": st.get("winrate"),
                "max_dd": st.get("max_drawdown_account"),
                "sharpe": st.get("sharpe"),
                "sharpe_daily": sharpe_daily,
                "profit_factor": st.get("profit_factor"),
                "market_change": st.get("market_change"),
                "file": zname,
            }
        )
    rows.sort(key=lambda r: (r["profit_total"] is None, -(r["profit_total"] or 0)))
    rows_by_period[period] = rows

for period, rows in rows_by_period.items():
    if not rows:
        print(f"\n## {period}: no results\n")
        continue
    print(f"\n## {period}  ({rows[0]['start']} -> {rows[0]['end']})\n")
    print(
        "| Strategy | TF | Total profit % | Profit USDT | Trades | Win rate | Max DD (account) "
        "| Sharpe (trades) | Sharpe (daily wallet) | Profit factor | Market change (B&H) |"
    )
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(
            f"| {r['strategy']} | {r['timeframe']} | {pct(r['profit_total'])} | {num(r['profit_abs'])} "
            f"| {r['trades']} | {pct(r['winrate'], 1)} | {pct(r['max_dd'])} | {num(r['sharpe'])} "
            f"| {num(r['sharpe_daily'])} | {num(r['profit_factor'])} | {pct(r['market_change'])} |"
        )

json.dump(rows_by_period, open(ROOT / "summary.json", "w"), indent=2, default=str)
print(f"\nwrote {ROOT / 'summary.json'}")
