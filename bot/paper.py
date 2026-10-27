"""Live paper trading: real prices from the exchange, simulated fills. Nothing is ever sent to the exchange.

Each strategy gets its own virtual portfolio of `capital`, split equally across pairs, exactly like
the backtester. A buy-and-hold portfolio always runs alongside as the benchmark.

Fills happen at the live best ask (buys) / best bid (sells) when a new candle closes, plus the
taker fee. The real spread is therefore paid for real; on top of that every fill is logged next to
the candle close that triggered it, so `fills.csv` shows the slippage we'd actually have had.
"""
from __future__ import annotations

import csv
import json
import os
import time
import traceback
from dataclasses import asdict
from pathlib import Path

import ccxt
import pandas as pd

from . import config, data
from .backtest import Account, Costs
from .strategies import make

FILL_FIELDS = ["time", "strategy", "pair", "side", "reason", "ref_price", "fill_price", "qty", "fee", "slip_bps"]
TRADE_FIELDS = ["strategy", "pair", "entry_time", "entry_price", "exit_time", "exit_price", "qty", "cost", "proceeds", "pnl", "ret_%", "reason"]


def run_dir(name: str) -> Path:
    return config.RUNS_DIR / "paper" / name


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(10):  # cloud sync / antivirus can briefly lock the file
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.5 * (attempt + 1))
    raise PermissionError(f"Could not replace {path}")


def _append(path: Path, fields: list[str], row: dict) -> None:
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


class PaperTrader:
    def __init__(self, name: str, specs: list[str] | None = None, pairs: list[str] | None = None,
                 timeframe: str = config.TIMEFRAME, capital: float = config.CAPITAL, costs: Costs = Costs(),
                 exchange: str = config.EXCHANGE, poll: int = 30, history: int = 1000):
        self.dir = run_dir(name)
        self.state_path = self.dir / "state.json"
        self.poll, self.history = poll, history
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if specs and specs != self.state["specs"]:
                raise ValueError(f"Run {name!r} already exists with strategies {self.state['specs']}. Use another --name.")
        else:
            if not specs or not pairs:
                raise ValueError("A new run needs strategies and pairs")
            specs = ["buy_hold", *[s for s in specs if s != "buy_hold"]]
            self.state = {
                "started": pd.Timestamp.now(tz="UTC").isoformat(), "exchange": exchange, "timeframe": timeframe,
                "pairs": pairs, "specs": specs, "capital": capital, "costs": asdict(costs), "last_bar": {}, "accounts": {},
            }
        st = self.state
        self.costs = Costs(**st["costs"])
        self.strategies = {spec: make(spec) for spec in st["specs"]}
        self.ex = data.make_exchange(st["exchange"])
        self.tf_ms = data.timeframe_ms(st["timeframe"])
        self.accounts: dict[str, Account] = {}
        per = st["capital"] / len(st["pairs"])
        for spec, strat in self.strategies.items():
            for pair in st["pairs"]:
                key = f"{spec}|{pair}"
                saved = st["accounts"].get(key)
                # Live fills already pay the real spread, so no extra modeled slippage here.
                self.accounts[key] = Account(**saved) if saved else Account(per, self.costs.fee, 0.0, strat.stoploss, strat.takeprofit)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.save()

    # ---- persistence -------------------------------------------------------------------------
    def save(self) -> None:
        self.state["accounts"] = {k: {**asdict(a), "entry_time": str(a.entry_time) if a.entry_time else None}
                                  for k, a in self.accounts.items()}
        _write_atomic(self.state_path, json.dumps(self.state, indent=1))

    def log(self, msg: str) -> None:
        line = f"{pd.Timestamp.now(tz=config.DISPLAY_TZ):%Y-%m-%d %H:%M:%S %Z} {msg}"
        print(line, flush=True)
        with (self.dir / "log.txt").open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    # ---- order simulation --------------------------------------------------------------------
    def _fill(self, spec: str, pair: str, side: str, reason: str, price: float, ref_price: float) -> None:
        acct = self.accounts[f"{spec}|{pair}"]
        now = pd.Timestamp.now(tz="UTC").isoformat()
        fees_before = acct.fees_paid
        if side == "buy":
            acct.buy(price, now)
            qty = acct.qty
        else:
            qty = acct.qty
            t = acct.sell(price, now, reason, pair)
            _append(self.dir / "trades.csv", TRADE_FIELDS, {
                "strategy": spec, "pair": pair, "entry_time": t.entry_time, "entry_price": t.entry_price, "exit_time": t.exit_time,
                "exit_price": t.exit_price, "qty": t.qty, "cost": round(t.cost, 4), "proceeds": round(t.proceeds, 4),
                "pnl": round(t.pnl, 4), "ret_%": round(t.ret * 100, 3), "reason": reason})
        sign = 1 if side == "buy" else -1
        _append(self.dir / "fills.csv", FILL_FIELDS, {
            "time": now, "strategy": spec, "pair": pair, "side": side, "reason": reason, "ref_price": ref_price,
            "fill_price": price, "qty": qty, "fee": round(acct.fees_paid - fees_before, 6),
            "slip_bps": round(sign * (price / ref_price - 1) * 1e4, 2)})
        self.log(f"{side.upper():4} {pair:<10} {spec:<40} @ {price:.6g} ({reason})")

    def step(self) -> None:
        st = self.state
        now = pd.Timestamp.now(tz="UTC")
        tickers = self.ex.fetch_tickers(st["pairs"])
        last_closed = pd.Timestamp((int(now.timestamp() * 1000) // self.tf_ms - 1) * self.tf_ms, unit="ms", tz="UTC")
        new_bar = False
        for pair in st["pairs"]:
            tk = tickers[pair]
            last = tk["last"]
            bid, ask = tk.get("bid") or last, tk.get("ask") or last

            # Stops and take-profits are checked on every poll, not just at candle close.
            for spec in self.strategies:
                acct = self.accounts[f"{spec}|{pair}"]
                if acct.in_position and acct.stop_price and last <= acct.stop_price:
                    self._fill(spec, pair, "sell", "stoploss", bid, acct.stop_price)
                elif acct.in_position and acct.tp_price and last >= acct.tp_price:
                    self._fill(spec, pair, "sell", "takeprofit", bid, acct.tp_price)

            if st["last_bar"].get(pair) == last_closed.isoformat():
                continue
            age = now - (last_closed + pd.Timedelta(milliseconds=self.tf_ms))
            if age > pd.Timedelta(seconds=max(3 * self.poll, 180)):
                # Started (or resumed) mid-candle: acting now would fill far from the candle's close and
                # wouldn't match the backtest. Wait for the next close instead.
                next_close = (last_closed + 2 * pd.Timedelta(milliseconds=self.tf_ms)).tz_convert(config.DISPLAY_TZ)
                self.log(f"{pair}: candle closed {age.total_seconds() / 60:.0f} min ago, waiting for the next one at {next_close:%H:%M}")
                st["last_bar"][pair] = last_closed.isoformat()
                continue
            df = data.fetch_recent(self.ex, pair, st["timeframe"], self.history)
            if df.empty or df.index[-1] != last_closed:
                continue  # exchange hasn't published the closed candle yet; try next poll
            ref = df["close"].iloc[-1]
            for spec, strat in self.strategies.items():
                try:
                    sig = strat.signals(df, start=len(df) - 1).iloc[-1]
                except Exception as e:  # one broken strategy (e.g. AI call failing) must not stop the others
                    self.log(f"ERROR {spec} on {pair}: {type(e).__name__}: {e}")
                    continue
                acct = self.accounts[f"{spec}|{pair}"]
                if acct.in_position and sig["exit"]:
                    self._fill(spec, pair, "sell", "signal", bid, ref)
                elif not acct.in_position and sig["enter"] and not sig["exit"]:
                    self._fill(spec, pair, "buy", "signal", ask, ref)
            st["last_bar"][pair] = last_closed.isoformat()
            new_bar = True

        if new_bar:
            prices = {p: tickers[p]["last"] for p in st["pairs"]}
            for spec in self.strategies:
                eq = sum(self.accounts[f"{spec}|{p}"].equity(prices[p]) for p in st["pairs"])
                _append(self.dir / "equity.csv", ["time", "strategy", "equity"],
                        {"time": now.isoformat(), "strategy": spec, "equity": round(eq, 4)})
        self.save()

    def run(self) -> None:
        st = self.state
        self.log(f"Paper trading {st['specs']} on {st['pairs']} ({st['timeframe']}), state in {self.dir}")
        self.log("Simulated fills only - no orders are sent to the exchange. Ctrl+C to stop (state is kept).")
        try:
            while True:
                try:
                    self.step()
                except (ccxt.NetworkError, ccxt.ExchangeError) as e:
                    self.log(f"exchange error, retrying next poll: {type(e).__name__}: {e}")
                except Exception:
                    self.log("unexpected error:\n" + traceback.format_exc())
                time.sleep(self.poll)
        except KeyboardInterrupt:
            self.save()
            self.log("stopped")


def status(name: str) -> pd.DataFrame:
    """Current value of every strategy in a paper run, marked at live prices."""
    path = run_dir(name) / "state.json"
    if not path.exists():
        raise FileNotFoundError(f"No paper run named {name!r} in {run_dir(name).parent}")
    st = json.loads(path.read_text(encoding="utf-8"))
    ex = data.make_exchange(st["exchange"])
    prices = {p: t["last"] for p, t in ex.fetch_tickers(st["pairs"]).items()}
    trades_path = run_dir(name) / "trades.csv"
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame(columns=TRADE_FIELDS)
    rows = []
    for spec in st["specs"]:
        accts = [Account(**st["accounts"][f"{spec}|{p}"]) for p in st["pairs"]]
        eq = sum(a.equity(prices[p]) for a, p in zip(accts, st["pairs"]))
        mine = trades[trades["strategy"] == spec]
        rows.append({
            "strategy": spec, "equity": eq, "return_%": (eq / st["capital"] - 1) * 100,
            "fees": sum(a.fees_paid for a in accts), "closed_trades": len(mine),
            "win_%": (mine["pnl"] > 0).mean() * 100 if len(mine) else float("nan"),
            "open": ", ".join(p for a, p in zip(accts, st["pairs"]) if a.in_position) or "-",
        })
    df = pd.DataFrame(rows).set_index("strategy")
    hold = df.loc["buy_hold", "return_%"]
    df.insert(2, "vs_hold_%", df["return_%"] - hold)
    df.attrs["started"] = st["started"]
    return df
