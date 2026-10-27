# Result files

Every number in the paper comes from a file in this folder. `paper/make_figures.py` rebuilds the figures and
the derived tables marked *derived* below.

Hyperliquid wallet addresses are replaced by neutral IDs (`w00001`, ...). The study is about methods, and it is
not a list of wallets to copy.

## `engine/` - indicator strategies (Section 5.1)

| File | Content |
|---|---|
| `backtest_1h_full_2024-10_2026-10.csv` | 5 strategies + buy-and-hold, 5 pairs, 1-hour candles, Binance spot costs (0.10% fee + 0.05% slippage per fill): gross, fees, slippage, net, CAGR, drawdown, Sharpe, trades |
| `backtest_1h_in_sample_2024-10_2026-04.csv` | Same, in-sample period |
| `backtest_1h_out_of_sample_2026-04_2026-10.csv` | Same, out-of-sample period |

## `freqtrade/` - community strategies (Section 5.2)

`summary.md` / `summary.json`: Freqtrade backtest tables for both periods. `lookahead/`: Freqtrade
lookahead-analysis results for the top three strategies.

## `ai/` - LLM agent pilot (Section 5.3)

| File | Content |
|---|---|
| `backtest_claude_4h_btc_2026-10-19_2026-10-26.csv` | Claude agent vs buy-and-hold, one week of 4-hour BTC candles |
| `claude_decisions.jsonl` | All 42 decisions: stance, confidence, the model's one-sentence reason, API-equivalent cost |

## `copylab/` - copy trading (Section 5.4)

| File | Content |
|---|---|
| `walkforward_12m.csv` | Monthly returns of every finder and variant (leader raw, copy 10 s, copy + 25% stop) and of 100 random placebo sets a month, 2025-10-25 to 2026-10-20, with BTC's return |
| `walkforward_12m_summary.csv` | *Derived:* 12-month totals, months up, share of random sets beaten (Table 3) |
| `quick_screen_15m.csv` | The optimistic per-dollar screen on all wallets (Section 5.4.2) |
| `cost_sensitivity_steady.csv` | Steady finder with no fees, with maker fees, with taker fees and slippage |
| `waiting_orders_steady.csv` | Steady finder copied with realistic limit-order fills (chase or skip) |
| `steady_cost_ladder.csv` | *Derived:* Figure 3 |
| `steady_contributions.csv` | Every steady leader-month's copy return and main coin (Section 5.4.5) |
| `steady_ranking_in_pool.csv` | Steady ranking applied inside the independent placebo pool |
| `hot_streak_weekly.csv` | Last week's top 10 vs 20 random sets per week, copy returns in weeks 1-4 after picking |
| `hot_streak_summary.csv` | *Derived:* Figure 5 |
| `weekly_copy_returns.parquet` | Our 1-week copy return for 3,283 wallets x 52 weeks (51,201 replays) |
| `weekly_btc.json` | BTC's weekly return for the same weeks |
| `rotation_rules.csv` | Adaptive keep/drop and recurring-winner rules over 52 weeks (Table 4) |
| `persistence_deciles.csv`, `persistence_win_share.csv` | *Derived:* Figure 4 |

## `trend/` - daily trend rotation (Section 5.5)

`trend_variants.csv` (*derived*): CAGR, max drawdown, Sharpe, trades per coin per year and average invested
share for top 10/20/30, daily and weekly, over three periods, plus the hold-BTC and hold-top-30 benchmarks.
