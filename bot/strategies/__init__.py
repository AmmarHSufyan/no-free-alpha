"""Strategy registry. Specs look like `rsi_reversion` or `rsi_reversion:buy_below=25,stoploss=0.05`."""
from __future__ import annotations

from .ai import ClaudeTrader
from .base import Strategy
from .classic import BollingerReversion, BuyAndHold, DonchianBreakout, EmaCross, MacdTrend, RsiReversion

REGISTRY: dict[str, type[Strategy]] = {
    cls.name: cls for cls in (BuyAndHold, EmaCross, RsiReversion, BollingerReversion, DonchianBreakout, MacdTrend, ClaudeTrader)
}
CLASSIC = [name for name in REGISTRY if name != ClaudeTrader.name]


def _value(text: str):
    if text.lower() in ("none", "null"):
        return None
    if text.lower() in ("true", "false"):
        return text.lower() == "true"
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return text


def make(spec: str) -> Strategy:
    name, _, args = spec.partition(":")
    if name not in REGISTRY:
        raise ValueError(f"Unknown strategy {name!r}. Available: {', '.join(REGISTRY)}")
    params = dict(kv.split("=", 1) for kv in args.split(",") if kv) if args else {}
    return REGISTRY[name](**{k: _value(v) for k, v in params.items()})


def split_specs(text: str) -> list[str]:
    """Specs are separated by spaces or `;`. `all` means every classic strategy (the AI one costs money, so it's opt-in)."""
    out = []
    for spec in text.replace(";", " ").split():
        out.extend(CLASSIC if spec == "all" else [spec])
    return list(dict.fromkeys(out))


def expand(text: str) -> list[Strategy]:
    return [make(spec) for spec in split_specs(text)]
