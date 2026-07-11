"""RLTrader: train / persist / serve a TensorTrade DQN as a full RL trader.

This is the object the live loop and CLI talk to. Responsibilities:
  * train(closes)      -> build env, train the DQN, remember obs shape
  * backtest(closes)   -> greedy rollout, report agent vs buy-and-hold
  * decide(symbol,..)  -> greedy action on the latest window -> an `Intent`

The RL policy makes the buy/flat CALL (this is the "full RL trader" the user
asked for), but the resulting `Intent` is still routed through the platform's
existing execution guardrails (policy, safety lock, circuit breaker, sizing) in
run.py -- an RL brain replaces the *decision*, not the risk plumbing.

BSH gives a 2-action space: 0 = flat/cash, 1 = long/asset. In the live loop we
only open positions (exits are broker-side brackets), so decide() emits a buy
Intent when the agent wants to be long and we're flat, and None otherwise.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass

from .env import EnvConfig, build_env, buy_and_hold_return
from .features import latest_window, FEATURE_NAMES

DEFAULT_MODEL_DIR = os.environ.get(
    "RL_MODEL_DIR",
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "rl")),
)


def model_path(symbol: str, model_dir: str | None = None) -> str:
    d = model_dir or DEFAULT_MODEL_DIR
    safe = symbol.replace("/", "_").upper()
    return os.path.join(d, safe)


_VOICE: "RLTrader | None" = None


def score_from_closes(symbol: str, closes, window: int = 20, model_dir: str | None = None):
    """Confluence-voice entry point: bounded RL conviction in [-1, 1], or None.

    Uses a process-wide cached `RLTrader` (models load once, then stay warm) so
    this is cheap to call in the hot decision path. Returns None whenever the RL
    extra isn't installed or there's no trained model for `symbol` -- in which
    case the voice is simply absent from the blend, changing nothing.
    """
    global _VOICE
    from . import available
    if not available():
        return None
    if _VOICE is None or _VOICE.window != int(window) or (model_dir and _VOICE.model_dir != model_dir):
        _VOICE = RLTrader(window=int(window), model_dir=model_dir or None)
    try:
        return _VOICE.score_signal(symbol, closes)
    except Exception:  # noqa: BLE001
        return None


@dataclass
class RLResult:
    symbol: str
    agent_return: float
    benchmark_return: float
    n_steps: int
    final_net_worth: float

    @property
    def beat_benchmark(self) -> bool:
        return self.agent_return > self.benchmark_return


class RLTrader:
    def __init__(self, window: int = 20, slippage_bps: float = 10.0,
                 model_dir: str | None = None):
        self.window = int(window)
        self.slippage_bps = float(slippage_bps)
        self.model_dir = model_dir or DEFAULT_MODEL_DIR
        self._agents: dict[str, object] = {}   # symbol -> DQNAgent (cache)

    def _env_cfg(self) -> EnvConfig:
        return EnvConfig(window_size=self.window, slippage_bps=self.slippage_bps)

    # ---- training ------------------------------------------------------- #
    def train(self, symbol: str, closes, episodes: int = 6, **train_kw):
        """Train and persist a DQN for `symbol` from a close-price series."""
        from .agent import DQNAgent
        env = build_env(closes, self._env_cfg())
        obs_shape = env.observation_space.shape
        n_actions = env.action_space.n
        agent = DQNAgent(obs_shape, n_actions)
        agent.meta["symbol"] = symbol.upper()
        agent.meta["window"] = self.window
        agent.train(env, episodes=episodes, **train_kw)
        path = model_path(symbol, self.model_dir)
        agent.save(path)
        self._agents[symbol.upper()] = agent
        return path

    # ---- evaluation ----------------------------------------------------- #
    def backtest(self, symbol: str, closes) -> RLResult:
        """Greedy rollout of the saved agent; agent return vs buy-and-hold."""
        agent = self._get_agent(symbol)
        env = build_env(closes, self._env_cfg())
        from .agent import _unpack_step, _unpack_reset
        obs = _unpack_reset(env.reset())
        start = float(env._rl_portfolio.net_worth)
        steps = 0
        while True:
            a = agent.act(obs, epsilon=0.0) if agent is not None else 0
            obs, _, done, _ = _unpack_step(env.step(a))
            steps += 1
            if done:
                break
        final = float(env._rl_portfolio.net_worth)
        return RLResult(
            symbol=symbol.upper(),
            agent_return=final / start - 1.0 if start else 0.0,
            benchmark_return=buy_and_hold_return(closes),
            n_steps=steps,
            final_net_worth=final,
        )

    # ---- serving -------------------------------------------------------- #
    def _get_agent(self, symbol: str):
        key = symbol.upper()
        if key in self._agents:
            return self._agents[key]
        from .agent import DQNAgent
        agent = DQNAgent.load(model_path(symbol, self.model_dir))
        self._agents[key] = agent
        return agent

    def target_position(self, symbol: str, closes) -> int | None:
        """Greedy BSH action for the latest window: 1 = long, 0 = flat, None = no model."""
        agent = self._get_agent(symbol)
        if agent is None:
            return None
        obs = latest_window(closes, self.window)
        return agent.act(obs, epsilon=0.0)

    def score_signal(self, symbol: str, closes) -> float | None:
        """Bounded conviction in [-1, 1] for the confluence brain, or None.

        Derived from the DQN's Q-gap between the long and flat actions on the
        latest window: tanh(Q_long - Q_flat). Positive => the policy prefers being
        long. None when there's no trained model (voice simply absent). The
        observation window is read from the model's own metadata so it always
        matches how the model was trained, regardless of caller config.
        """
        agent = self._get_agent(symbol)
        if agent is None:
            return None
        try:
            window = int(getattr(agent, "meta", {}).get("window", self.window))
            obs = latest_window(closes, window)
            q = agent.q_values(obs)
            if q is None or len(q) < 2:
                return None
            return max(-1.0, min(1.0, math.tanh(float(q[1] - q[0]))))
        except Exception:  # noqa: BLE001
            return None

    def decide(self, symbol: str, closes, cfg, open_symbols=None):
        """Return a buy `Intent` when the RL policy wants to be long and we're flat.

        `cfg` is the app StrategyConfig (for notional / TP / SL). Returns None when
        there is no trained model, the agent wants flat, or we already hold it.
        """
        from ..strategy import Intent
        open_symbols = open_symbols or set()
        if symbol.upper() in {s.upper() for s in open_symbols}:
            return None
        pos = self.target_position(symbol, closes)
        if pos != 1:
            return None
        return Intent(
            symbol=symbol.upper(),
            side="buy",
            notional=cfg.notional_per_trade,
            take_profit_pct=cfg.take_profit_pct,
            stop_loss_pct=cfg.stop_loss_pct,
            reason="rl: DQN wants long (BSH=1)",
        )
