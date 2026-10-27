import numpy as np
import pandas as pd
import pytest

from bot import indicators as ind
from bot.backtest import Account, Costs, backtest, simulate
from bot.fees import PROFILES, Funding
from bot.lookahead import check
from bot.strategies import CLASSIC, make
from bot.strategies.base import Strategy
from bot.strategies.classic import BuyAndHold


def candles(closes, spread=0.0):
    closes = np.asarray(closes, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    idx = pd.date_range("2025-01-01", periods=len(closes), freq="h", tz="UTC")
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * (1 + spread),
                         "low": np.minimum(opens, closes) * (1 - spread), "close": closes, "volume": 1.0}, index=idx)


def random_walk(n=3000, seed=1):
    rng = np.random.default_rng(seed)
    return candles(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n))), spread=0.003)


class Signals(Strategy):
    """Enter / exit on explicitly listed bar numbers."""
    name = "fixed"
    defaults = {"enter_at": (), "exit_at": ()}

    def signals(self, df, start=0):
        e = pd.Series(False, index=df.index)
        x = pd.Series(False, index=df.index)
        e.iloc[list(self.p["enter_at"])] = True
        x.iloc[list(self.p["exit_at"])] = True
        return self.frame(df, e, x)


class Cheater(Strategy):
    name = "cheater"

    def signals(self, df, start=0):
        up_next = df["close"].shift(-1) > df["close"]
        return self.frame(df, up_next, ~up_next)


def test_buy_and_hold_pays_fee_and_slippage_both_ways():
    df = candles([100, 110, 120, 130])
    res = backtest({"X": df}, BuyAndHold(), capital=1000, costs=Costs(fee=0.001, slippage=0.0005))
    # signal on bar 0 close -> buy at bar 1 open (=100), liquidated at the final close (=130)
    entry = 100 * 1.0005
    qty = 1000 * 0.999 / entry
    expected = qty * 130 * 0.9995 * 0.999
    assert res.equity.iloc[-1] == pytest.approx(expected)
    assert res.fees == pytest.approx(1000 * 0.001 + qty * 130 * 0.9995 * 0.001)
    assert res.slippage == pytest.approx(qty * (entry - 100) + qty * 130 * 0.0005)


def test_orders_fill_at_next_open_not_signal_close():
    df = candles([100, 101, 102, 103, 104, 105])
    res = simulate(df, Signals(enter_at=[1], exit_at=[3]).signals(df), 1000, Costs(0, 0))
    t = res.trades[0]
    assert t.entry_time == df.index[2] and t.entry_price == df["open"].iloc[2]
    assert t.exit_time == df.index[4] and t.exit_price == df["open"].iloc[4]


def test_stoploss_fills_at_open_when_price_gaps_through_it():
    df = candles([100, 100, 100, 80, 80])
    df.loc[df.index[3], "open"] = 85  # gap down far below the 5% stop
    res = simulate(df, Signals(enter_at=[0]).signals(df), 1000, Costs(0, 0), stoploss=0.05)
    t = res.trades[0]
    assert t.reason == "stoploss" and t.exit_price == 85


def test_costs_only_ever_reduce_returns():
    df = random_walk()
    for spec in CLASSIC:
        s = make(spec)
        free = backtest({"X": df}, s, costs=Costs(0, 0)).equity.iloc[-1]
        paid = backtest({"X": df}, s, costs=PROFILES["binance"].costs()).equity.iloc[-1]
        assert paid <= free + 1e-9, spec


def test_lookahead_check_catches_cheater_and_passes_builtins():
    df = random_walk(1500)
    assert check(Cheater(), df)
    for spec in CLASSIC:
        assert check(make(spec), df) == [], spec


def test_rsi_bounds_and_ema_matches_pandas():
    close = random_walk()["close"]
    r = ind.rsi(close).dropna()
    assert r.between(0, 100).all()
    assert ind.ema(close, 20).iloc[-1] == pytest.approx(close.ewm(span=20, adjust=False).mean().iloc[-1])


def test_account_round_trip_without_costs_is_exact():
    a = Account(1000, fee=0, slippage=0)
    a.buy(50, "t0")
    t = a.sell(60, "t1", "signal")
    assert a.cash == pytest.approx(1200) and t.ret == pytest.approx(0.2)


def test_take_home_applies_onramp_offramp_and_tax_on_gains_only():
    f = Funding(deposit=0.02, withdraw=0.01, tax=0.2)
    assert f.take_home(0.10) == pytest.approx(((0.98 * 1.10 * 0.99) - 1) * 0.8)
    assert f.take_home(-0.10) == pytest.approx(0.98 * 0.90 * 0.99 - 1)  # no tax on a loss


def test_strategy_specs_parse_params():
    s = make("rsi_reversion:buy_below=25,stoploss=none")
    assert s.p["buy_below"] == 25 and s.stoploss is None
    assert s.label == "rsi_reversion(buy_below=25,stoploss=None)"
    with pytest.raises(ValueError):
        make("rsi_reversion:nonsense=1")
