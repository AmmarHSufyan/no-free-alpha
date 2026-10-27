# I tested AI trading bots, copy trading and 1.5 million crypto wallets. Nothing beat holding Bitcoin.

*A plain-English summary of the research paper "No Free Alpha". The full paper, code and data are at
https://github.com/AmmarHSufyan/no-free-alpha*

---

Every week there's a new promise: an AI agent that trades crypto for you, a bot with a 90% win rate, or a
"smart money" wallet you can copy. I wanted to know whether any of it actually works for a normal person once you
pay real exchange fees. So I tested it, with fake money and years of real market data, using the same honest
rules for every idea:

- The strategy only sees the past. A signal on one candle is traded at the next candle's price.
- Every trade pays the exchange fee plus a little slippage, about 0.3% for a round trip on Binance.
- Every result is compared with the simplest alternative: buying and holding.

## 1. Classic trading bots: fees eat everything

I tested five well-known indicator strategies (MACD, moving-average crossovers, RSI, Bollinger Bands and Donchian
breakouts) on hourly candles of five big coins over two years.

Before fees, two of them looked brilliant: MACD made +79%. After fees, MACD **lost 49%**. It made 2,168 trades,
and each one paid the exchange. On the most recent six months, none of the five beat simply holding the coins.

I also ran 11 popular community strategies for Freqtrade, a well-known open-source trading bot. Nine of the 11
lost money over two years, and none beat holding.

## 2. AI trading agents: the smart move was to do nothing

I gave Claude (an AI model) anonymised price charts and asked it to decide, every four hours, whether to hold
Bitcoin or cash. I told it what trading costs. Out of 42 decisions it chose "don't trade" 41 times.

That's too small a test to prove anything on its own, but it matches public contests. In 2025's "Alpha Arena",
four of six top AI models lost real money trading crypto, and across eleven public AI trading contests the
typical model made money in only two.

## 3. Copy trading: the leaderboard is mostly luck

Hyperliquid, a crypto futures exchange, publishes every trade by every wallet. So I downloaded all of it: 457 days
and **1.5 million wallets**. Then I tested four ways of picking wallets to copy, month by month, always using
only the past to pick and the future to judge:

- **Hot:** the best performers of the last 7 days (what leaderboards show).
- **Steady:** wallets that made money consistently for three months.
- **Edge:** wallets with the best profit per dollar traded.
- **Skill:** statistical tests borrowed from fund research, designed to separate skill from luck.

I copied each group with a 10-second delay and all fees, for 12 months:

| How I picked wallets | Copying them for 12 months |
|---|---|
| Hot (leaderboard) | −23% |
| Edge | −10.5% |
| Skill (statistical tests) | −30% |
| Steady | +10.9% |
| Random wallets | −19% |
| Just holding Bitcoin | −26% |

The one winner, "steady", made its money from a single group of linked wallets trading one token. When I applied
the same ranking to a fresh set of wallets, it lost 37%.

The skill tests were honest in their own way: they almost never found anyone to certify. A few months of
trading simply isn't enough to tell a skilled trader from a lucky one.

## 4. "Copy last week's winners" and "drop them when they lose" don't work either

Two popular ideas: a good trader stays hot for a week or two, so copy last week's winners and swap them weekly;
or keep a trader while they make you money and replace them when they start losing.

I tested both, and every variant lost money. Last week's top wallets beat random wallets 51% of the time, a coin
flip. Across 33,927 wallet-weeks, how well a wallet did over the past two months had almost no relationship with
how it did the next week (correlation: 0.04).

The most surprising result: wallets that won in more than 75% of their past weeks had the **worst** next week.
Their many small wins hid occasional big losses.

## 5. The one thing that half-worked: a slow trend follower

A slow strategy that holds coins only while they're trending up, sizes them by risk, and trades daily did one
useful thing. Its worst loss was about −54%, against −77% for holding Bitcoin. But over 2020–2026 it grew 9x,
while Bitcoin grew 11x. It's a seatbelt, not an engine.

## Why none of it works

1. **Fees are the real opponent.** At 0.3% per round trip, a bot that trades 300 times a year has to find 90% a
   year of edge just to break even.
2. **Futures trading is close to zero-sum before fees, and negative after.** One big study of Hyperliquid found
   manual traders lost $1.11 billion, $725 million of it in fees.
3. **The skill that does exist can't be copied.** Profitable wallets mostly earn by placing patient limit
   orders or by acting within seconds. Copying them seconds late, paying the spread, destroys exactly that edge.
   I measured it: the "steady" leaders made +75% themselves; my copy kept +11%.
4. **A smarter AI doesn't change the maths.** It still faces the same costs, the same zero-sum market and the
   same luck in short track records.

## What I'd tell a friend

- Judge any bot, signal group or copy-trading leader **after fees**, on data it wasn't tuned on, against simply
  holding.
- A high win rate means almost nothing. A hot week means nothing.
- If you use a strategy at all, its realistic job is cutting crashes, not beating the market.

The code, the data and the full paper (with all the statistics) are free on GitHub:
**https://github.com/AmmarHSufyan/no-free-alpha**

*This is research, not financial advice.*

*Tags: AI Trading, Cryptocurrency, Trading Bots, Copy Trading, Bitcoin*
