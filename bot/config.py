"""Project-wide defaults. Everything here can be overridden from the command line."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RUNS_DIR = ROOT / "runs"

EXCHANGE = "binance"
PAIRS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME = "1h"

# Extra history before BACKTEST_START is downloaded so indicators are warmed up on day one.
DOWNLOAD_SINCE = "2024-06-01"
BACKTEST_START = "2024-10-01"
# Everything before SPLIT_DATE is "in-sample" (fine to tune on), everything after is "out-of-sample".
SPLIT_DATE = "2026-04-01"

# Time zone for logs and status output (e.g. BOT_TZ=Europe/London). Candle data itself stays in UTC (that's how
# exchanges publish it).
DISPLAY_TZ = os.environ.get("BOT_TZ", "UTC")

CAPITAL = 10_000.0
FEE = 0.001        # 0.1% per fill (Binance spot taker without BNB discount)
SLIPPAGE = 0.0005  # 5 bps against us on every fill
