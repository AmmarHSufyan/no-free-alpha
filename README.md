# No Free Alpha

**Is trading with AI, bots or copy trading profitable for retail crypto investors in 2026? We tested it with
realistic fees and found no strategy that beat simply holding Bitcoin.**

This repository holds a research paper and all of its code. It is an end-to-end, cost-aware evaluation of:

- technical-indicator trading bots;
- Freqtrade community strategies;
- an LLM (Claude) trading agent;
- copy trading of on-chain "smart money" on Hyperliquid, selected four ways, including a statistical skill finder;
- a slow daily trend-following rotation.

Every strategy was backtested out of sample with exchange fees and slippage, and compared against buy-and-hold.

📄 **Read the paper:** [web version](https://ammarhsufyan.github.io/no-free-alpha/) ·
[Markdown](paper/no-free-alpha.md) · [PDF](docs/no-free-alpha.pdf)

![Copying Hyperliquid wallets chosen by four finders](paper/figures/fig2_copy_walkforward.png)

## Key findings

| Strategy family | Best result after costs | Benchmark | Verdict |
|---|---|---|---|
| 5 indicator bots, 1-hour candles (2024–2026) | Donchian +27.9% (MACD: +79% before costs, −49% after) | Buy-and-hold +23.0% | None beat holding out of sample |
| 11 Freqtrade community strategies | +20.4%; 9 of 11 lost money | Buy-and-hold +23.8% | None beat holding |
| LLM trading agent (Claude, pilot) | 41 of 42 decisions were "don't trade" | – | No evidence of an edge; public AI contests mostly lost money |
| Copy trading, 1.5M Hyperliquid wallets, 12-month walk-forward | −10.5% to −30.2% for three finders; +10.9% for one, from a single wallet cluster | Random wallets −19.1%, BTC −26.2% | Loses; past copy profit does not predict the next week (rank correlation 0.04) |
| Daily trend rotation, top 30 coins incl. dead coins (2020–2026) | 38.0% a year, worst drop −54% | BTC 42.4% a year, worst drop −77% | Smaller crashes, lower returns |

Why it fails:

1. **Fees and slippage eat frequent trading.**
2. **On-chain leaderboards mostly measure luck and risk-taking.**
3. **The skill that does persist (patient limit orders, speed) cannot be copied.** A copier is seconds late and
   pays the spread.
4. **A smarter model doesn't change those constraints.**

## What's in the repository

| Path | Content |
|---|---|
| [`paper/`](paper/) | The paper, its figures, a [plain-English summary](paper/medium-article.md), `make_figures.py` (rebuilds every figure and table) and `build_site.py` (web page and PDF) |
| [`results/`](results/) | The result files behind every number in the paper (see [`results/README.md`](results/README.md)) |
| [`bot/`](bot/) | Our own backtesting engine: next-candle fills, fee and slippage model, lookahead check, optimiser, paper trader, Claude CLI strategy, trend rotation |
| [`bot/copylab/`](bot/copylab/) | Copy-trading lab: Hyperliquid API client, finders (hot, steady, edge, skill), copier simulator, statistics (block bootstrap, Benjamini–Hochberg FDR, empirical-Bayes shrinkage), walk-forward and full-ledger pipelines |
| [`bot/copylab/bulk/`](bot/copylab/bulk/) | Cloud job that reduces Hyperliquid's 322 GB public fill ledger (`s3://hl-mainnet-node-data`) to wallet-day totals |
| [`freqtrade/`](freqtrade/) | Freqtrade benchmark setup (dry-run only) and its results |
| [`tests/`](tests/) | 24 unit tests for the engine, copier and statistics |

## Reproduce

Requires Python 3.11 or newer.

```bash
python -m venv .venv && . .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,figures]"                     # add ",bulk" for the Hyperliquid ledger job
python -m pytest                                    # 24 tests

python -m bot download                              # 1-hour candles for the indicator study
python -m bot backtest                              # indicator strategies vs buy-and-hold, gross and net
python -m bot fees                                  # the fee profiles and their sources
python -m bot.trend                                 # daily candles, including delisted coins
python paper/make_figures.py                        # rebuild every figure and derived table
```

Market data is public and downloaded by the code; it is not stored in the repository. The copy-trading results
need the Hyperliquid ledger. It is public in a requester-pays bucket, and processing it cost about USD 1.20 (see
the docstrings in `bot/copylab/bulk/`). The LLM strategy needs the
[Claude Code CLI](https://docs.claude.com/en/docs/claude-code); each decision is one request, and answers are
cached.

**Safety:** nothing here places real orders. There are no exchange keys anywhere; Freqtrade runs with
`dry_run: true`, and the LLM gets no tools.

## Cite

```bibtex
@techreport{nofreealpha2026,
  title  = {No Free Alpha: Transaction Costs, Luck, and the Limits of Algorithmic, LLM-Driven and Copy Trading for Retail Crypto Investors},
  author = {AmmarHSufyan},
  year   = {2026},
  month  = {10},
  url    = {https://github.com/AmmarHSufyan/no-free-alpha}
}
```

See also [`CITATION.cff`](CITATION.cff).

## Disclaimer

This is research, not investment advice. Past results, including the negative ones here, do not predict future
results.

## License

Code is released under the [MIT](LICENSE) license. The Freqtrade community strategies are GPL-3.0 and are fetched
from their own repository, not included here.

---

*Keywords: AI trading, LLM trading agent, ChatGPT and Claude trading bots, crypto trading bot backtest,
copy trading, social trading, Hyperliquid smart money, on-chain wallet analysis, whale tracking, leaderboard
traders, skill vs luck, false discovery rate, walk-forward backtest, transaction costs, trading fees, Freqtrade
strategies, trend following, Bitcoin buy and hold, is algorithmic trading profitable, retail trader losses.*
