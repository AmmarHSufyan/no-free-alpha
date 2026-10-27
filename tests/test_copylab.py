import numpy as np
import pandas as pd
import pytest

from bot.copylab.copier import CopyCosts, Prices, copy_leader
from bot.copylab.finder import equity_at, select

MIN = 60_000
HOUR = 3_600_000
T0 = 1_780_000_000_000 // HOUR * HOUR  # an hour boundary


def prices(opens, coin="BTC"):
    idx = pd.to_datetime([T0 - 10 * MIN + i * MIN for i in range(len(opens))], unit="ms", utc=True)
    return Prices({coin: pd.DataFrame({"open": opens}, index=idx)})


def fills(rows):
    """rows: (minutes after T0, seconds, signed size, price, position before)"""
    return pd.DataFrame([{"time": T0 + m * MIN + s * 1000, "coin": "BTC", "signed": q, "px": px, "startPosition": sp}
                         for m, s, q, px, sp in rows])


FLAT = [100.0] * 10 + [100.0] * 5 + [110.0] * 200  # index 10 == T0


def test_ideal_copy_matches_leader_pnl_scaled_to_our_allocation():
    f = fills([(1, 0, +1, 100, 0), (10, 0, -1, 110, 1)])  # leader: buy 1 @100, sell @110, account $1000
    r = copy_leader(f, T0, T0 + 2 * HOUR, leader_equity=1000, alloc=2000, prices=prices(FLAT), funding={},
                    costs=CopyCosts(ideal=True))
    assert r.equity.iloc[-1] == pytest.approx(2000 + 2 * 10)  # we're 2x the leader's size
    assert r.trades == 2 and r.fees == 0


def test_delayed_copy_pays_the_move_plus_fee_and_slippage():
    p = [100.0] * 11 + [105.0] * 200  # price jumps from 100 to 105 one minute after T0
    f = fills([(0, 30, +1, 100, 0)])  # leader buys @100 at T0+30s; we act at the next minute (T0+1m) -> 105
    c = CopyCosts(delay_s=10, fee=0.001, slippage=0.0)
    r = copy_leader(f, T0, T0 + HOUR, leader_equity=1000, alloc=1000, prices=prices(p), funding={}, costs=c)
    buy, sell = 105.0, 105.0
    expected = 1000 - buy * 1.001 + sell * 0.999
    assert r.equity.iloc[-1] == pytest.approx(expected)


def test_leverage_is_capped_relative_to_our_equity():
    f = fills([(1, 0, +500, 100, 0)])  # leader runs 50x on a $1000 account
    r = copy_leader(f, T0, T0 + 2 * HOUR, leader_equity=1000, alloc=1000, prices=prices(FLAT), funding={},
                    costs=CopyCosts(ideal=True, lev_cap=3.0))
    # capped at 3x of $1000 = 30 BTC at 100; price goes to 110 -> +$300
    assert r.equity.iloc[-1] == pytest.approx(1300)


def test_longs_pay_positive_funding_every_hour():
    f = fills([(0, 0, +10, 100, 0)])
    rate = 0.001
    fund = {"BTC": {T0 + HOUR: rate, T0 + 2 * HOUR: rate}}
    r = copy_leader(f, T0, T0 + 3 * HOUR, 1000, 1000, prices([100.0] * 300), fund, CopyCosts(delay_s=0, fee=0, slippage=0))
    assert r.funding == pytest.approx(2 * 10 * 100 * rate)
    assert r.equity.iloc[-1] == pytest.approx(1000 - 2.0)


def test_equity_wipeout_is_a_liquidation():
    p = [100.0] * 15 + [60.0] * 200  # -40% while we hold 3x long
    f = fills([(0, 0, +30, 100, 0)])
    r = copy_leader(f, T0, T0 + 3 * HOUR, 1000, 1000, prices(p), {}, CopyCosts(ideal=True))
    assert r.liquidated and r.equity.iloc[-1] == 0


def test_positions_open_at_start_are_copied():
    before = fills([(-60, 0, +1, 100, 0)])  # leader bought an hour before our window starts
    r = copy_leader(before, T0, T0 + HOUR, 1000, 1000, prices(FLAT), {}, CopyCosts(ideal=True))
    assert r.trades == 2 and r.equity.iloc[-1] == pytest.approx(1010)


def test_exchange_price_gap_creates_no_profit():
    """Hyperliquid can trade a few % away from Binance; only Binance price *changes* may count (bug fixed 2026-10-26)."""
    p = [100.0] * 15 + [110.0] * 200            # Binance: 100 -> 110
    f = fills([(1, 30, +1, 97, 0), (10, 30, -1, 106.7, 1)])  # leader fills 3% below Binance both times
    r = copy_leader(f, T0, T0 + HOUR, 1000, 1000, prices(p), {}, CopyCosts(delay_s=0, fee=0, slippage=0))
    assert r.equity.iloc[-1] == pytest.approx(1000 + 10)  # +10 from Binance's move, nothing from the 3% gap


def test_maker_order_fills_only_when_price_trades_through():
    f = fills([(0, 30, +1, 100, 0)])                       # leader buys; we post at the next open (100, minute 1)
    dip = [100.0] * 12 + [99.0] + [100.0] * 200            # minute 2 trades below our price -> filled at 100
    run = [100.0] * 11 + [101.0, 102.0, 103.0, 104.0, 105.0] + [105.0] * 200  # never comes back down
    maker = CopyCosts(delay_s=0, slippage=0.0, fee=0.0, maker=True, maker_fee=0.0, maker_wait_min=5)  # fee=0: the final close is a taker trade
    r = copy_leader(f, T0, T0 + HOUR, 1000, 1000, prices(dip), {}, maker)
    assert r.fees == 0 and r.equity.iloc[-1] == pytest.approx(1000)  # bought at 100, still 100 at the end
    chase = copy_leader(f, T0, T0 + HOUR, 1000, 1000, prices(run), {}, CopyCosts(delay_s=0, slippage=0.0, fee=0.0, maker=True))
    assert chase.equity.iloc[-1] == pytest.approx(1000)     # chased at 105 after 5 min, worth 105 at the end
    skip = copy_leader(f, T0, T0 + HOUR, 1000, 1000, prices(run), {}, CopyCosts(delay_s=0, maker=True, maker_fallback="skip"))
    assert skip.trades == 0


def test_equity_at_uses_latest_snapshot_before_time():
    hist = [(1, 10.0), (5, 50.0), (9, 90.0)]
    assert equity_at(hist, 6) == 50.0 and np.isnan(equity_at(hist, 0))


def test_finders_respect_filters():
    stats = pd.DataFrame({
        "capped": [False, False, True], "supported_share": [0.9, 0.9, 0.9], "equity": [5e3, 5e3, 5e3],
        "week_closes": [5, 5, 5], "week_ret_%": [50.0, 10.0, 99.0], "closes": [40, 40, 40],
        "months_profitable": [3, 1, 3], "net": [1, 1, 1], "tstat": [3, 3, 3], "fills_per_day": [5, 5, 5],
        "sharpe": [2, 3, 9], "month_fills": [10, 10, 10]}, index=["a", "b", "capped"])
    assert select("hot", stats, 2) == ["a", "b"]       # the capped bot is excluded even though it's "hottest"
    assert select("steady", stats, 2) == ["a"]         # b fails the months-profitable test
