# TensorTrade RL trader (`MODE=rl`)

A full reinforcement-learning trader built on
[TensorTrade](https://github.com/tensortrade-org/tensortrade). A DQN agent makes
the **long / flat** call per symbol from a window of price features; the trade it
produces still flows through the platform's existing execution guardrails
(confirmation, policy, safety lock, circuit breaker, sizing). The RL brain
replaces the *decision*, not the risk plumbing.

**On by default in the deployed image.** The Dockerfile bakes in the RL extra
(`INSTALL_RL=1`, ~1 GB of TensorFlow/Keras/gym/pandas), the RL DQN votes in the
confluence brain (`USE_RL_VOICE` + `USE_CONFLUENCE` default true), and the
champion/challenger retrain daemon runs (`RUN_RL_DAEMON=1`). To disable, set those
env vars to `0`/`false` (or build with `--build-arg INSTALL_RL=0`).

It remains a **lazily-loaded** extra: `trader.rl.available()` gates every RL path,
so a local checkout without the heavy install still boots and runs — the RL voice
and daemon simply self-skip. Only the standalone `MODE=rl` brain is opt-in.

> **Resource note:** TensorFlow import + periodic training is memory-hungry. On a
> small host (e.g. Render starter) the daemon can pressure RAM; bump the plan or
> set `RUN_RL_DAEMON=0` if you see OOMs. Model files themselves are tiny (KBs).

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

## Two ways to use it

The RL agent plugs into the platform at **two** levels:

### 1. Standalone brain — `MODE=rl`
The RL policy makes the buy/flat call for `RL_UNIVERSE` symbols (shown above). It
runs *instead of* the news/scalper/daytrader loops.

### 2. Confluence voice — `USE_RL_VOICE=true` (works in every mode)
The RL agent also plugs into the **confluence brain** as a first-class method
(`rl`), sitting alongside `ta`, `quant`, `fundamental`, `ml`, `cortex`, etc. When
enabled, the DQN's long/flat conviction (`tanh(Q_long − Q_flat)`, bounded to
[-1, 1]) becomes one vote in the multi-method agreement gate that `news` and
`daytrader` modes already use. This is the deeper integration: the RL signal
contributes to conviction and position sizing everywhere, not just in `MODE=rl`.

```bash
USE_CONFLUENCE=true      # the confluence gate must be on
USE_RL_VOICE=true        # add the RL voice to the blend
RL_WINDOW=20             # must match how the models were trained
# train models for the symbols you trade, then run any mode (news/daytrader/rl)
```

The voice is **absent** (contributes nothing) whenever the RL extra isn't
installed or there's no trained model for the symbol — so turning it on can only
add information, never break a decision. Regime weights for the `rl` voice live
in `alpha._REGIME_W` (trusted more in trending regimes, trimmed in high-vol), and
its blended score is logged in the confluence `reason` and fed to the `cortex`
fuser and reasoning trace like every other method.

Model status is exposed at **`GET /api/rl`** on the dashboard backend (extra
availability, config, and per-symbol trained models — read from metadata without
loading TensorFlow).

## Keeping models fresh — the retrain daemon

Like the NumPy ML model (`trader.ml.daemon`), the RL models can retrain on a
cadence with a **champion/challenger gate** so the live model only ever improves:

```bash
python -m trader.rl.daemon --every 12 --episodes 12
```

Each cycle, per symbol in the RL universe:

1. Recent history is split into a training slice and a **held-out tail**.
2. The incumbent (champion) is backtested on the held-out tail.
3. A challenger is trained on the training slice **only**, then backtested on the
   *same* held-out tail.
4. The challenger is promoted **only if it beats the champion out-of-sample**
   (or there's no champion yet). Otherwise the incumbent is left untouched.

The held-out evaluation is the honesty layer — a challenger can't win by
memorising the bars it's scored on. Promotion is an atomic file swap; a failed or
worse challenger never touches the live model.

Enable it in the container with `RUN_RL_DAEMON=1` (off by default, since it needs
the heavy RL extra):

```bash
RUN_RL_DAEMON=1
RL_RETRAIN_EVERY_H=12
RL_RETRAIN_EPISODES=12
```

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
