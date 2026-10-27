#!/usr/bin/env bash
# Backtest every strategy in STRATEGIES over the FULL period and the RECENT ~6 months.
# Runs everything inside the Freqtrade container (third-party strategy code never runs on the host).
#
# Usage (from the freqtrade/ folder, Git Bash):
#   ./scripts/run_backtests.sh                 # both periods, all strategies
#   PERIODS="recent6m" ./scripts/run_backtests.sh
#   STRATEGIES="BbandRsi Supertrend" ./scripts/run_backtests.sh
# Then: docker compose run --rm -T --entrypoint python freqtrade /freqtrade/scripts/summarize_backtests.py
set -u
cd "$(dirname "$0")/.."

FULL_RANGE="${FULL_RANGE:-20241001-}"
RECENT_RANGE="${RECENT_RANGE:-20260426-}"
PERIODS="${PERIODS:-full recent6m}"
FEE="${FEE:-0.001}"   # Binance spot default taker fee 0.1%, applied on entry and exit
STRATEGIES="${STRATEGIES:-SampleStrategy Strategy001 Strategy002 Strategy003 Strategy004 Strategy005 BbandRsi CombinedBinHAndCluc ClucMay72018 ADXMomentum Supertrend}"

for period in $PERIODS; do
  case "$period" in
    full) range="$FULL_RANGE" ;;
    recent6m) range="$RECENT_RANGE" ;;
    *) echo "unknown period $period"; exit 1 ;;
  esac
  outdir="user_data/backtest_results/$period"
  mkdir -p "$outdir"
  for s in $STRATEGIES; do
    echo "=== [$period $range] $s"
    docker compose run --rm -T freqtrade backtesting \
      --config user_data/config.json \
      --strategy "$s" \
      --timerange "$range" \
      --fee "$FEE" \
      --cache none \
      --export trades \
      --backtest-directory "$outdir" \
      > "$outdir/$s.log" 2>&1
    rc=$?
    echo "    exit=$rc  log=$outdir/$s.log"
  done
done
