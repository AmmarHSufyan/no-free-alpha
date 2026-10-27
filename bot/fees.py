"""Realistic trading costs per venue, so a backtest's "profit" is what you'd actually keep.

Per-trade costs (applied on every fill by the engine):
  * fee: the exchange's commission. The bot uses market orders, so it pays the *taker* rate.
  * slippage: what a market order loses to the bid/ask spread and to the price moving between
    the signal and the fill. For small orders on the big USDT pairs that's a few basis points.

One-off costs (applied to the final result only, see `take_home`):
  * deposit / withdrawal: e.g. buying USDT with a card costs ~2-3.5%, a bank or P2P deposit is cheaper.
  * tax on profit: depends entirely on your country; 0 by default.

Fee schedules change and differ by region and volume tier. Check your own account's fee page
and override with --fee / --slippage if yours differ.
"""
from __future__ import annotations

from dataclasses import dataclass

from .backtest import Costs


@dataclass(frozen=True)
class FeeProfile:
    key: str
    venue: str
    maker: float
    taker: float
    slippage: float
    checked: str  # where and when the fee number was confirmed
    note: str = ""

    def costs(self, fee: float | None = None, slippage: float | None = None) -> Costs:
        return Costs(fee=self.taker if fee is None else fee, slippage=self.slippage if slippage is None else slippage)


PROFILES = {p.key: p for p in [
    FeeProfile("binance", "Binance spot, regular user (VIP 0)", 0.0010, 0.0010, 0.0005,
               "binance.com/en/fee/trading, 2026-10-26"),
    FeeProfile("binance_bnb", "Binance spot, VIP 0, fees paid in BNB (25% off)", 0.00075, 0.00075, 0.0005,
               "binance.com/en/fee/trading, 2026-10-26",
               "Requires holding BNB; the BNB you spend on fees is itself exposed to BNB's price."),
    FeeProfile("bybit", "Bybit spot, VIP 0", 0.0010, 0.0010, 0.0005,
               "third-party summaries of Bybit's Sep 2026 schedule (not verified on bybit.com)"),
    FeeProfile("okx", "OKX spot, regular user", 0.0008, 0.0010, 0.0005,
               "third-party summaries, Sep 2026 (not verified on okx.com)"),
    FeeProfile("kraken", "Kraken Pro spot, lowest volume tier", 0.0040, 0.0080, 0.0005,
               "unverified: sources conflict (0.25%/0.40% vs 0.40%/0.80% after a Jul 2026 change); using the higher",
               "Check kraken.com/features/fee-schedule for your tier."),
    FeeProfile("coinbase", "Coinbase Advanced, lowest volume tier", 0.0040, 0.0060, 0.0005,
               "unverified: commonly cited entry tier; Coinbase shows your tier only when signed in"),
    FeeProfile("convert", "In-app Convert / instant buy (no fee, priced into the spread)", 0.0, 0.0, 0.0075,
               "third-party guides, Jan 2026: spread ~0.1% on majors up to 1-2% on small coins; 0.75% is a middle guess",
               "Convenient, but usually the most expensive way to trade often."),
    FeeProfile("free", "No costs at all (only for seeing a strategy's raw edge)", 0.0, 0.0, 0.0, "-"),
]}

DEFAULT = "binance"


@dataclass(frozen=True)
class Funding:
    deposit: float = 0.0   # e.g. 0.02 for a 2% card purchase of USDT
    withdraw: float = 0.0  # e.g. 0.01 to sell USDT back to your bank
    tax: float = 0.0       # tax rate on a positive gain

    def take_home(self, net_return: float) -> float:
        """Return on the money that left your bank, after on-ramp, off-ramp and tax."""
        gain = (1 - self.deposit) * (1 + net_return) * (1 - self.withdraw) - 1
        return gain * (1 - self.tax) if gain > 0 else gain

    @property
    def active(self) -> bool:
        return any((self.deposit, self.withdraw, self.tax))


def describe() -> str:
    lines = [f"{'profile':<12} {'maker':>7} {'taker':>7} {'slippage':>9}  venue / where checked"]
    for p in PROFILES.values():
        lines.append(f"{p.key:<12} {p.maker:>7.3%} {p.taker:>7.3%} {p.slippage:>9.3%}  {p.venue}")
        lines.append(f"{'':<44}checked: {p.checked}")
        if p.note:
            lines.append(f"{'':<44}note: {p.note}")
    lines.append("")
    lines.append("The bot sends market orders, so it pays the taker rate plus slippage on every buy and every sell.")
    lines.append("A round trip on Binance VIP 0 therefore costs about 2 x (0.10% + 0.05%) = 0.30%.")
    return "\n".join(lines)
