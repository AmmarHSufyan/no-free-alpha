# Freqtrade benchmark results

**Date:** 2026-10-26 (backtests run about 03:00 UTC)
**Mode:** backtest and dry-run only. No live trading, no exchange keys.

## Setup

| Item | Value |
|---|---|
| Freqtrade | 2026.9 (`freqtradeorg/freqtrade:stable`, Python 3.14.7, CCXT 4.5.84), Docker Desktop 29.2.1 |
| Exchange / market | Binance spot, stake currency USDT |
| Pairs (StaticPairList) | BTC/USDT, ETH/USDT, SOL/USDT, BNB/USDT, XRP/USDT |
| Wallet / sizing | `dry_run_wallet` 10,000 USDT, `max_open_trades` 5, `stake_amount` "unlimited", `tradable_balance_ratio` 0.99 |
| Fees | `--fee 0.001` (0.1%, Binance spot default), charged on entry and exit |
| Data | 5m and 1h candles from 2024-09-20 (warm-up) to 2026-10-26 02:45 UTC. 220,642 5m and 18,386 1h candles per pair, no gaps |
| Full period | `--timerange 20241001-`: 2024-10-01 to 2026-10-26 (about 755 days) |
| Recent period | `--timerange 20260426-`: 2026-04-26 to 2026-10-26 (183 days) |
| Strategy source | github.com/freqtrade/freqtrade-strategies @ `f3340ce` (2026-09-08), plus the built-in SampleStrategy |
| Protections / hyperopt | None. Strategies run with their published default parameters. No hyperopt was run |

Strategies tested (all INTERFACE_VERSION 3; none needed porting, none errored):
`SampleStrategy` (built-in), `Strategy001`-`Strategy005`, `Supertrend` (from `user_data/strategies/`),
and `BbandRsi`, `CombinedBinHAndCluc`, `ClucMay72018`, `ADXMomentum` (from `berlinguyinca/`).

**Market change** is Freqtrade's buy-and-hold figure: the average % price change of the 5 pairs over the
backtest window. It differs slightly between 5m and 1h strategies because the last closed candle differs
(02:45 vs 01:00 UTC). "vs B&H" = total profit % minus market change, in percentage points.

**Max DD** = `max_drawdown_account`, the largest peak-to-trough drop of the account. Freqtrade 2026.9 reports two
Sharpe figures: **Sharpe (trades)** is computed from closed trades, and **Sharpe (daily)** from the daily wallet balance.

## (b) Recent ~6 months: 2026-04-26 to 2026-10-26 (this period decided the selection)

| # | Strategy | TF | Total profit % | Profit USDT | Trades | Win rate | Max DD | Sharpe (trades) | Sharpe (daily) | Profit factor | Market change | vs B&H |
|--:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | BbandRsi | 1h | **+8.77%** | +877.33 | 100 | 71.0% | 18.74% | 0.85 | 0.81 | 1.28 | +5.74% | **+3.03** |
| 2 | CombinedBinHAndCluc | 5m | +2.82% | +282.26 | 14 | 92.9% | 1.02% | 0.74 | 1.88 | 3.67 | +5.17% | -2.35 |
| 3 | ClucMay72018 | 5m | +2.01% | +201.24 | 12 | 91.7% | 0.18% | 1.97 | 2.72 | 11.87 | +5.17% | -3.16 |
| 4 | Strategy003 | 5m | +1.46% | +146.38 | 138 | 94.9% | 6.98% | 0.39 | 0.36 | 1.14 | +5.17% | -3.71 |
| 5 | Strategy002 | 5m | -1.32% | -132.12 | 72 | 94.4% | 5.78% | -0.32 | -0.41 | 0.80 | +5.17% | -6.49 |
| 6 | Strategy004 | 5m | -6.28% | -627.54 | 228 | 93.4% | 9.30% | -1.43 | -1.08 | 0.74 | +5.17% | -11.45 |
| 7 | Strategy005 | 5m | -11.03% | -1102.87 | 160 | 83.8% | 18.72% | -1.51 | -0.91 | 0.75 | +5.17% | -16.20 |
| 8 | Strategy001 | 5m | -14.02% | -1402.31 | 299 | 88.3% | 28.85% | -2.28 | -0.80 | 0.77 | +5.17% | -19.19 |
| 9 | Supertrend | 1h | -14.07% | -1406.84 | 275 | 48.7% | 19.56% | -2.40 | -0.93 | 0.80 | +5.74% | -19.81 |
| 10 | SampleStrategy | 5m | -16.08% | -1607.83 | 282 | 85.5% | 23.25% | -2.66 | -1.07 | 0.73 | +5.17% | -21.25 |
| 11 | ADXMomentum | 1h | -20.39% | -2038.64 | 405 | 73.8% | 21.75% | -5.01 | -1.90 | 0.73 | +5.74% | -26.13 |

## (a) Full period: 2024-10-01 to 2026-10-26

| # | Strategy | TF | Total profit % | Profit USDT | Trades | Win rate | Max DD | Sharpe (trades) | Sharpe (daily) | Profit factor | Market change | vs B&H |
|--:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | CombinedBinHAndCluc | 5m | **+20.42%** | +2041.63 | 198 | 78.3% | 9.25% | 0.73 | 1.33 | 1.42 | +23.84% | -3.42 |
| 2 | ClucMay72018 | 5m | +1.50% | +149.62 | 129 | 82.9% | 7.23% | 0.09 | 0.16 | 1.08 | +23.84% | -22.34 |
| 3 | Strategy003 | 5m | -14.15% | -1415.33 | 655 | 93.4% | 26.99% | -0.70 | -0.49 | 0.83 | +23.84% | -37.99 |
| 4 | BbandRsi | 1h | -16.18% | -1617.52 | 361 | 63.2% | 44.23% | -0.30 | -0.05 | 0.91 | +23.36% | -39.54 |
| 5 | Strategy002 | 5m | -21.91% | -2191.33 | 326 | 91.1% | 25.93% | -1.02 | -1.07 | 0.57 | +23.84% | -45.75 |
| 6 | Strategy004 | 5m | -33.81% | -3381.36 | 1033 | 92.7% | 41.59% | -1.74 | -0.91 | 0.73 | +23.84% | -57.65 |
| 7 | ADXMomentum | 1h | -37.04% | -3704.26 | 1984 | 80.7% | 54.79% | -1.81 | -0.74 | 0.90 | +23.36% | -60.40 |
| 8 | Supertrend | 1h | -39.73% | -3972.93 | 1139 | 55.5% | 62.77% | -1.20 | -0.64 | 0.89 | +23.36% | -63.09 |
| 9 | Strategy005 | 5m | -65.89% | -6588.85 | 862 | 81.4% | 70.43% | -2.75 | -1.53 | 0.67 | +23.84% | -89.73 |
| 10 | Strategy001 | 5m | -70.23% | -7022.95 | 1853 | 87.9% | 79.60% | -3.09 | -1.26 | 0.79 | +23.84% | -94.07 |
| 11 | SampleStrategy | 5m | -71.21% | -7121.34 | 1722 | 86.4% | 76.78% | -3.54 | -1.41 | 0.73 | +23.84% | -95.05 |

### What the tables say (read before trusting any of it)

- **Over the full ~2 years, no strategy beat buy-and-hold.** The best, CombinedBinHAndCluc at +20.42%, is
  3.4 pp short of B&H at +23.84%, though with a much smaller drawdown (9.25%). 9 of the 11 strategies lost money.
  SampleStrategy, Strategy001 and Strategy005 lost 66-71%.
- **Over the recent 6 months, only BbandRsi beat buy-and-hold** (+8.77% vs +5.74%), and it got there with an
  18.7% drawdown. Over the full period the same strategy lost 16.18% with a 44.23% drawdown, so its recent edge
  looks regime-dependent, not robust.
- CombinedBinHAndCluc and ClucMay72018 are the only strategies positive in **both** periods, but their recent
  results rest on just **14 and 12 trades**. Statistically that is thin, and their tiny recent drawdowns mostly
  reflect how rarely they were in the market.
- The high win rates (81-95%) of Strategy001-005 come from `exit_profit_only=True` plus a -10% stoploss:
  many small wins and a few large losses. All five have full-period profit factors below 1. In the recent
  window only Strategy003 is above 1, at 1.14.
- These are default community parameters on 5 large-cap pairs with no protections. Treat them as baselines,
  not as tuned strategies.

## Lookahead analysis (top 3 by recent-6-months profit)

Command: `lookahead-analysis --timerange 20241001- --fee 0.001`. Freqtrade automatically forces market orders,
a 10k fixed stake and a 1e9 wallet, and then checks 20 signals per strategy.

| Strategy | Trades found | Signals checked | Biased entry signals | Biased exit signals | Biased indicators | Has bias |
|---|---:|---:|---:|---:|---:|---|
| BbandRsi | 362 | 20 | 0 | 0 | none | **No** |
| CombinedBinHAndCluc | 199 | 20 | 0 | 0 | none | **No** |
| ClucMay72018 | 130 | 20 | 0 | 0 | none | **No** |

No lookahead bias was detected in any of the three. A manual read of the code agrees: only backward
`shift()` and rolling windows are used. Note that the tool checks a 20-signal sample, not every trade.
`recursive-analysis` (startup-candle sensitivity) was **not** run.

## Dry runs

Two strategies were then dry-run on live prices (10,000 USDT dry-run wallet, 5 pairs, max 5 open trades). The
dry runs were stopped after about a day, which is far too short to report, so they are not used as evidence.

Why these two were chosen:

1. **BbandRsi** was #1 on the recent 6 months and the only strategy that beat buy-and-hold there, and it passed
   lookahead analysis. Caveat: it was poor over the full period (-16.18%, 44% drawdown), so the dry run is
   partly a test of whether the recent regime persists.
2. **CombinedBinHAndCluc** was #2 on the recent 6 months and passed lookahead analysis. It is also the best
   full-period strategy and positive in both windows. It was chosen over #3 ClucMay72018 because its entry rule
   is a superset of ClucMay72018's (ClucMay72018 conditions OR BinHV45 conditions), so running both would mostly
   duplicate trades. Caveat: it trades rarely (14 trades in 6 months), so the dry run will need months before it
   says much.

Benchmark use: compare the custom engine against these two dry-run bots **and** against plain buy-and-hold of
the same 5 pairs. Over both windows tested, buy-and-hold was the hardest benchmark.

## Not done / caveats

- No hyperopt (skipped on purpose).
- No `--enable-protections`, no `--timeframe-detail` (intra-candle fills are approximated at 5m/1h resolution).
- Backtests assume fills at candle prices. The dry-run bots use order-book pricing (`price_side: same`,
  limit orders), so dry-run fills and trade timing will differ somewhat from backtests.
- Result tables: `../results/freqtrade/summary.{md,json}` and the lookahead CSVs in `../results/freqtrade/lookahead/`.
  The raw backtest exports (about 100 MB of zip files) are not included; `scripts/run_backtests.sh` recreates them.
