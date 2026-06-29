# paper-trader

An LLM-assisted **paper** trading bot for Alpaca. News in → structured signal → deterministic strategy → paper orders → an honest scoreboard.

This is a learning/measurement instrument. The win condition is **"did I measure a real edge?"** — not "did the number go up this week." The architecture is built specifically so you can't lie to yourself.

## The one idea that matters

The LLM is a **feature extractor**, not the trader. It reads a news item and emits a structured `Label` (tickers, sentiment, confidence, event type). A separate **deterministic** function turns labels into trades. That wall is the whole point:

```
news.fetch ─▶ labeler.label ─▶ strategy.decide ─▶ broker.submit
 (RSS)         (Claude → Label)   (pure, testable)   (Alpaca PAPER)
```

Because labels are a pure function of the news text, you can archive them and replay history through the strategy a thousand times with zero LLM calls and zero randomness. **That's what makes it backtestable.** If the decision-maker were the LLM itself, you could never get a real win rate or max drawdown out of it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in your keys
```

You need two free things:
1. **Alpaca paper keys** — sign up, switch to the *Paper Trading* account, generate API keys. Paper trading is free and global; a paper-only account works.
2. **Anthropic API key** — for the labeler.

## Run

```bash
# 1. Prove the deterministic core works (no keys needed):
pytest -q

# 2. Backtest on the synthetic sample (no keys needed):
python -m trader.backtest data/sample_labels.jsonl 0.015
#    (second arg = benchmark return over the window, e.g. SPY +1.5% = 0.015)

# 3. Run the live paper loop (needs keys):
python -m trader.run
```

The live loop polls news every `POLL_SECONDS`, logs every decision (trade *and* skip) to `data/trades.csv`, and ships every entry as an Alpaca **bracket order** so take-profit / stop-loss are handled broker-side — a crash won't leave a runaway position.

## The honest-measurement protocol

Read this before you ever think about real money.

1. **Paper flatters you.** Alpaca's paper sim won't even check your order against real available liquidity — it fills size that wouldn't exist live, with no real slippage. So the backtest (`SimBroker`) applies a slippage haircut to *every* fill, on purpose. Keep `SLIPPAGE_BPS` ≥ 10. A flat-price round trip should lose money — that's correct.
2. **Beat the benchmark or it's worthless.** The scoreboard reports `vs_benchmark`. If the strategy can't beat buy-and-hold SPY over the same window, it has no reason to exist. Most strategies — including clever ones — lose here.
3. **Run it for weeks, not an afternoon.** A few good days is noise. Track win rate, avg-win-vs-avg-loss, expectancy, and max drawdown over a real window.
4. **The news leg is slow on purpose-of-physics.** RSS polls in minutes; an LLM round-trip adds seconds. You are *not* racing the people who trade headlines in milliseconds off direct feeds. Treat signals as direction over hours/days, or the backtest will (correctly) punish you.
5. **No real money until 1–4 pass.** And even then, size it as tuition, not a wealth machine. No strategy doubles money weekly and compounds — that math owns the market inside a year, which is why it doesn't exist.

## Tuning

All knobs live in `.env` (see `.env.example`) and `trader/config.py`: confidence/sentiment thresholds, notional per trade, take-profit / stop-loss percentages, shorting on/off, the trading universe, slippage, and the RSS feed list.

## Layout

```
trader/
  labels.py      Label model + robust LLM-output parser   (pure)
  strategy.py    deterministic decide() rule               (pure, backtestable)
  simbroker.py   slippage-haircut sim broker               (pure, the honesty layer)
  metrics.py     win rate / expectancy / drawdown / vs-benchmark  (pure)
  news.py        RSS ingestion + dedup
  labeler.py     Claude news → Label
  broker.py      Alpaca paper broker (bracket orders)
  config.py      env-driven config
  run.py         live paper loop
  backtest.py    deterministic replay harness
tests/           26 tests over the pure core
```

Not investment advice. Paper only by default — keep it that way until the numbers earn otherwise.
