# TensorTrade RL trader (`MODE=rl`)

A full reinforcement-learning trader built on
[TensorTrade](https://github.com/tensortrade-org/tensortrade). A DQN agent makes
the **long / flat** call per symbol from a window of price features; the trade it
produces still flows through the platform's existing execution guardrails
(confirmation, policy, safety lock, circuit breaker, sizing). The RL brain
replaces the *decision*, not the risk plumbing.

This is an **optional extra** — it is not part of the lean core and pulls in
~1 GB of TensorFlow/Keras/gym/pandas. The core app boots and deploys fine
without it; `trader.rl.available()` gates every RL path.

## Why the install is unusual

`tensortrade==1.0.3` is the only release on PyPI (from 2021). Two realities shape
this integration:

1. **Its `setup.py` fails under modern setuptools' build isolation.** You must
   install with `--no-build-isolation`.
2. **Its bundled `DQNAgent` is dead on Keras 3** (`Adam(lr=...)` was removed). So
   we ship our own Keras-3-correct DQN in `trader/rl/agent.py` and only use
   TensorTrade for the *environment* (OMS, exchange, portfolio, feed, schemes).

The TensorTrade `TradingEnv` itself works well on the current stack — the
integration is built and tested against it.

## Install

```bash
pip install "setuptools<66" wheel
pip install --no-build-isolation -r requirements-rl.txt
```

In Docker, build with the opt-in arg:

```bash
docker build --build-arg INSTALL_RL=1 -t paper-trader:rl .
```

## Train a model per symbol

The agent is trained per symbol and persisted to `data/rl/<SYMBOL>.keras`
(+ a `.meta.json` sidecar). Train before running `MODE=rl`.

```bash
# from recent Alpaca history (needs ALPACA_* keys)
python -m trader.rl.cli train AAPL MSFT NVDA --episodes 12 --lookback 400

# from a local close-price file (no keys) — one price per line or comma-separated
python -m trader.rl.cli train DEMO --closes data/demo_closes.txt --episodes 8 --window 10
```

Each `train` run prints an honest scoreboard: **agent return vs buy-and-hold**
over the window, with a slippage commission (`SLIPPAGE_BPS`) charged on every
fill. If the agent can't beat buy-and-hold, the policy has no edge — the same bar
the rest of this platform holds itself to. Expect to need many episodes and real
history before that happens; a couple of episodes on a short series will (rightly)
lose.

## Backtest a saved model

```bash
python -m trader.rl.cli backtest AAPL --lookback 400
```

## Run it live (paper)

```bash
MODE=rl
RL_UNIVERSE=AAPL,MSFT,NVDA     # symbols scanned each cycle
RL_WINDOW=20                   # observation lookback (bars); must match training
# RL_MODEL_DIR=                # empty -> data/rl
```

Then `python -m trader.run`. Each cycle, for every symbol in `RL_UNIVERSE` that
isn't already held, the loop pulls recent closes, asks the DQN for the greedy
BSH action, and — when the agent wants to be long — emits a buy `Intent` that is
confirmed, sized, and routed exactly like every other entry. Symbols without a
trained model are skipped and logged.

## How it works

```
recent closes ─▶ features.py (pure NumPy)  ─▶ env.py (TensorTrade TradingEnv)
   (MarketData)   lret / SMA dist / vol /       Exchange+commission(=slippage),
                  RSI / momentum                  Portfolio, BSH action, SimpleProfit
                          │                               │
                          ▼                               ▼
                  agent.py DQN  ◀── train ───────  gym rollout (episodes)
                          │
                   greedy action (0=flat, 1=long)
                          │
                  trader.py decide() ─▶ Intent ─▶ run.py guardrails ─▶ AlpacaBroker (paper)
```

- **`features.py`** — pure-NumPy, zero heavy deps, unit-tested without TF. The
  live path and the training env build observations with the *same* function, so
  inputs are identical. All features are causal (no lookahead).
- **`env.py`** — builds the `TradingEnv`. Every fill pays a commission equal to
  `SLIPPAGE_BPS`, mirroring `SimBroker`'s honesty haircut, so the agent trains
  against costs it will actually pay. Features are pre-computed NumPy columns fed
  as plain streams (TensorTrade's lazy `.diff()/.fillna()` emit `None` on the
  first tick under gym-0.26/pandas-3, which crashes the feed).
- **`agent.py`** — a compact DQN (Dense layers, experience replay, target net,
  epsilon-greedy) on the current Keras API. TensorFlow is imported lazily.
- **`trader.py`** — `RLTrader.train / backtest / decide`. `decide()` only opens
  positions (exits are broker-side brackets), so it returns a buy `Intent` when
  the policy wants long and we're flat, else `None`.

## Limitations

- **Long / flat only.** BSH has no short leg, matching `ALLOW_SHORT=false`. A
  short-capable scheme is a future extension.
- **Undertrained models lose.** DQN needs many episodes and real history; the
  scoreboard is deliberately honest about it.
- **Keep it paper.** Same rule as the rest of the platform — no real money until
  the measured edge earns it.
```
